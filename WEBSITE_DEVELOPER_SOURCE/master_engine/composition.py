"""One geometry model shared by the director, renderer and safety checks.

Every sharp subject layer is fitted into an explicit face-safe rectangle.
The diagonal layout uses two different rectangles INSIDE its visible polygons,
not two full-height crops with a mask hiding the second person's face.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .models import LayoutSegment, MediaInfo

Box = Tuple[float, float, float, float]
W, H = 1080, 1920


@dataclass(frozen=True)
class Panel:
    x: int
    y: int
    width: int
    height: int
    face_index: int
    safe: Tuple[int, int, int, int]  # local x, y, width, height
    border: int = 0
    face_fraction: float = .45
    members: Tuple[int, ...] = ()


@dataclass(frozen=True)
class Geometry:
    panels: Tuple[Panel, ...]
    caption_y: int
    diagonal: bool = False


# All previously useful tested layouts remain available.  The old
# ``group_context`` room-wide strip is intentionally absent because it is the
# black-space frame the user asked to remove.  ``fit_blur`` is retained only
# for the director's <=2 second verified multi-speaker rule.
LAYOUT_NAMES = (
    "focus",
    "fit_blur",
    "passthrough",
    "split_reaction",
    "horizontal_split",
    "diagonal_split",
    "reaction_pip",
    "listener_context",
    "speaker_context",
    "grid3",
    "grid4",
    "grid4_context",
    "duo_context",
    "offset_duo",
    "triple_column",
    "hero_strip",
    "portrait_card",
    "cinema_duo",
    "solo_medium",
    "speaker_detail",
)

# Automatic director subset.  Other names above remain available for existing
# saved plans/API callers, but automatic edits use this measured podcast set.
PRODUCTION_LAYOUTS = (
    "focus", "passthrough", "fit_blur", "duo_context", "speaker_context",
    "split_reaction", "diagonal_split", "cinema_duo", "offset_duo",
    "grid3", "grid4", "grid4_context",
)


def _panel(x: int, y: int, w: int, h: int, face: int, *, border: int = 0) -> Panel:
    # Extra head-room/shoulder-room is applied to the SOURCE face box too.
    return Panel(x, y, w, h, face, (int(w*.07), int(h*.08), int(w*.86), int(h*.82)), border)


def geometry(mode: str, active_side: Optional[str] = None, face_count: int = 0,
             panel_members: Tuple[Tuple[int, ...], ...] = ()) -> Geometry:
    lead = max(0, face_count-1) if active_side == "right" else 0
    other = 0 if lead else max(0, face_count-1)
    if mode == "focus":
        if face_count < 1:
            # Detector uncertainty keeps the complete sharp source on black;
            # it never substitutes a blurred copy or an object as the subject.
            return Geometry((Panel(0, 0, W, H, -1, (0, 0, W, H)),), 1450)
        return Geometry((Panel(0, 0, W, H, lead, (90, 130, 900, 1320),
                               face_fraction=.36),), 1450)
    if mode in {"split_reaction", "horizontal_split"}:
        return Geometry((_panel(0, 0, W, 960, lead),
                         _panel(0, 960, W, 960, other)), 960)
    if mode == "speaker_context":
        # Reference: active speaker above, stable shared two-person context
        # below.  The bottom is one source crop, not another speaker duplicate.
        members = tuple(range(face_count))
        return Geometry((
            Panel(0, 0, W, 960, lead, (75, 65, 930, 820), face_fraction=.46),
            Panel(0, 960, W, 960, -3, (36, 70, 1008, 800),
                  face_fraction=.27, members=members),
        ), 960)
    if mode == "speaker_detail":
        return Geometry((
            Panel(0, 0, W, 1440, lead, (95, 110, 880, 1060), face_fraction=.42),
            Panel(0, 1440, W, 480, lead, (130, 35, 820, 375), face_fraction=.27),
        ), 1120)
    if mode == "diagonal_split":
        # Divider y = .62H - .20x. Upper-left and lower-right rectangles
        # have a measured margin from that line at ALL four corners.
        return Geometry((
            Panel(0, 0, W, H, lead, (60, 140, 860, 800)),
            Panel(0, 0, W, H, other, (140, 1180, 870, 650)),
        ), 1082, True)
    if mode == "reaction_pip":
        return Geometry((
            Panel(0, 0, W, H, lead, (120, 440, 560, 650)),
            _panel(724, 174, 300, 420, other, border=4),
        ), 1210)
    if mode in {"listener_context", "duo_context"}:
        return Geometry((_panel(0, 550, 540, 810, 0),
                         _panel(540, 550, 540, 810, max(0, face_count-1))), 1500)
    if mode == "cinema_duo":
        return Geometry((_panel(32, 330, 1016, 640, lead),
                         _panel(32, 1000, 1016, 640, other)), 990)
    if mode == "offset_duo":
        return Geometry((_panel(36, 160, 790, 820, lead, border=3),
                         _panel(254, 1040, 790, 720, other, border=3)), 1010)
    if mode == "grid3":
        rest = [index for index in range(3) if index != lead]
        return Geometry((_panel(0, 0, W, 960, lead),
                         _panel(0, 960, 540, 960, rest[0]),
                         _panel(540, 960, 540, 960, rest[1])), 960)
    if mode == "grid4":
        return Geometry(tuple(_panel((n % 2)*540, (n // 2)*960,
                                     540, 960, n) for n in range(4)), 960)
    if mode == "grid4_context":
        if not panel_members:
            from .subjects import four_panel_members
            panel_members = four_panel_members(tuple((0.0, 0.0, 1.0, 1.0)
                                                       for _ in range(face_count)))
        return Geometry(tuple(Panel((n%2)*540,(n//2)*960,540,960,-3,
                              (22,78,496,804),face_fraction=.43,members=members)
                              for n,members in enumerate(panel_members)),960)
    if mode == "triple_column":
        return Geometry(tuple(_panel(24+n*350, 300, 332, 840, n)
                              for n in range(3)), 1220)
    if mode == "hero_strip":
        rest = [index for index in range(3) if index != lead]
        return Geometry((_panel(24, 60, 1032, 1090, lead),
                         _panel(60, 1250, 460, 510, rest[0]),
                         _panel(560, 1250, 460, 510, rest[1])), 1200)
    if mode == "portrait_card":
        return Geometry((_panel(54, 180, 972, 1410, lead, border=4),), 1200)
    if mode == "fit_blur":
        return Geometry((Panel(0, 0, W, H, -1, (0, 0, W, H)),), 1400)
    if mode == "passthrough":
        # Sharp centre-cover fallback. Unlike the removed room-wide strip it
        # cannot place a 16:9 island between huge black bands.
        return Geometry((Panel(0, 0, W, H, -4, (0, 0, W, H)),), 1480)
    if mode == "solo_medium":
        return Geometry((Panel(0, 0, W, H, lead, (100, 170, 880, 900),
                               face_fraction=.245),), 1430)
    raise ValueError(f"unknown or removed layout: {mode}")


def caption_y(mode: str) -> int:
    return geometry(mode, face_count=4).caption_y


def layout_geometry(layout: LayoutSegment, media: Optional[MediaInfo] = None) -> Geometry:
    members=layout.panel_face_indexes
    if layout.mode=='grid4_context' and not members:
        from .subjects import four_panel_members
        members=four_panel_members(layout.face_boxes,media.aspect if media else 16/9)
    return geometry(layout.mode,layout.active_side,len(layout.face_boxes),members)


def padded_face(box: Box) -> Box:
    x, y, w, h = box
    left = max(0.0, x-w*.24)
    top = max(0.0, y-h*.30)
    right = min(1.0, x+w*1.24)
    bottom = min(1.0, y+h*1.45)
    return left, top, right-left, bottom-top


def subject_face(layout, panel, boxes=None):
    boxes=layout.face_boxes if boxes is None else boxes
    if panel.members:
        boxes=tuple(boxes[index] for index in panel.members)
    if (panel.face_index==-2 or panel.members) and boxes:
        # Pad EACH head, not the width of the entire group. Padding the group
        # envelope was shrinking a two-person shot back to a room-wide strip.
        boxes=tuple(padded_face(box) for box in boxes)
        x=min(b[0] for b in boxes); y=min(b[1] for b in boxes)
        return x,y,max(b[0]+b[2] for b in boxes)-x,max(b[1]+b[3] for b in boxes)-y
    return boxes[panel.face_index] if 0<=panel.face_index<len(boxes) else None


def protected_face(panel,face):
    return face if panel.face_index==-2 or panel.members else padded_face(face)


def tracking_envelope(panel,face):
    """Conservative upper-body proxy used only for camera boundary checks.

    YuNet verifies a human face; extending that verified anchor is safer than
    following generic object motion from a microphone, plant or poster.
    """
    if panel.members:
        return face
    x,y,w,h=face
    left=max(0.0,x-w*.28)
    top=max(0.0,y-h*.30)
    right=min(1.0,x+w*1.28)
    bottom=min(1.0,y+h*2.20)
    return left,top,right-left,bottom-top


def placement(media: MediaInfo, panel: Panel, face: Optional[Box]) -> Tuple[int, int, int, int]:
    """Return even scaled-source width/height and signed local overlay x/y."""
    sw, sh = media.width, media.height
    if face is None:
        scale = (max(panel.width/sw,panel.height/sh)
                 if panel.face_index==-4 else min(panel.width/sw,panel.height/sh))
        width = max(2, int(sw*scale)//2*2)
        height = max(2, int(sh*scale)//2*2)
        return width, height, (panel.width-width)//2, (panel.height-height)//2
    bx, by, bw, bh = protected_face(panel,face)
    sx, sy, safe_w, safe_h = panel.safe
    # Zoom only as far as the padded face envelope permits. Empty canvas is
    # filled by a softened copy of the source, rather than cropping a head.
    fill = max(panel.width/sw, panel.height/sh)
    cap = min(safe_w/(bw*sw), safe_h/(bh*sh))*.975
    desired = panel.height*panel.face_fraction/max(1.0,face[3]*sh)
    scale = min(max(fill,desired),cap)
    width = max(2, int(sw*scale)//2*2)
    height = max(2, int(sh*scale)//2*2)
    x = round(sx+safe_w*.5 - (bx+bw*.5)*width)
    y = round(sy+safe_h*.5 - (by+bh*.5)*height)
    for axis,scaled,canvas,low,high in ((0,width,panel.width,sx-bx*width,sx+safe_w-(bx+bw)*width),(1,height,panel.height,sy-by*height,sy+safe_h-(by+bh)*height)):
        lo,hi=max(low,canvas-scaled),min(high,0)
        if scaled>=canvas and lo<=hi:
            if axis==0: x=round(min(hi,max(lo,x)))
            else: y=round(min(hi,max(lo,y)))
    return width, height, x, y


def projected_box(media: MediaInfo, panel: Panel, face: Box, *, padded: bool = False) -> Box:
    width, height, x, y = placement(media, panel, face)
    bx, by, bw, bh = protected_face(panel,face) if padded else face
    return panel.x+x+bx*width, panel.y+y+by*height, bw*width, bh*height


def isolation_crop(face: Optional[Box], boxes: Tuple[Box,...], width: int, height: int,
                   group_protection: Optional[Box] = None) -> Tuple[int,int,int,int]:
    """Exclude neighbouring sharp faces without cutting the selected envelope."""
    left,top,right,bottom=0.0,0.0,1.0,1.0
    if face is not None:
        x,y,w,h=face
        px,py,pw,ph=padded_face(face)
        for other in boxes:
            if other == face:
                continue
            ox,oy,ow,oh=other
            if ox+ow < x:
                left=max(left,min(px,(ox+ow+x)*.5))
            elif ox > x+w:
                right=min(right,max(px+pw,(ox+x+w)*.5))
            if oy+oh < y:
                top=max(top,min(py,(oy+oh+y)*.5))
            elif oy > y+h:
                bottom=min(bottom,max(py+ph,(oy+y+h)*.5))
            if group_protection is not None:
                # Crowded/remote-gallery inputs may contain a neighbour's torso
                # past the midpoint between heads. Trim that unused edge to the
                # group's independently padded envelope, never through a head.
                gx,gy,gw,gh=group_protection
                if ox+ow < x:
                    left=max(left,gx)
                elif ox > x+w:
                    right=min(right,gx+gw)
                if oy+oh < y:
                    top=max(top,gy)
                elif oy > y+h:
                    bottom=min(bottom,gy+gh)
    x1=max(0,int(left*width)//2*2)
    y1=max(0,int(top*height)//2*2)
    x2=min(width,int(math.ceil(right*width/2))*2)
    y2=min(height,int(math.ceil(bottom*height/2))*2)
    return x1,y1,max(2,x2-x1),max(2,y2-y1)


def validate_geometry(media: MediaInfo, layout: LayoutSegment) -> List[str]:
    errors: List[str] = []
    if layout.mode not in LAYOUT_NAMES:
        return [f"unknown composition: {layout.mode}"]
    if any(len(frame.boxes)!=len(layout.face_boxes) for frame in layout.face_keyframes):
        return ['tracked keyframe subject count differs from the layout']
    if layout.mode=='grid4_context':
        try:
            from .subjects import four_panel_members
            members=layout.panel_face_indexes or four_panel_members(layout.face_boxes,media.aspect)
        except ValueError as exc:
            return [str(exc)]
        flat=[index for group in members for index in group]
        if (len(members)!=4 or any(not group for group in members)
                or set(flat)!=set(range(len(layout.face_boxes)))
                or (len(layout.face_boxes)>=4 and sorted(flat)!=list(range(len(layout.face_boxes))))):
            return ['four context panels must visibly cover every planned face']
    geo = layout_geometry(layout,media)
    for number, panel in enumerate(geo.panels):
        if panel.face_index == -1:
            continue
        if panel.face_index >= len(layout.face_boxes):
            if layout.required_faces:
                errors.append("composition has an unverified subject panel")
            continue
        box = subject_face(layout,panel)
        if box is None:
            continue
        x, y, w, h = projected_box(media, panel, box, padded=True)
        sx, sy, sw, sh = panel.safe
        if not (x >= panel.x+sx-2 and y >= panel.y+sy-2 and x+w <= panel.x+sx+sw+2 and y+h <= panel.y+sy+sh+2):
            errors.append("padded face escapes its panel safe rectangle")
        if geo.diagonal:
            for px, py in ((x,y), (x+w,y), (x,y+h), (x+w,y+h)):
                line = .62*H-.20*px
                if (number == 0 and py > line-12) or (number == 1 and py < line+12):
                    errors.append("face intersects the diagonal mask")
        if not geo.diagonal:
            for overlay in geo.panels[number+1:]:
                overlap = min(x+w, overlay.x+overlay.width)-max(x, overlay.x)
                vertical = min(y+h, overlay.y+overlay.height)-max(y, overlay.y)
                if overlap > 1 and vertical > 1:
                    errors.append("a reaction panel covers another speaker's face")
    if layout.face_keyframes:
        from .motion import validate_motion
        errors.extend(validate_motion(media,layout))
    return list(dict.fromkeys(errors))


def _background_scale(width: int, height: int) -> str:
    # Use long-standing scale expressions rather than force_divisible_by,
    # which is absent from older FFmpeg builds. Always cover the panel with
    # even pixel dimensions before cropping it to the requested rectangle.
    factor = f"max({width}/iw,{height}/ih)"
    return f"scale=w='ceil(iw*{factor}/2)*2':h='ceil(ih*{factor}/2)*2':flags=lanczos"


def render_geometry(label: str, output: str, layout: LayoutSegment, media: MediaInfo, fps: float, index: int, source_time=None,source_end=None) -> List[str]:
    from .motion import camera_path,expression,union
    geo = layout_geometry(layout,media)
    failures = validate_geometry(media, layout)
    if failures:
        raise ValueError("Unsafe composition: " + "; ".join(failures))
    prefix = f"c{index}_"
    lines: List[str] = []
    source_labels = [prefix+"base"] + [prefix+f"p{n}" for n in range(len(geo.panels))]
    # Convert timing BEFORE framesync overlays. Their EOF timestamp can equal
    # the final frame PTS; a downstream fps filter would drop that last frame
    # (and all frames of a one-frame shot). Keep EOF rounding inclusive here.
    lines.append(f"[{label}]fps={fps}:eof_action=pass,split={len(source_labels)}" + "".join(f"[{name}]" for name in source_labels))
    if layout.mode == "fit_blur":
        lines.append(f"[{source_labels[0]}]{_background_scale(W,H)},crop={W}:{H},"
                     f"gblur=sigma=22:steps=2,eq=brightness=-0.055:saturation=0.82,"
                     f"setsar=1[{prefix}bg]")
    else:
        lines.append(f"[{source_labels[0]}]scale={W}:{H},drawbox=color=black:t=fill,"
                     f"setsar=1[{prefix}bg]")
    if geo.diagonal:
        lines.append(f"[{prefix}bg]split=2[{prefix}bg0][{prefix}bg1]")
    current = prefix+"bg"
    diagonal_layers: List[str] = []
    for n, panel in enumerate(geo.panels):
        face = subject_face(layout,panel)
        width,height,points,_=camera_path(media,panel,layout)
        envelopes=tuple(union([frame.boxes[i] for frame in layout.face_keyframes]) for i in range(len(layout.face_boxes))) if layout.face_keyframes else layout.face_boxes
        isolated_face=(union([envelopes[i] for i in panel.members]) if panel.members
                       else envelopes[panel.face_index] if 0<=panel.face_index<len(envelopes) else None)
        neighbours=tuple(box for i,box in enumerate(envelopes) if i not in panel.members)
        protected_group=union([padded_face(envelopes[i]) for i in panel.members]) if panel.members else None
        crop_x,crop_y,crop_w,crop_h=isolation_crop(isolated_face,neighbours,width,height,protected_group)
        # Crop the union of visible camera windows BEFORE upscaling. Scaling
        # a whole 4K scene to 10K+ and cropping it afterwards wastes memory.
        left=max(crop_x,min(-p[1] for p in points)-4,0)
        top=max(crop_y,min(-p[2] for p in points)-4,0)
        right=min(crop_x+crop_w,max(panel.width-p[1] for p in points)+4,width)
        bottom=min(crop_y+crop_h,max(panel.height-p[2] for p in points)+4,height)
        src_x=max(0,int(left*media.width/width)//2*2)
        src_y=max(0,int(top*media.height/height)//2*2)
        src_right=min(media.width,int(math.ceil(right*media.width/width/2))*2)
        src_bottom=min(media.height,int(math.ceil(bottom*media.height/height/2))*2)
        src_w=max(2,src_right-src_x); src_h=max(2,src_bottom-src_y)
        crop_x=src_x*width/media.width; crop_y=src_y*height/media.height
        crop_w=max(2,round(src_w*width/media.width/2)*2)
        crop_h=max(2,round(src_h*height/media.height/2)*2)
        clock=layout.source_start if source_time is None else source_time
        x=expression(points,1,clock,crop_x,source_end)
        y=expression(points,2,clock,crop_y,source_end)
        raw = source_labels[n+1]
        # Compose the sharp layer over a soft context layer at the preflighted
        # coordinates; the renderer never invents a new crop at render time.
        lines.append(f"[{raw}]split=2[{prefix}s{n}][{prefix}b{n}]")
        if layout.mode == "fit_blur":
            lines.append(f"[{prefix}b{n}]{_background_scale(panel.width,panel.height)},"
                         f"crop={panel.width}:{panel.height},gblur=sigma=20:steps=2,"
                         f"eq=brightness=-0.045:saturation=0.84,setsar=1[{prefix}pb{n}]")
        else:
            lines.append(f"[{prefix}b{n}]scale={panel.width}:{panel.height},"
                         f"drawbox=color=black:t=fill,setsar=1[{prefix}pb{n}]")
        lines.append(f"[{prefix}s{n}]crop={src_w}:{src_h}:{src_x}:{src_y},scale={crop_w}:{crop_h}:flags=lanczos,setsar=1[{prefix}ps{n}]")
        lines.append(f"[{prefix}pb{n}][{prefix}ps{n}]overlay=x='{x}':y='{y}':eval=frame:shortest=1:format=yuv420[{prefix}panel{n}]")
        panel_label = prefix+f"panel{n}"
        if geo.diagonal:
            # Blend two complete streams in original order; no hidden face.
            lines.append(f"[{prefix}bg{n}][{panel_label}]overlay=0:0:shortest=1:format=yuv420,format=gbrp[{prefix}layer{n}]")
            diagonal_layers.append(prefix+f"layer{n}")
        else:
            next_label = prefix+f"canvas{n}"
            border = f",drawbox=x={panel.x}:y={panel.y}:w={panel.width}:h={panel.height}:color=white@0.7:t={panel.border}" if panel.border else ""
            lines.append(f"[{current}][{panel_label}]overlay=x={panel.x}:y={panel.y}:shortest=1:format=yuv420{border}[{next_label}]")
            current = next_label
    if geo.diagonal:
        lines.append(f"[{diagonal_layers[0]}][{diagonal_layers[1]}]blend=all_expr='if(lt(abs(Y-(0.62*H-0.20*X)),4),0.12*A+0.12*B,if(gt(Y,0.62*H-0.20*X),B,A))'[{prefix}diagonal]")
        current = prefix+"diagonal"
    lines.append(f"[{current}]setsar=1,format=yuv420p,settb=AVTB[{output}]")
    return lines
