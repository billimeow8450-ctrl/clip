"""Offline sticky camera paths: hold still, then correct with no tracking lag."""
from functools import lru_cache
import math
from statistics import median
from pathlib import Path
import xml.etree.ElementTree as ET

from .composition import placement,subject_face,tracking_envelope


@lru_cache(maxsize=2)
def profile_values(name):
    if name!="xml_reference":
        # Fast enough to arrive at the first boundary-crossing sample while the
        # smoothstep still hides a mechanical snap.  Inside the lane there is
        # no motion at all.
        return .22,2000.0
    root=ET.parse(Path(__file__).parent/"assets/master_motion.xml").getroot()
    row=root.find("profile")
    return max(.2,min(.45,float(row.attrib["smoothing-seconds"]))),max(300,min(1400,float(row.attrib["max-pan-pixels-per-second"])))


def union(boxes):
    x=min(b[0] for b in boxes); y=min(b[1] for b in boxes)
    return x,y,max(b[0]+b[2] for b in boxes)-x,max(b[1]+b[3] for b in boxes)-y


def _constrain(value,lo,hi,canvas,scaled):
    # Prefer completely filled panels when this also preserves the whole head.
    if scaled>=canvas and max(lo,canvas-scaled)<=min(hi,0):
        lo=max(lo,canvas-scaled); hi=min(hi,0)
    return min(hi,max(lo,value))


def _sticky_axis(times, bounds, initial, speed, smoothing):
    """Hold an axis until the protected subject reaches its safe boundary.

    The path is computed offline.  A correction is placed between the last
    in-bounds observation and the first boundary crossing, so FFmpeg starts it
    immediately instead of reacting one or more samples late.  A small landing
    guard prevents left/right chatter after the correction.
    """
    low, high = max(b[0] for b in bounds), min(b[1] for b in bounds)
    if low<=high:
        return [min(high,max(low,initial))]*len(times)
    if any(lo>hi for lo,hi in bounds):
        return None
    points = [min(bounds[0][1],max(bounds[0][0],initial))]
    for i in range(1,len(times)):
        lo,hi=bounds[i]
        current=points[-1]
        if lo<=current<=hi:
            points.append(current)
            continue
        # Land just inside the safe interval.  This is a one-time correction,
        # not continuous centring, so normal seated movement leaves the frame
        # completely frozen.
        guard=min(max(3.0,smoothing*32.0),max(3.0,(hi-lo)*.18))
        inner_lo=min(hi,lo+guard)
        inner_hi=max(lo,hi-guard)
        target=inner_lo if current<lo else inner_hi
        # Smoothstep peaks at 1.5x its average speed.
        travel = speed*max(.001,times[i]-times[i-1])/1.5
        if abs(target-current)>travel+.001:
            return None
        points.append(target)
    return points

@lru_cache(maxsize=512)
def camera_path(media,panel,layout):
    smoothing,max_speed = profile_values(layout.motion_profile)
    face = subject_face(layout,panel)
    observed = {f.time:subject_face(layout,panel,f.boxes) for f in layout.face_keyframes}
    raw = sorted((t,b) for t,b in observed.items() if b is not None)
    if face is None or len(raw)<2:
        w,h,x,y = placement(media,panel,face)
        return w,h,((layout.source_start,float(x),float(y)),),False
    largest = (median(b[0] for _,b in raw),median(b[1] for _,b in raw),
               max(b[2] for _,b in raw),max(b[3] for _,b in raw))
    width,height,_,_ = placement(media,panel,largest)
    times = [t for t,_ in raw]
    sx,sy,sw,sh = panel.safe
    for attempt in range(8):
        axes = []
        for index,scaled,canvas,origin,span in ((0,width,panel.width,sx,sw),(1,height,panel.height,sy,sh)):
            bounds = []
            for _,box in raw:
                b = tracking_envelope(panel,box)
                lo,hi = origin-b[index]*scaled,origin+span-(b[index]+b[index+2])*scaled
                if scaled>=canvas and max(lo,canvas-scaled)<=min(hi,0):
                    lo,hi = max(lo,canvas-scaled),min(hi,0)
                bounds.append((lo,hi))
            if any(lo>hi for lo,hi in bounds):
                break
            b = tracking_envelope(panel,raw[0][1])
            initial = origin+span/2-(b[index]+b[index+2]/2)*scaled
            values = _sticky_axis(times,bounds,initial,max_speed/math.sqrt(2),smoothing)
            if values is None:
                break
            axes.append(values)
        if len(axes)==2:
            points = tuple((t,round(x,4),round(y,4)) for t,x,y in zip(times,*axes))
            if all(p[1:]==points[0][1:] for p in points):
                points = ((layout.source_start,*points[0][1:]),)
            return width,height,points,False
        width = max(2,int(width*.94)//2*2)
        height = max(2,int(height*.94)//2*2)
    # If a very fast move cannot satisfy the bounded smooth path, show the
    # complete sharp source in this panel rather than lagging behind the body.
    w,h,x,y = placement(media,panel,None)
    return w,h,((layout.source_start,float(x),float(y)),),True


def value_at(points,time,index):
    if time<=points[0][0]:
        return points[0][index]
    for a,b in zip(points,points[1:]):
        if time<=b[0]:
            u=(time-a[0])/max(.000001,b[0]-a[0])
            return a[index]+(b[index]-a[index])*u
    return points[-1][index]


def expression(points,index,source_time,offset=0,source_end=None):
    if source_end is not None:
        points=((source_time,value_at(points,source_time,1),value_at(points,source_time,2)),)+tuple(p for p in points if source_time<p[0]<source_end)+((source_end,value_at(points,source_end,1),value_at(points,source_end,2)),)
    if len(points)<2 or max(p[index] for p in points)-min(p[index] for p in points)<.15:
        return f"{points[0][index]+offset:.3f}"
    clock=f"(t+{source_time:.6f})"
    # Balanced summation avoids deeply nested FFmpeg expressions on long shots.
    # Smoothstep provides an eased but zero-delay move between the last safe
    # observation and the first boundary crossing.
    pieces=[]
    for a,b in zip(points,points[1:]):
        delta=b[index]-a[index]
        if abs(delta)<.001:
            continue
        u=f"clip(({clock}-{a[0]:.6f})/{max(.000001,b[0]-a[0]):.6f},0,1)"
        pieces.append(f"({delta:.6f}*({u})*({u})*(3-2*({u})))")
    def balanced(parts):
        if not parts:
            return "0"
        if len(parts)==1:
            return parts[0]
        middle=len(parts)//2
        return "("+balanced(parts[:middle])+"+"+balanced(parts[middle:])+")"
    return f"({points[0][index]+offset:.3f}+{balanced(pieces)})"


def validate_motion(media,layout):
    from .composition import layout_geometry,H
    errors=[]
    geo=layout_geometry(layout,media)
    for n,panel in enumerate(geo.panels):
        if panel.face_index==-1:
            continue
        width,height,points,full_source_fallback=camera_path(media,panel,layout)
        for frame in layout.face_keyframes:
            box=subject_face(layout,panel,frame.boxes)
            if box is None:
                continue
            bx,by,bw,bh=tracking_envelope(panel,box)
            x=value_at(points,frame.time,1)+bx*width
            y=value_at(points,frame.time,2)+by*height
            w,h=bw*width,bh*height
            sx,sy,sw,sh=(0,0,panel.width,panel.height) if full_source_fallback else panel.safe
            if x<sx-2 or y<sy-2 or x+w>sx+sw+2 or y+h>sy+sh+2:
                errors.append("tracked head leaves its safe lane")
            if geo.diagonal:
                for px,py in ((x,y),(x+w,y),(x,y+h),(x+w,y+h)):
                    line=.62*H-.20*px
                    if (n==0 and py>line-12) or (n==1 and py<line+12):
                        errors.append("tracked head intersects diagonal")
    return list(dict.fromkeys(errors))
