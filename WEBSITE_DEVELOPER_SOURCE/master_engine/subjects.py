"""Verified subject continuity and deterministic, complete crowd grouping."""
from collections import defaultdict
from dataclasses import replace
from statistics import median


MIN_FACE_AREA = .0005


def eligible(face):
    return face.score >= .70 and face.w * face.h >= MIN_FACE_AREA


def bridge_face_gaps(samples, max_missing=.85):
    """Interpolate ONLY a short gap bounded by the same observed track.

    Call separately for each native shot. Never extrapolate a departed person,
    turn a nearby object into a face, or create speech evidence from interpolation.
    """
    if len(samples) < 3:
        return samples
    deltas=[b.time-a.time for a,b in zip(samples,samples[1:]) if b.time>a.time]
    cadence = median(deltas) if deltas else .20
    tracks = defaultdict(list)
    for index, sample in enumerate(samples):
        for face in sample.faces:
            if eligible(face) and face.track_id >= 0 and not face.interpolated:
                tracks[face.track_id].append((index, face))
    additions = defaultdict(list)
    for observations in tracks.values():
        for (left, a), (right, b) in zip(observations, observations[1:]):
            dt = samples[right].time-samples[left].time
            travel = ((b.cx-a.cx)**2+(b.cy-a.cy)**2)**.5
            if right-left <= 1 or dt > max_missing+cadence+.02 or travel > .18:
                continue
            for index in range(left+1,right):
                if any(face.track_id == a.track_id for face in samples[index].faces):
                    continue
                amount = (samples[index].time-samples[left].time)/dt
                values = {name:getattr(a,name)+(getattr(b,name)-getattr(a,name))*amount
                          for name in ('x','y','w','h')}
                additions[index].append(replace(a,**values,score=min(a.score,b.score),
                                                motion=0.0,interpolated=True))
    return [replace(sample,faces=sorted(sample.faces+additions[index],key=lambda f:f.cx))
            if index in additions else sample for index,sample in enumerate(samples)]


def four_groups(boxes, aspect=16/9):
    """Partition EVERY verified face into four nearby, nonempty groups.

    Groups are spatial, not a constantly changing top-four speaker ranking.
    Four panels can therefore contain five, eight, twenty or more people.
    """
    if len(boxes) < 4:
        raise ValueError('A four-panel context layout needs at least four verified faces')
    def center(index,axis):
        box=boxes[index]
        return box[axis]+box[axis+2]/2
    def extent(group,axis):
        return max(boxes[i][axis]+boxes[i][axis+2] for i in group)-min(boxes[i][axis] for i in group)
    groups=[tuple(range(len(boxes)))]
    while len(groups)<4:
        candidate=max((group for group in groups if len(group)>1),
                      key=lambda group:(max(extent(group,0)*aspect,extent(group,1))*len(group),len(group)))
        axis=0 if extent(candidate,0)*aspect>=extent(candidate,1) else 1
        ordered=sorted(candidate,key=lambda i:(center(i,axis),center(i,1-axis),i))
        at=len(ordered)//2
        groups.remove(candidate)
        groups.extend([tuple(ordered[:at]),tuple(ordered[at:])])
    # Stable reading order. Small differences in eye height are not a new row.
    groups.sort(key=lambda group:(round(median(center(i,1) for i in group)/.22),
                                  median(center(i,0) for i in group)))
    return tuple(tuple(sorted(group)) for group in groups)


def four_panel_members(boxes, aspect=16/9):
    """Map three or more verified people onto the fixed 2x2 reference frame.

    Four or more people are partitioned exactly once.  With three people the
    fourth panel is a stable shared context view, matching the supplied collage
    reference without inventing a fourth face.
    """
    if len(boxes) < 3:
        raise ValueError('The four-panel reference needs at least three verified faces')
    if len(boxes) == 3:
        ordered=tuple(sorted(range(3),key=lambda i:boxes[i][0]+boxes[i][2]/2))
        return ((ordered[0],),(ordered[1],),(ordered[2],),ordered)
    return four_groups(boxes,aspect)
