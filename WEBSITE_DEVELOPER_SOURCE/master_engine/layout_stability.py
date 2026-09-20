"""Local, source-preserving repairs for unstable participant populations.

Changing head count must never make the editor fail its own hold-time rules.
During uncertainty keep all observed people in a shared view, then resume the
normal portrait/split/grid director. Never merge across a native camera cut.
"""
from dataclasses import replace

from .models import LayoutSegment
from .subjects import eligible, four_panel_members


def _union(boxes):
    x=min(b[0] for b in boxes); y=min(b[1] for b in boxes)
    return x,y,max(b[0]+b[2] for b in boxes)-x,max(b[1]+b[3] for b in boxes)-y


def _context(start,end,samples,native=False):
    window=[row for row in samples if start<=row.time<end]
    tracked={}
    counts=[]
    for sample in window:
        valid=[face for face in sample.faces if eligible(face)]
        counts.append(len(valid))
        for slot,face in enumerate(sorted(valid,key=lambda f:f.cx)):
            key=face.track_id if face.track_id>=0 else -10-slot
            tracked.setdefault(key,[]).append((face.x,face.y,face.w,face.h))
    boxes=tuple(sorted((_union(values) for values in tracked.values()),key=lambda b:b[0]+b[2]/2))
    minimum=min(counts,default=0)
    duration=end-start
    if minimum>=2 and len(boxes)>=2 and duration<=2.0+.001:
        mode='fit_blur'
        members=()
    elif len(boxes)>4:
        mode='grid4_context'
        members=four_panel_members(boxes)
    elif len(boxes)==4:
        mode='grid4'
        members=()
    elif len(boxes)==3:
        mode='grid3'
        members=()
    elif minimum>=2 and len(boxes)>=2:
        mode='duo_context'
        members=()
    else:
        # During uncertain detection keep the complete sharp source.  Blur is
        # reserved for the exact verified short multi-speaker rule above.
        mode='passthrough'
        boxes=()
        minimum=0
        members=()
    # The static envelope contains EVERY observed position, including entrants
    # that are visible for less than the normal subject-confidence window.
    return LayoutSegment(start,end,mode,required_faces=minimum,
        face_boxes=boxes,face_centers=tuple((x+w/2,y+h/2) for x,y,w,h in boxes),
        native_cut=native,panel_face_indexes=members,
        turn_seconds=round(duration,3),
        reason='temporary participant uncertainty; stable duration-safe reference frame')


def _violation(rows,hold):
    if not rows:
        return None
    since=rows[0].source_start
    for previous,row in zip(rows,rows[1:]):
        if row.native_cut:
            since=row.source_start
            continue
        remap=(row.mode==previous.mode=='grid4_context' and
               (row.required_faces!=previous.required_faces or row.panel_face_indexes!=previous.panel_face_indexes))
        if row.mode==previous.mode and not remap:
            continue
        held=row.source_start-since
        drop=row.required_faces<previous.required_faces
        upgrade=row.required_faces>=2 and row.required_faces>previous.required_faces and held>=.90
        short_blur=previous.mode=='fit_blur' and held>=.50
        enter_short_blur=(row.mode=='fit_blur' and row.source_end-row.source_start<=2.0+.001
                          and row.source_end-row.source_start>=.50)
        turn=previous.mode in {'speaker_context','duo_context'} and held>=.64 and row.turn_seconds>3
        if held<hold-.10 and (remap or not (drop or upgrade or turn or short_blur or enter_short_blur)):
            return since,row.source_start
        since=row.source_start
    # Ignore cuts already present in the source. Bound only new editorial cuts.
    events=[row.source_start for previous,row in zip(rows,rows[1:])
            if not row.native_cut and (row.mode!=previous.mode or
                row.mode=='grid4_context' and row.panel_face_indexes!=previous.panel_face_indexes)]
    for index,start in enumerate(events):
        window=[at for at in events[index:] if at<start+10]
        if len(window)>5:
            return start,window[5]
    # Same-family fragments can still jump crop anchors. Count actual planned
    # boundaries as well, matching output QA rather than merely mode names.
    starts=[row.source_start for row in rows if not row.native_cut]
    for index,start in enumerate(starts):
        window=[at for at in starts[index:] if at<start+1.0]
        if len(window)>2:
            return start,window[2]
    return None


def _replace(rows,start,end,samples):
    native=any(row.native_cut and abs(row.source_start-start)<1e-6 for row in rows)
    output=[]
    inserted=False
    for row in rows:
        if row.source_end<=start or row.source_start>=end:
            output.append(row)
            continue
        if row.source_start<start:
            output.append(replace(row,source_end=start))
        if not inserted:
            output.append(_context(start,end,samples,native))
            inserted=True
        if row.source_end>end:
            output.append(replace(row,source_start=end,native_cut=False))
    # Adjacent uncertainty windows are one stable camera, not extra cuts.
    merged=[]
    for row in output:
        if (merged and not row.native_cut and row.reason.startswith('temporary participant uncertainty')
                and merged[-1].reason.startswith('temporary participant uncertainty')):
            previous=merged.pop()
            merged.append(_context(previous.source_start,row.source_end,samples,previous.native_cut))
        else:
            merged.append(row)
    return merged


def settle_layouts(media,rows,samples,hold=3.0):
    result=list(rows)
    # Each iteration replaces a problematic interval, never appends a micro-cut.
    for _ in range(max(16,len(rows)*3)):
        problem=_violation(result,hold)
        if problem is None:
            return result
        start,until=problem
        native_times=[row.source_start for row in result if row.native_cut and row.source_start>start+1e-6]
        boundary=min(native_times,default=media.duration)
        end=min(boundary,max(start+hold,until+hold))
        if end<=start:
            break
        result=_replace(result,start,end,samples)
    return result
