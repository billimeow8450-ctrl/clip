"""Master Editor 7.8.1 frame-control hotfix."""
from __future__ import annotations

import math
from dataclasses import replace
from statistics import median

VERSION = "7.8.1-frame-control"
_installed = False
W, H = 1080, 1920
SHORT_BLUR_MAX = 2.001
MIN_LAYOUT_HOLD = 3.0


def _finite(v, default=0.0):
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except Exception:
        return default


def _valid_box(box):
    if not box or len(box) != 4:
        return False
    x, y, w, h = map(_finite, box)
    if w <= 0.018 or h <= 0.028 or w >= 0.62 or h >= 0.78:
        return False
    ratio = w / max(h, 1e-6)
    if ratio < 0.28 or ratio > 1.75:
        return False
    cx, cy = x + w * .5, y + h * .5
    return -0.05 <= cx <= 1.05 and -0.05 <= cy <= 1.05


def _clamp_box(box):
    x, y, w, h = map(_finite, box)
    x = max(0.0, min(0.985, x))
    y = max(0.0, min(0.985, y))
    w = max(0.018, min(1.0 - x, w))
    h = max(0.028, min(1.0 - y, h))
    return (x, y, w, h)


def _quantile(values, q):
    values = sorted(float(v) for v in values)
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    p = max(0.0, min(1.0, q)) * (len(values) - 1)
    lo = int(math.floor(p))
    hi = int(math.ceil(p))
    if lo == hi:
        return values[lo]
    f = p - lo
    return values[lo] * (1.0 - f) + values[hi] * f


def _robust_envelope(boxes):
    rows = [_clamp_box(b) for b in boxes if _valid_box(b)]
    if not rows:
        return None
    if len(rows) == 1:
        return rows[0]

    lefts = [x for x, y, w, h in rows]
    tops = [y for x, y, w, h in rows]
    rights = [x + w for x, y, w, h in rows]
    bottoms = [y + h for x, y, w, h in rows]

    left = _quantile(lefts, .08)
    top = _quantile(tops, .08)
    right = _quantile(rights, .92)
    bottom = _quantile(bottoms, .92)

    mw = median([b[2] for b in rows])
    mh = median([b[3] for b in rows])
    left -= max(.008, mw * .12)
    right += max(.008, mw * .12)
    top -= max(.008, mh * .10)
    bottom += max(.008, mh * .10)

    left = max(0.0, left)
    top = max(0.0, top)
    right = min(1.0, right)
    bottom = min(1.0, bottom)
    w, h = right - left, bottom - top

    if w > max(.55, mw * 3.4) or h > max(.80, mh * 3.2):
        cx = median([x + w * .5 for x, y, w, h in rows])
        cy = median([y + h * .5 for x, y, w, h in rows])
        w = min(.48, max(mw * 1.55, mw + .035))
        h = min(.70, max(mh * 1.55, mh + .045))
        left = max(0.0, min(1.0 - w, cx - w * .5))
        top = max(0.0, min(1.0 - h, cy - h * .5))
    return _clamp_box((left, top, w, h))


def _count(row):
    return max(int(getattr(row, "required_faces", 0) or 0),
               len(tuple(getattr(row, "face_boxes", ()) or ())))


def _center(row):
    boxes = tuple(getattr(row, "face_boxes", ()) or ())
    if len(boxes) != 1:
        return None
    x, y, w, h = boxes[0]
    return (x + w * .5, y + h * .5)


def _freeze_row_tracks(row):
    boxes = tuple(getattr(row, "face_boxes", ()) or ())
    frames = tuple(getattr(row, "face_keyframes", ()) or ())
    if not boxes:
        return row
    stable = []
    for i, base in enumerate(boxes):
        track = [base]
        for frame in frames:
            fboxes = tuple(getattr(frame, "boxes", ()) or ())
            if i < len(fboxes):
                track.append(fboxes[i])
        env = _robust_envelope(track)
        stable.append(env or _clamp_box(base))
    stable = tuple(stable)
    return replace(
        row,
        face_boxes=stable,
        face_centers=tuple((x + w * .5, y + h * .5) for x, y, w, h in stable),
        face_keyframes=(),
        reason=str(getattr(row, "reason", "")) + "; V781 speaker-safe static envelope",
    )


def _repair_short_detection_gaps(rows):
    rows = list(rows)
    for i, row in enumerate(rows):
        if _count(row) > 0 or getattr(row, "native_cut", False):
            continue
        duration = float(row.source_end) - float(row.source_start)
        if duration > 3.0:
            continue
        prev = rows[i - 1] if i > 0 else None
        nxt = rows[i + 1] if i + 1 < len(rows) else None
        if nxt is not None and getattr(nxt, "native_cut", False):
            nxt = None
        prev_ok = prev is not None and _count(prev) == 1 and tuple(prev.face_boxes or ())
        next_ok = nxt is not None and _count(nxt) == 1 and tuple(nxt.face_boxes or ())
        source = None
        if prev_ok and next_ok:
            pc, nc = _center(prev), _center(nxt)
            if pc and nc and math.hypot(pc[0] - nc[0], pc[1] - nc[1]) <= .24:
                source = prev
        elif duration <= 1.25:
            source = prev if prev_ok else (nxt if next_ok else None)
        if source is None:
            continue
        box = tuple(source.face_boxes)[0]
        rows[i] = replace(
            row,
            mode="focus",
            required_faces=1,
            face_boxes=(box,),
            face_centers=((box[0] + box[2] * .5, box[1] + box[3] * .5),),
            active_side=None,
            face_keyframes=(),
            panel_face_indexes=(),
            reason=str(getattr(row, "reason", "")) + "; V781 bridged verified speaker detector gap",
        )
    return rows


def _safe_mode(c, media, row, preferred, alternatives):
    for mode in (preferred, *alternatives):
        try:
            candidate = replace(row, mode=mode)
            if mode != "grid4_context":
                candidate = replace(candidate, panel_face_indexes=())
            if not c.validate_geometry(media, candidate):
                return mode
        except Exception:
            continue
    return preferred


def _normalise_plan(plan):
    from . import composition as c
    from .subjects import four_panel_members

    rows = [_freeze_row_tracks(r) for r in list(getattr(plan, "layouts", ()) or ())]
    rows = _repair_short_detection_gaps(rows)
    out = []
    duo_index = 0
    trio_index = 0
    last_change = -1e9

    for row in rows:
        boxes = tuple(getattr(row, "face_boxes", ()) or ())
        count = max(int(getattr(row, "required_faces", 0) or 0), len(boxes))
        duration = max(0.0, float(row.source_end) - float(row.source_start))
        side = row.active_side if getattr(row, "active_side", None) in {"left", "right"} else None
        members = ()
        mode = str(getattr(row, "mode", "passthrough") or "passthrough")

        if count <= 0 or not boxes:
            mode, count, boxes, side = "passthrough", 0, (), None
        elif count == 1:
            mode, side = "focus", None
        elif count == 2:
            if duration <= SHORT_BLUR_MAX and plan.media.width > plan.media.height:
                mode, side = "fit_blur", None
            elif duration < MIN_LAYOUT_HOLD:
                mode = _safe_mode(c, plan.media, row, "duo_context",
                                  ("split_reaction", "diagonal_split"))
            else:
                if side:
                    sequence = ("split_reaction", "diagonal_split", "reaction_pip",
                                "speaker_context", "cinema_duo", "offset_duo")
                else:
                    sequence = ("split_reaction", "diagonal_split", "cinema_duo",
                                "offset_duo", "duo_context")
                preferred = sequence[duo_index % len(sequence)]
                duo_index += 1
                mode = _safe_mode(c, plan.media, row, preferred,
                                  ("split_reaction", "diagonal_split", "duo_context"))
        elif count == 3:
            preferred = "hero_strip" if duration >= 6.0 and trio_index % 2 else "grid3"
            trio_index += 1
            mode = _safe_mode(c, plan.media, row, preferred, ("grid3", "triple_column"))
            side = None
        elif count == 4:
            mode, side = "grid4", None
        else:
            mode, side = "grid4_context", None
            try:
                members = four_panel_members(boxes, plan.media.aspect)
            except Exception:
                members = ()

        if (out and not getattr(row, "native_cut", False)
                and float(row.source_start) - last_change < MIN_LAYOUT_HOLD
                and _count(out[-1]) == count
                and mode != "fit_blur" and out[-1].mode != "fit_blur"):
            mode = out[-1].mode

        if not out or mode != out[-1].mode or getattr(row, "native_cut", False):
            last_change = float(row.source_start)

        fixed = replace(
            row,
            mode=mode,
            active_side=side,
            required_faces=count,
            face_boxes=boxes,
            face_centers=tuple((x + w * .5, y + h * .5) for x, y, w, h in boxes),
            face_keyframes=(),
            panel_face_indexes=members if mode == "grid4_context" else (),
            turn_seconds=round(duration, 3),
            reason=str(getattr(row, "reason", "")) + "; V781 controlled reference-frame policy",
        )
        out.append(fixed)

    plan.layouts = out
    analysis = getattr(plan, "analysis", None)
    if isinstance(analysis, dict):
        analysis.update({
            "frame_control_patch": VERSION,
            "speaker_tracking": "robust static per-occurrence envelope; no delayed chase",
            "blur_policy": "only verified landscape multi-speaker <=2.00s center-strip blur fill",
            "minimum_layout_hold": MIN_LAYOUT_HOLD,
            "object_tracking": "never used as a speaker source",
        })
    return plan


def _safe_placement_factory(current):
    def safe_placement(media, panel, face):
        if face is None or not _valid_box(face):
            return current(media, panel, face)
        from . import composition as c
        sw, sh = max(2, int(media.width)), max(2, int(media.height))
        bx, by, bw, bh = c.protected_face(panel, _clamp_box(face))
        sx, sy, safe_w, safe_h = panel.safe
        fill = max(panel.width / sw, panel.height / sh)
        desired = panel.height * max(.16, min(.52, float(getattr(panel, "face_fraction", .34)))) / max(1.0, face[3] * sh)
        cap = max(.01, min(safe_w / max(1.0, bw * sw),
                           safe_h / max(1.0, bh * sh)) * .965)
        scale = min(max(fill, desired), cap) if cap >= fill else cap
        width = max(2, int(sw * scale) // 2 * 2)
        height = max(2, int(sh * scale) // 2 * 2)
        x = round(sx + safe_w * .5 - (bx + bw * .5) * width)
        y = round(sy + safe_h * .5 - (by + bh * .5) * height)
        x = max(panel.width - width, min(0, x))
        y = max(panel.height - height, min(0, y))
        return width, height, x, y
    safe_placement._v781_frame_control = True
    safe_placement._previous = current
    return safe_placement


def _fit_blur_graph(label, output, layout, media, fps, index):
    prefix = f"v781_{index}_"
    return [
        f"[{label}]fps={fps}:eof_action=pass,split=2[{prefix}bg0][{prefix}fg0]",
        f"[{prefix}bg0]scale=w='ceil(iw*max({W}/iw,{H}/ih)/2)*2':h='ceil(ih*max({W}/iw,{H}/ih)/2)*2':flags=lanczos,"
        f"crop={W}:{H},gblur=sigma=22:steps=2,eq=brightness=-0.045:saturation=0.86,setsar=1[{prefix}bg]",
        f"[{prefix}fg0]scale={W}:-2:flags=lanczos,setsar=1[{prefix}fg]",
        f"[{prefix}bg][{prefix}fg]overlay=x=(W-w)/2:y=(H-h)/2:shortest=1:format=yuv420,"
        f"setsar=1,format=yuv420p,settb=AVTB[{output}]",
    ]


def _patch_composition():
    from . import composition as c
    from . import motion
    try:
        from . import shot_director as sd
    except Exception:
        sd = None

    if not getattr(c.placement, "_v781_frame_control", False):
        safe = _safe_placement_factory(c.placement)
        c.placement = safe
        motion.placement = safe
        if sd is not None:
            sd.placement = safe

    current = c.render_geometry
    if not getattr(current, "_v781_frame_control", False):
        def render_geometry(label, output, layout, media, fps, index,
                            source_time=None, source_end=None):
            count = _count(layout)
            duration = max(0.0, float(layout.source_end) - float(layout.source_start))
            legal_blur = (
                layout.mode == "fit_blur"
                and count >= 2
                and duration <= SHORT_BLUR_MAX
                and media.width > media.height
            )
            if legal_blur:
                return _fit_blur_graph(label, output, layout, media, fps, index)

            safe_layout = layout
            if layout.mode == "fit_blur":
                fallback = "duo_context" if count == 2 else "grid3" if count == 3 else "grid4"
                safe_layout = replace(layout, mode=fallback, face_keyframes=())

            lines = current(label, output, safe_layout, media, fps, index, source_time, source_end)
            if "gblur=" in ";".join(lines):
                fallback = ("focus" if count == 1 else
                            "split_reaction" if count == 2 else
                            "grid3" if count == 3 else
                            "grid4" if count == 4 else
                            "grid4_context")
                sharp = replace(safe_layout, mode=fallback, face_keyframes=())
                lines = current(label, output, sharp, media, fps, index, source_time, source_end)
                if "gblur=" in ";".join(lines):
                    raise RuntimeError("V781 rejected unexpected blur outside approved short shared frame")
            return lines

        render_geometry._v781_frame_control = True
        render_geometry._previous = current
        c.render_geometry = render_geometry

    try:
        motion.camera_path.cache_clear()
    except Exception:
        pass


def _patch_engine():
    from . import engine
    current = engine.MasterEngine._build_plan
    if getattr(current, "_v781_frame_control", False):
        return

    def build(self, *args, **kwargs):
        plan = current(self, *args, **kwargs)
        return _normalise_plan(plan)

    build._v781_frame_control = True
    build._previous = current
    engine.MasterEngine._build_plan = build


def check_v781():
    from pathlib import Path
    from . import composition as c
    from .models import LayoutSegment, MediaInfo

    media = MediaInfo(Path("/tmp/v781_fake.mp4"), 10.0, 1920, 1080, 25.0, True)
    one = ((.08, .18, .13, .23),)
    focus = LayoutSegment(0., 6., "focus", required_faces=1, face_boxes=one,
                          face_centers=((.145, .295),), reason="v781-check")
    graph = ";".join(c.render_geometry("in", "out", focus, media, 25.0, 0, 0, 6))
    assert "gblur=" not in graph

    two = ((.12, .17, .12, .22), (.68, .18, .12, .22))
    short = LayoutSegment(0., 1.8, "fit_blur", required_faces=2, face_boxes=two,
                          face_centers=((.18, .28), (.74, .29)), reason="v781-check")
    blur = ";".join(c.render_geometry("in", "out", short, media, 25.0, 1, 0, 1.8))
    assert "gblur=" in blur and "overlay=" in blur

    illegal = replace(short, source_end=4.0)
    no_blur = ";".join(c.render_geometry("in", "out", illegal, media, 25.0, 2, 0, 4))
    assert "gblur=" not in no_blur

    return {
        "ok": True,
        "version": VERSION,
        "speaker_safe_static_envelope": True,
        "tracking_delay": False,
        "minimum_layout_hold_seconds": MIN_LAYOUT_HOLD,
        "approved_blur_only": "landscape + >=2 verified faces + <=2.00s",
        "unexpected_blur": False,
    }


def install():
    global _installed
    if _installed:
        return
    _patch_composition()
    _patch_engine()
    _installed = True
