"""Plan verified podcast turns into deliberate reference-style layouts.

One person gets a stable close-up.  A verified shared multi-person moment of
2.00 seconds or less gets the one allowed blur-backed fit.  Longer dialogue
uses duo/split/diagonal frames and three-or-more people use stable grids.  The
removed room-wide group strip is never selected or used as a fallback.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import replace
from statistics import median
from bisect import bisect_left
from typing import Sequence

from .composition import validate_geometry,geometry,placement,isolation_crop,padded_face
from .config import Settings
from .models import FaceBox, FaceKeyframe, LayoutSegment
from .subjects import eligible, bridge_face_gaps, four_panel_members
from .layout_stability import settle_layouts


def union_box(boxes):
    x=min(b[0] for b in boxes); y=min(b[1] for b in boxes)
    r=max(b[0]+b[2] for b in boxes); d=max(b[1]+b[3] for b in boxes)
    return x,y,r-x,d-y


def _observations(window):
    found=defaultdict(list)
    for sample in window:
        for slot,face in enumerate(sorted(sample.faces,key=lambda f:f.cx)):
            if not eligible(face):
                continue
            key=face.track_id if face.track_id >= 0 else -10-slot
            found[key].append((sample.time,face))
    stable={key:rows for key,rows in found.items() if len(rows)>=max(1,len(window)*.45)}
    if not stable:
        return []
    # Include every stable, confidently detected face. A back-row participant
    # is not discarded merely because a foreground person has a larger head.
    selected=list(stable.items())
    return sorted(selected,key=lambda row:median(f.cx for _,f in row[1]))


def _track_data(window, start, end, motion=True):
    observations=_observations(window)
    boxes=[]
    for _,rows in observations:
        boxes.append(tuple(median(getattr(f,name) for _,f in rows) for name in ("x","y","w","h")))
    keyframes=[]
    track_times=[[time for time,_ in rows] for _,rows in observations]
    for sample in window:
        values=[]
        for (_,rows),times in zip(observations,track_times):
            at=bisect_left(times,sample.time)
            candidates=rows[max(0,at-1):min(len(rows),at+1)]
            time,face=min(candidates,key=lambda item:abs(item[0]-sample.time))
            if abs(time-sample.time)>1.05:
                break
            values.append((face.x,face.y,face.w,face.h))
        if values and len(values)==len(boxes):
            keyframes.append(FaceKeyframe(sample.time,tuple(values)))
    if keyframes:
        if keyframes[0].time>start:
            keyframes.insert(0,replace(keyframes[0],time=start))
        if keyframes[-1].time<end:
            keyframes.append(replace(keyframes[-1],time=end))
    if not motion and keyframes:
        boxes=[union_box([frame.boxes[n] for frame in keyframes]) for n in range(len(boxes))]
        keyframes=[]
    return tuple(boxes),tuple(keyframes)


def _label(sample,count=None):
    count=len([f for f in sample.faces if eligible(f)]) if count is None else count
    if count <= 1:
        return "single" if count else "unknown"
    if count>4:
        return 'crowd_'+str(count)
    if count==4:
        return "four"
    if count==3:
        return "three"
    if sample.speaker_state=="both":
        return "shared"
    return sample.active_side if sample.active_side in {"left","right"} else "shared"


def _runs(samples,start,end):
    if not samples:
        return [[start,end,"unknown"]]
    counts=[len([f for f in row.faces if eligible(f)]) for row in samples]
    labels=[_label(row,round(median(counts[max(0,i-2):min(len(counts),i+3)]))) for i,row in enumerate(samples)]
    # A speaking turn contains breaths and closed-mouth phonemes. Bridge only
    # <=0.85s gaps bounded by the SAME verified person, never overlapping
    # speech, a different person, a changed face count or a native source cut.
    original=tuple(labels)
    # The first frame has no previous mouth patch. Use the verified following
    # turn to backfill that <=0.45s analysis warm-up; do not add a visible delay.
    first=next((i for i,label in enumerate(labels) if label in {'left','right'}),None)
    if first and samples[first].time-start<=.45 and all(
        original[i]=='shared' and samples[i].speaker_state!='both' for i in range(first)):
        confirm=[label for i,label in enumerate(original) if samples[first].time<=samples[i].time<=samples[first].time+.65]
        if len(confirm)>=3 and all(label==original[first] for label in confirm):
            labels[:first]=[original[first]]*first
    original=tuple(labels)
    i=0
    while i<len(labels):
        if original[i]!="shared" or samples[i].speaker_state=="both":
            i+=1
            continue
        end_gap=i+1
        while end_gap<len(labels) and original[end_gap]=="shared" and samples[end_gap].speaker_state!="both":
            end_gap+=1
        if i>0 and end_gap<len(labels) and original[i-1] in {"left","right"} and original[i-1]==original[end_gap] and samples[end_gap].time-samples[i].time<=.85:
            labels[i:end_gap]=[original[i-1]]*(end_gap-i)
        i=end_gap
    raw=[]
    for i,(sample,label) in enumerate(zip(samples,labels)):
        at=start if not raw else sample.time
        until=samples[i+1].time if i+1<len(samples) else end
        if raw and raw[-1][2]==label:
            raw[-1][1]=until
        else:
            raw.append([at,until,label])
    # One-frame left/right twitch is not a new speaking turn.
    for i in range(1,len(raw)-1):
        if raw[i][1]-raw[i][0]<.55 and raw[i-1][2]==raw[i+1][2] and raw[i-1][1]-raw[i-1][0]>=.6 and raw[i+1][1]-raw[i+1][0]>=.6:
            raw[i][2]=raw[i-1][2]
    result=[]
    for a,b,label in raw:
        if result and result[-1][2]==label:
            result[-1][1]=b
        else:
            result.append([a,b,label])
    return result


def _split_mode(index, boxes, beats, start, end):
    # Variation occurs only at a verified speaking/semantic boundary.  A timer
    # never cycles frames while the same turn is continuing.
    contrast=any(beat.kind in {"question","contrast","reveal"} and start<=beat.start<end for beat in beats)
    separated=(len(boxes)>=2 and
               abs((boxes[0][0]+boxes[0][2]/2)-(boxes[-1][0]+boxes[-1][2]/2))>=.20)
    if separated and (contrast or index % 5 == 1):
        return "diagonal_split"
    return ("split_reaction", "diagonal_split", "speaker_context",
            "cinema_duo", "offset_duo")[index % 5]


def _solo_mode(media,box):
    # Already-vertical source with a large, centred subject should not receive
    # a second destructive zoom.
    if .50<=media.width/media.height<=.68 and box[3]>=.13:
        return "passthrough"
    return "focus"


def _sharp_coverage(media,mode,boxes,side):
    """Don't turn a tightly cropped input into floating sharp cards on blur.

    A split is eligible only when its available subject pixels substantially
    fill both visible panels. Otherwise use the reference central duo band.
    """
    geo=geometry(mode,side,len(boxes))
    for index,panel in enumerate(geo.panels):
        if panel.members:
            selected=[padded_face(boxes[item]) for item in panel.members]
            box=union_box(selected)
            neighbours=tuple(value for item,value in enumerate(boxes)
                             if item not in panel.members)
        elif 0<=panel.face_index<len(boxes):
            box=boxes[panel.face_index]
            neighbours=boxes
        else:
            continue
        w,h,x,y=placement(media,panel,box)
        cx,cy,cw,ch=isolation_crop(box,neighbours,w,h)
        inside=filled=0
        for yi in range(24):
            py=panel.height*(yi+.5)/24
            for xi in range(16):
                px=panel.width*(xi+.5)/16
                visible=not geo.diagonal or (py<.62*1920-.2*px if index==0 else py>.62*1920-.2*px)
                if visible:
                    inside+=1
                    filled+=x+cx<=px<=x+cx+cw and y+cy<=py<=y+cy+ch
        # Reference edits intentionally allow a restrained matte around a
        # portrait.  Reject only a genuinely tiny/floating crop; the previous
        # .78/.82 gate was so strict that almost every podcast fell back to the
        # same duo frame and never reached its planned split variety.
        if filled/max(1,inside)<(.64 if geo.diagonal else .68):
            return False
    return True


def build_layouts(media,profile,samples,settings:Settings,beats:Sequence=()):
    ordered=sorted((s for s in samples if 0<=s.time<media.duration),key=lambda s:s.time)
    if not ordered:
        return [LayoutSegment(0,media.duration,"passthrough",reason="no verified face; complete sharp source retained")]
    cuts=[0.0]+[s.time for s in ordered[1:] if s.scene_delta>=.22]+[media.duration]
    cuts=sorted(set(cuts))
    result=[]
    repaired_samples=[]
    split_number=0
    for scene_start,scene_end in zip(cuts,cuts[1:]):
        scene=bridge_face_gaps([s for s in ordered if scene_start<=s.time<scene_end])
        repaired_samples.extend(scene)
        raw=_runs(scene,scene_start,scene_end)
        desired=[]
        for start,end,label in raw:
            stable_pair=(label=='shared' and end-start>3.0+.001 and
                not any(s.speaker_state=='both' for s in scene if start<=s.time<end))
            # A held two-person composition does not need an invented speaker
            # identity. Unknown speech keeps spatial order, never swaps people.
            long_turn=(label in {"left","right"} or stable_pair) and end-start>3.0+.001 and settings.split_enabled
            kind="split" if long_turn else ("group" if label in {"left","right","shared"} else label)
            item=[start,end,kind,label,end-start]
            if desired and desired[-1][2]==kind and kind!="split":
                desired[-1][1]=end
            else:
                desired.append(item)
        # Short reaction turns stay in ONE shared shot. Keep that shared shot
        # through the next short region instead of producing micro transitions.
        i=0
        while i<len(desired)-1:
            row=desired[i]
            if row[1]-row[0]<1.2 and row[2] in {"group","unknown"}:
                nxt=desired[i+1]
                if row[2]=='group' and row[1]-row[0]>=.65 and nxt[2]=='split':
                    i+=1
                    continue
                if nxt[2] in {"group","split","unknown"}:
                    boundary=row[0]+1.2
                    if nxt[2]=="split" and nxt[1]-boundary>3.0:
                        row[1]=boundary
                        nxt[0]=boundary
                        i+=1
                        continue
                    desired[i:i+2]=[[row[0],nxt[1],"group","shared",nxt[1]-row[0]]]
                    continue
            i+=1
        # A long sustained answer can change composition at an important new
        # sentence/idea, with >=8s between those changes and >=4s at the tail.
        # No timer-only cycling; a solo close-up is never split for decoration.
        structured=[]
        for start,end,kind,label,turn in desired:
            points=[start]
            if kind=="split":
                for beat in sorted(beats,key=lambda item:item.start):
                    relevant=kind=='split' or (media.width>media.height and beat.kind=='evidence' and
                        any(token in beat.text.lower().split() for token in ('look','show','hands','here')))
                    if relevant and beat.kind in {"question","turn","hook","evidence"} and beat.importance>=.5 and beat.start-points[-1]>=8 and end-beat.start>=4:
                        points.append(beat.start)
                        if kind=='single':
                            break
            points.append(end)
            structured.extend([a,b,kind,label,turn] for a,b in zip(points,points[1:]))
        for start,end,kind,label,turn in structured:
            window=[s for s in scene if start<=s.time<end]
            boxes,keyframes=_track_data(window,start,end,settings.motion_enabled)
            count=len(boxes)
            side=label if label in {"left","right"} else None
            segment_seconds=end-start
            if count==0:
                mode="passthrough"
            elif count==1:
                mode=_solo_mode(media,boxes[0])
                side=None
            elif segment_seconds<=2.0+.001:
                # Exact requested rule: the blur layout is legal only for a
                # short verified shared shot containing at least two people.
                mode="fit_blur"
                side=None
            elif count>4:
                mode="grid4_context"
                side=None
            elif count==4:
                mode="grid4"
                side=None
            elif count==3:
                mode="grid3"
            elif kind=="split" and turn>3.0:
                mode=_split_mode(split_number,boxes,beats,start,end)
                choices=tuple(dict.fromkeys((mode,"split_reaction","diagonal_split",
                    "speaker_context","cinema_duo","offset_duo","duo_context")))
                mode=next((candidate for candidate in choices
                           if _sharp_coverage(media,candidate,boxes,side)),"duo_context")
                split_number+=1
            else:
                mode="speaker_context" if side in {"left","right"} else "duo_context"
            row=LayoutSegment(start,end,mode,active_side=side,
                required_faces=count,face_boxes=boxes,
                face_centers=tuple((x+w/2,y+h/2) for x,y,w,h in boxes),
                face_keyframes=keyframes,native_cut=bool(start==scene_start and start>0),
                turn_seconds=round(segment_seconds,3),
                motion_profile=settings.motion_profile,
                caption_y=0,
                panel_face_indexes=four_panel_members(boxes,media.aspect) if mode=='grid4_context' else (),
                reason=("source camera cut; " if start==scene_start and start>0 else "")+
                    ("single-person centred close-up" if mode=="focus" else
                     "stable visible pair >3 seconds; spatial order locked, speech unassigned" if kind=="split" and count==2 and side is None else
                     "verified sustained speech >3 seconds" if kind=="split" and count==2 else
                     "verified multi-speaker moment <=2.00 seconds; full source over controlled blur" if mode=="fit_blur" else
                     "active speaker above and stable shared context below" if mode=="speaker_context" else
                     "stable two-person context; no room-wide strip" if mode=="duo_context" else
                     "four stable context panels cover every verified participant" if mode=='grid4_context' else
                     "verified visible participants"))
            errors=validate_geometry(media,row)
            if errors:
                fallback=("fit_blur" if count>=2 and segment_seconds<=2.0+.001 else
                          "grid4_context" if count>4 else "grid4" if count==4 else
                          "grid3" if count==3 else "duo_context" if count==2 else
                          "focus" if count==1 else "passthrough")
                members=four_panel_members(boxes,media.aspect) if fallback=='grid4_context' else ()
                row=replace(row,mode=fallback,active_side=None,
                    panel_face_indexes=members,reason="allowed-layout geometric fallback: "+"; ".join(errors))
                if validate_geometry(media,row):
                    # Last local safety net retains the complete sharp source.
                    row=replace(row,mode='passthrough',required_faces=0,face_boxes=(),face_centers=(),
                        face_keyframes=(),active_side=None,panel_face_indexes=(),
                        reason='geometry uncertain; complete sharp source retained')
            result.append(row)
    return settle_layouts(media,result,repaired_samples,settings.layout_min_hold_seconds) if result else [LayoutSegment(0,media.duration,"passthrough",reason="no usable observations; complete sharp source retained")]
