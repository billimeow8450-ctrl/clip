"""Master Editor V7.3: one-ratio delivery and deterministic face-count frames.

This patch is intentionally loaded after V7.1 and V7.2.  It removes the
3:4-inside-9:16 presentation, keeps native portrait sources untouched, makes
single-person shots edge-to-edge, and constrains captions to one job-wide
size.  Legacy editor and clipper code are not imported or changed.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import re
import time
from collections import Counter
from dataclasses import replace
from functools import lru_cache
from pathlib import Path

_installed = False
W = 1080
H_916 = 1920
H_34 = 1440
MARK_34 = "output-family=3:4"
MARK_NATIVE = "native-portrait-lock"


def _height(layout) -> int:
    return H_34 if MARK_34 in str(getattr(layout, "reason", "")) else H_916


def _mark(reason: str, family: str, native: bool = False) -> str:
    clean = re.sub(r";?\s*output-family=(?:9:16|3:4)", "", reason or "").strip(" ;")
    clean = re.sub(r";?\s*native-portrait-lock", "", clean).strip(" ;")
    suffix = f"output-family={family}"
    if native:
        suffix += "; native-portrait-lock"
    return (clean + "; " + suffix).strip(" ;")


def _panel(c, x, y, width, height, face, *, fraction=.34, members=()):
    mx = max(10, round(width * .035))
    my = max(10, round(height * .035))
    return c.Panel(x, y, width, height, face,
                   (mx, my, width - 2 * mx, height - 2 * my),
                   face_fraction=fraction, members=tuple(members))


def _geometry_for(c, mode, active_side, face_count, panel_members, height):
    lead = max(0, face_count - 1) if active_side == "right" else 0
    other = 0 if lead else max(0, face_count - 1)
    half = height // 2
    if mode == "focus":
        if face_count < 1:
            return c.Geometry((_panel(c, 0, 0, W, height, -4, fraction=.30),), round(height*.70))
        return c.Geometry((_panel(c, 0, 0, W, height, lead, fraction=.32),), round(height*.70))
    if mode == "passthrough":
        return c.Geometry((_panel(c, 0, 0, W, height, -4, fraction=.30),), round(height*.70))
    if mode == "fit_blur":
        # The sole permitted blur-backed frame.  Planning restricts it to a
        # verified 2+ person landscape-source moment of <=2.00 seconds.
        return c.Geometry((c.Panel(0, 0, W, height, -1, (0, 0, W, height)),), round(height*.68))
    if mode in {"split_reaction", "horizontal_split", "listener_context",
                "speaker_context", "duo_context", "cinema_duo", "offset_duo"}:
        return c.Geometry((_panel(c, 0, 0, W, half, lead, fraction=.35),
                           _panel(c, 0, half, W, height-half, other, fraction=.35)), half)
    if mode == "diagonal_split":
        top_safe = (48, 45, W-96, max(220, round(height*.46)))
        bottom_y = round(height*.54)
        bottom_safe = (48, bottom_y, W-96, height-bottom_y-32)
        return c.Geometry((c.Panel(0, 0, W, height, lead, top_safe, face_fraction=.33),
                           c.Panel(0, 0, W, height, other, bottom_safe, face_fraction=.33)),
                          round(height*.53), True)
    if mode == "reaction_pip":
        # Kept for saved plans; automatic V7.3 planning never selects it.
        return c.Geometry((_panel(c, 0, 0, W, height, lead, fraction=.31),
                           _panel(c, round(W*.66), round(height*.07), round(W*.30),
                                  round(height*.28), other, fraction=.40)), round(height*.69))
    if mode == "grid3" or mode in {"triple_column", "hero_strip"}:
        rest = [index for index in range(3) if index != lead]
        return c.Geometry((_panel(c, 0, 0, W, half, lead, fraction=.34),
                           _panel(c, 0, half, W//2, height-half, rest[0], fraction=.42),
                           _panel(c, W//2, half, W-W//2, height-half, rest[1], fraction=.42)), half)
    if mode == "grid4":
        return c.Geometry(tuple(_panel(c, (n % 2)*(W//2), (n // 2)*half,
                                             W//2, half if n < 2 else height-half,
                                             n, fraction=.43) for n in range(4)), half)
    if mode == "grid4_context":
        if not panel_members:
            from .subjects import four_panel_members
            panel_members = four_panel_members(tuple((0., 0., 1., 1.) for _ in range(face_count)))
        return c.Geometry(tuple(_panel(c, (n % 2)*(W//2), (n // 2)*half,
                                             W//2, half if n < 2 else height-half,
                                             -3, fraction=.43, members=members)
                                for n, members in enumerate(panel_members)), half)
    if mode in {"portrait_card", "solo_medium", "speaker_detail"}:
        return c.Geometry((_panel(c, 0, 0, W, height, lead, fraction=.31),), round(height*.70))
    raise ValueError(f"unknown layout: {mode}")


def _patch_composition():
    from . import composition as c
    from . import motion as motion
    from . import qa, shot_director as sd

    def geometry(mode, active_side=None, face_count=0, panel_members=()):
        # Calls without a LayoutSegment are planning-time canonical 9:16
        # geometry.  The chosen output height is encoded into every final row.
        return _geometry_for(c, mode, active_side, face_count, panel_members, H_916)

    def layout_geometry(layout, media=None):
        members = layout.panel_face_indexes
        if layout.mode == "grid4_context" and not members:
            from .subjects import four_panel_members
            members = four_panel_members(layout.face_boxes, media.aspect if media else 16/9)
        return _geometry_for(c, layout.mode, layout.active_side,
                             len(layout.face_boxes), members, _height(layout))

    def placement(media, panel, face):
        sw, sh = media.width, media.height
        if face is None:
            scale = min(panel.width/sw, panel.height/sh) if panel.face_index == -1 else max(panel.width/sw, panel.height/sh)
            width = max(2, int(sw*scale)//2*2)
            height = max(2, int(sh*scale)//2*2)
            return width, height, (panel.width-width)//2, (panel.height-height)//2
        bx, by, bw, bh = c.protected_face(panel, face)
        sx, sy, safe_w, safe_h = panel.safe
        fill = max(panel.width/sw, panel.height/sh)
        desired = panel.height*panel.face_fraction/max(1.0, face[3]*sh)
        scale = min(max(fill, desired), fill*2.15)
        width = max(panel.width, int(sw*scale)//2*2)
        height = max(panel.height, int(sh*scale)//2*2)
        x = round(sx+safe_w*.5-(bx+bw*.5)*width)
        y = round(sy+safe_h*.5-(by+bh*.5)*height)
        for axis, scaled, canvas, low, high in (
            (0, width, panel.width, sx-bx*width, sx+safe_w-(bx+bw)*width),
            (1, height, panel.height, sy-by*height, sy+safe_h-(by+bh)*height),
        ):
            lo, hi = max(low, canvas-scaled), min(high, 0)
            if lo <= hi:
                value = round(min(hi, max(lo, x if axis == 0 else y)))
            else:
                value = round(min(0, max(canvas-scaled, x if axis == 0 else y)))
            if axis == 0:
                x = value
            else:
                y = value
        return width, height, x, y

    def tracking_envelope(panel, face):
        if panel.members:
            return face
        x, y, w, h = face
        left = max(0., x-w*.32)
        top = max(0., y-h*.28)
        right = min(1., x+w*1.32)
        bottom = min(1., y+h*2.45)
        return left, top, right-left, bottom-top

    def projected_box(media, panel, face, *, padded=False):
        width, height, x, y = placement(media, panel, face)
        bx, by, bw, bh = c.protected_face(panel, face) if padded else face
        return panel.x+x+bx*width, panel.y+y+by*height, bw*width, bh*height

    def validate_geometry(media, layout):
        errors = []
        try:
            geo = layout_geometry(layout, media)
        except Exception as exc:
            return [str(exc)]
        canvas_h = _height(layout)
        if not geo.panels:
            return ["composition has no panels"]
        for panel in geo.panels:
            if panel.x < 0 or panel.y < 0 or panel.width < 2 or panel.height < 2:
                errors.append("invalid panel rectangle")
            if panel.x+panel.width > W+1 or panel.y+panel.height > canvas_h+1:
                errors.append("panel escapes fixed output ratio")
            indexes = panel.members or ((panel.face_index,) if panel.face_index >= 0 else ())
            if any(index >= len(layout.face_boxes) for index in indexes):
                errors.append("composition has an unverified face panel")
        if not geo.diagonal:
            area = sum(panel.width*panel.height for panel in geo.panels)
            if area < W*canvas_h:
                errors.append("layout leaves an uncovered band")
        if layout.face_keyframes and layout.face_boxes:
            errors.extend(motion.validate_motion(media, layout))
        return list(dict.fromkeys(errors))

    def render_geometry(label, output, layout, media, fps, index, source_time=None, source_end=None):
        from .motion import camera_path, expression, union
        geo = layout_geometry(layout, media)
        failures = validate_geometry(media, layout)
        if failures:
            raise ValueError("Unsafe composition: " + "; ".join(failures))
        canvas_h = _height(layout)
        prefix = f"c{index}_"
        lines = []
        source_labels = [prefix+"base"] + [prefix+f"p{n}" for n in range(len(geo.panels))]
        lines.append(f"[{label}]fps={fps}:eof_action=pass,split={len(source_labels)}"+
                     "".join(f"[{name}]" for name in source_labels))
        bg_scale = c._background_scale(W, canvas_h)
        # This base canvas is sharp for every mode.  In fit_blur the full-size
        # panel below already supplies the ONE permitted blurred background and
        # completely covers this canvas.  Blurring here too was wasted 4K work
        # and doubled FFmpeg 4.4's peak filter memory.
        lines.append(f"[{source_labels[0]}]{bg_scale},crop={W}:{canvas_h},"
                     f"setsar=1[{prefix}bg]")
        if geo.diagonal:
            lines.append(f"[{prefix}bg]split=2[{prefix}bg0][{prefix}bg1]")
        current = prefix+"bg"
        diagonal_layers = []
        for n, panel in enumerate(geo.panels):
            face = c.subject_face(layout, panel)
            width, height, points, _ = camera_path(media, panel, layout)
            envelopes = (tuple(union([frame.boxes[i] for frame in layout.face_keyframes])
                               for i in range(len(layout.face_boxes)))
                         if layout.face_keyframes else layout.face_boxes)
            isolated = (union([envelopes[i] for i in panel.members]) if panel.members
                        else envelopes[panel.face_index] if 0 <= panel.face_index < len(envelopes) else None)
            neighbours = tuple(box for i, box in enumerate(envelopes) if i not in panel.members)
            protected = union([c.padded_face(envelopes[i]) for i in panel.members]) if panel.members else None
            crop_x, crop_y, crop_w, crop_h = c.isolation_crop(isolated, neighbours, width, height, protected)
            left = max(crop_x, min(-p[1] for p in points)-4, 0)
            top = max(crop_y, min(-p[2] for p in points)-4, 0)
            right = min(crop_x+crop_w, max(panel.width-p[1] for p in points)+4, width)
            bottom = min(crop_y+crop_h, max(panel.height-p[2] for p in points)+4, height)
            src_x = max(0, int(left*media.width/width)//2*2)
            src_y = max(0, int(top*media.height/height)//2*2)
            src_right = min(media.width, int(math.ceil(right*media.width/width/2))*2)
            src_bottom = min(media.height, int(math.ceil(bottom*media.height/height/2))*2)
            src_w, src_h = max(2, src_right-src_x), max(2, src_bottom-src_y)
            crop_x, crop_y = src_x*width/media.width, src_y*height/media.height
            crop_w = max(2, round(src_w*width/media.width/2)*2)
            crop_h = max(2, round(src_h*height/media.height/2)*2)
            clock = layout.source_start if source_time is None else source_time
            x = expression(points, 1, clock, crop_x, source_end)
            y = expression(points, 2, clock, crop_y, source_end)
            raw = source_labels[n+1]
            lines.append(f"[{raw}]split=2[{prefix}s{n}][{prefix}b{n}]")
            panel_scale = c._background_scale(panel.width, panel.height)
            if layout.mode == "fit_blur":
                lines.append(f"[{prefix}b{n}]{panel_scale},crop={panel.width}:{panel.height},"
                             f"gblur=sigma=20:steps=2,eq=brightness=-0.045:saturation=0.84,"
                             f"setsar=1[{prefix}pb{n}]")
            else:
                lines.append(f"[{prefix}b{n}]{panel_scale},crop={panel.width}:{panel.height},"
                             f"setsar=1[{prefix}pb{n}]")
            lines.append(f"[{prefix}s{n}]crop={src_w}:{src_h}:{src_x}:{src_y},"
                         f"scale={crop_w}:{crop_h}:flags=lanczos,setsar=1[{prefix}ps{n}]")
            lines.append(f"[{prefix}pb{n}][{prefix}ps{n}]overlay=x='{x}':y='{y}':"
                         f"eval=frame:shortest=1:format=yuv420[{prefix}panel{n}]")
            panel_label = prefix+f"panel{n}"
            if geo.diagonal:
                lines.append(f"[{prefix}bg{n}][{panel_label}]overlay=0:0:shortest=1:"
                             f"format=yuv420,format=gbrp[{prefix}layer{n}]")
                diagonal_layers.append(prefix+f"layer{n}")
            else:
                next_label = prefix+f"canvas{n}"
                lines.append(f"[{current}][{panel_label}]overlay=x={panel.x}:y={panel.y}:"
                             f"shortest=1:format=yuv420[{next_label}]")
                current = next_label
        if geo.diagonal:
            lines.append(f"[{diagonal_layers[0]}][{diagonal_layers[1]}]"
                         "blend=all_expr='if(lt(abs(Y-(0.62*H-0.20*X)),4),"
                         f"0.12*A+0.12*B,if(gt(Y,0.62*H-0.20*X),B,A))'[{prefix}diagonal]")
            current = prefix+"diagonal"
        lines.append(f"[{current}]scale={W}:{canvas_h}:flags=lanczos,setsar=1,"
                     f"format=yuv420p,settb=AVTB[{output}]")
        return lines

    c.geometry = geometry
    c.layout_geometry = layout_geometry
    c.placement = placement
    c.projected_box = projected_box
    c.tracking_envelope = tracking_envelope
    c.validate_geometry = validate_geometry
    c.render_geometry = render_geometry
    motion.placement = placement
    motion.tracking_envelope = tracking_envelope
    motion.subject_face = c.subject_face
    motion.camera_path.cache_clear()
    original_profile = motion.profile_values

    @lru_cache(maxsize=8)
    def profile_values(name):
        if name == "xml_reference":
            return original_profile(name)
        return .12, 8000.0

    motion.profile_values = profile_values
    motion.camera_path.cache_clear()

    def validate_motion(media, layout):
        errors = []
        geo = layout_geometry(layout, media)
        for panel in geo.panels:
            if panel.face_index == -1:
                continue
            width, height, points, fallback = motion.camera_path(media, panel, layout)
            for frame in layout.face_keyframes:
                box = c.subject_face(layout, panel, frame.boxes)
                if box is None:
                    continue
                bx, by, bw, bh = tracking_envelope(panel, box)
                x = motion.value_at(points, frame.time, 1)+bx*width
                y = motion.value_at(points, frame.time, 2)+by*height
                sx, sy, sw, sh = ((0, 0, panel.width, panel.height) if fallback else panel.safe)
                if x < sx-3 or y < sy-3 or x+bw*width > sx+sw+3 or y+bh*height > sy+sh+3:
                    # Full-source fallback is deliberately legal: it prevents
                    # a delayed camera from losing a rapidly moving speaker.
                    if not fallback:
                        errors.append("tracked person leaves the stable safe lane")
        return list(dict.fromkeys(errors))

    motion.validate_motion = validate_motion
    sd.geometry = geometry
    sd.placement = placement
    sd.validate_geometry = validate_geometry
    qa.geometry = geometry
    qa.layout_geometry = layout_geometry
    qa.validate_geometry = validate_geometry


def _patch_captions():
    from . import captions as cp, composition as c

    def reference_style(style, words, height):
        font = cp._font(style.font, 1000, style.italic, style.font_weight)
        factor = cp.ass_metric_scale(cp._font_path(style.font, style.italic, style.font_weight))
        box = font.getbbox("H")
        cap = max(1., (box[3]-box[1])*factor)
        # About a 32px visible cap height at a 1080px-wide master.  The old
        # 74px target could create 120-165 ASS font sizes on several bundled
        # fonts, which is the occasional giant caption reported by the user.
        desired = 32
        size = max(56, min(74, round(desired*1000/cap)))
        tokens = [token.upper() if style.uppercase else token
                  for word in words for token in word.text.split()]
        max_width = min(float(style.max_width), 790.)
        while size > 48 and any(cp._width(token, style, size)+2*style.outline_size+8 > max_width
                                for token in tokens):
            size -= 1
        return replace(style, size=size, max_width=max_width,
                       active_peak_scale=104, active_settle_scale=100,
                       normal_scale=100, lines=min(2, style.lines))

    def phrase_zoom(start, end, at, until):
        if end-start < .50:
            return r"\fscx100\fscy100"
        knots = ((start, 100.), (start+.14, 104.), (start+.40, 100.))
        def value(moment):
            for (a, x), (b, y) in zip(knots, knots[1:]):
                if moment <= b:
                    return x+(y-x)*max(0., min(1., (moment-a)/max(.001, b-a)))
            return 100.
        at, until = round(at*100)/100, round(until*100)/100
        tags = rf"\fscx{value(at):.3f}\fscy{value(at):.3f}"
        previous = at
        for stop in sorted({min(until, moment) for moment, _ in knots if moment > at}):
            if stop > previous:
                tags += rf"\t({round((previous-at)*1000)},{round((stop-at)*1000)}," \
                        rf"\fscx{value(stop):.3f}\fscy{value(stop):.3f})"
                previous = stop
        return tags

    cp._reference_style = reference_style
    cp._phrase_zoom = phrase_zoom
    cp.caption_y = c.caption_y


def _family(plan):
    raw = os.getenv("MASTER_FRAME_RATIO", "auto").strip().lower().replace(" ", "")
    aliases = {"9:16": "9:16", "916": "9:16", "vertical": "9:16",
               "3:4": "3:4", "34": "3:4", "portrait": "3:4"}
    if raw in aliases:
        return aliases[raw], "environment override"
    aspect = plan.media.aspect
    if .50 <= aspect <= .64:
        return "9:16", "native 9:16 portrait source"
    if .68 <= aspect <= .82:
        return "3:4", "native 3:4 portrait source"
    multi = sum(row.source_end-row.source_start for row in plan.layouts
                if max(row.required_faces, len(row.face_boxes)) >= 2)
    fraction = multi/max(.001, plan.media.duration)
    maximum = max((max(row.required_faces, len(row.face_boxes)) for row in plan.layouts), default=0)
    if maximum >= 3 or fraction >= .55:
        return "3:4", "wide multi-person podcast"
    return "9:16", "speaker-led vertical podcast"


def _normalise_layouts(plan, family, native):
    from .subjects import four_panel_members
    from .models import LayoutSegment
    marker = lambda reason: _mark(reason, family, native)
    if native:
        return [LayoutSegment(0., plan.media.duration, "passthrough", required_faces=0,
                              reason=marker("native portrait source preserved edge-to-edge"))]
    two_modes = {"split_reaction", "horizontal_split", "diagonal_split",
                 "duo_context", "cinema_duo", "offset_duo"}
    rows = []
    for index, row in enumerate(plan.layouts):
        count = max(row.required_faces, len(row.face_boxes))
        duration = row.source_end-row.source_start
        boxes = tuple(row.face_boxes)
        side = row.active_side
        members = ()
        if count <= 0 or not boxes:
            mode, count, boxes, side = "passthrough", 0, (), None
        elif count == 1:
            mode, side = "focus", None
        elif count >= 2 and duration <= 2.001:
            # Exact reference rule: any verified shared 2+ person moment up to
            # two seconds keeps every person in the one allowed blur-backed
            # full-source frame.  Longer 3+ person moments use grids below.
            mode, side = "fit_blur", None
        elif count == 2:
            mode = row.mode if row.mode in two_modes else ("diagonal_split" if index % 4 == 1 else "split_reaction")
        elif count == 3:
            mode, side = "grid3", None
        elif count == 4:
            mode, side = "grid4", None
        else:
            mode, side = "grid4_context", None
            members = four_panel_members(boxes, plan.media.aspect)
        rows.append(replace(row, mode=mode, active_side=side,
                            required_faces=count, face_boxes=boxes,
                            face_centers=tuple((x+w/2, y+h/2) for x, y, w, h in boxes),
                            panel_face_indexes=members,
                            caption_y=0, reason=marker(row.reason)))
    return rows


def _patch_qa():
    from . import qa, engine as eng

    def visible(output, plan):
        required = [(index, row) for index, row in enumerate(plan.layouts)
                    if row.required_faces > 0 and row.source_end-row.source_start >= .8]
        if not required or not plan.analysis.get("enforce_visible_face_qa", False):
            return {"checked": False, "segments": 0, "failures": []}
        try:
            import cv2
            from .vision import FaceDetector
        except Exception:
            return {"checked": False, "segments": len(required), "failures": [],
                    "warning": "OpenCV face QA unavailable"}
        cap = cv2.VideoCapture(str(output))
        detector = FaceDetector(cv2)
        failures, failed = [], []
        try:
            rows = required if len(required) <= 32 else [required[round(i*(len(required)-1)/31)] for i in range(32)]
            for occurrence, (layout_index, row) in enumerate(rows, 1):
                start, end = max(0., row.source_start), min(plan.media.duration, row.source_end)
                hits = checked = 0
                for index in range(5):
                    source_time = start+(end-start)*(index+.5)/5
                    mapped = qa.map_source_time(plan.spans, source_time)
                    if mapped is None:
                        continue
                    checked += 1
                    cap.set(cv2.CAP_PROP_POS_MSEC, mapped*1000.)
                    ok, frame = cap.read()
                    if not ok:
                        continue
                    if row.mode == "grid4_context":
                        fh, fw = frame.shape[:2]
                        panels = qa.layout_geometry(row, plan.media).panels
                        counts = []
                        for panel in panels:
                            x, y = round(panel.x*fw/W), round(panel.y*fh/_height(row))
                            width = round(panel.width*fw/W)
                            height = round(panel.height*fh/_height(row))
                            patch = frame[y:y+height, x:x+width]
                            counts.append(len(detector.detect(patch)) if patch.size else 0)
                        expected = [max(1, len(panel.members)) for panel in panels]
                        if all(found >= needed for found, needed in zip(counts, expected)):
                            hits += 1
                    elif len(detector.detect(frame)) >= max(1, row.required_faces):
                        hits += 1
                if checked and hits == 0:
                    failures.append(f"occurrence {occurrence} contained no verified sample with all "
                                    f"{max(1,row.required_faces)} expected faces (mode={row.mode})")
                    failed.append(layout_index)
        finally:
            cap.release()
        return {"checked": True, "segments": len(required), "failures": failures,
                "failed_layout_indexes": failed}

    def inspect(output, plan):
        media = qa.probe(output)
        expected = (W, int(plan.analysis.get("output_height") or H_916))
        errors, warnings = [], []
        actual = (media.width, media.height)
        if actual != expected:
            errors.append(f"wrong output geometry {actual[0]}x{actual[1]}; expected {expected[0]}x{expected[1]}")
        allowed = max(.10, 2./max(12., media.fps))
        if abs(media.duration-plan.output_duration) > allowed:
            errors.append(f"duration mismatch expected={plan.output_duration:.3f}s actual={media.duration:.3f}s")
        if plan.media.has_audio and not media.has_audio:
            errors.append("source had audio but final MP4 has no audio")
        if media.video_codec != "h264":
            errors.append(f"unexpected delivery video codec: {media.video_codec or 'unknown'}")
        if media.has_audio and media.audio_codec != "aac":
            errors.append(f"unexpected delivery audio codec: {media.audio_codec or 'unknown'}")
        expected_fps = float(plan.analysis.get("render_fps") or plan.analysis.get("settings_fps") or 30)
        if abs(media.fps-expected_fps) > 1.1:
            errors.append(f"output frame rate drifted: expected {expected_fps:g}, found {media.fps:.3f}")
        bitrate = output.stat().st_size*8./max(.1, media.duration)
        if bitrate < 1_300_000:
            warnings.append("Full-HD output compressed unusually small; inspect very simple/static footage")
        result = qa.run(["ffmpeg", "-hide_banner", "-nostats", "-v", "info", "-i", str(output),
                         "-vf", "fps=1/12,blackframe=amount=98:threshold=16", "-an", "-f", "null", "-"],
                        timeout=max(180, media.duration*1.8))
        black_rows = re.findall(r"pblack:(\d+)", result.stderr or "")
        if black_rows and len(black_rows) >= max(3, int(media.duration/12*.40)):
            errors.append("too many sampled frames are fully black")
        face_qa = visible(output, plan)
        errors.extend(face_qa.get("failures") or [])
        return {"passed": not errors, "errors": errors, "warnings": warnings,
                "visible_face_qa": face_qa,
                "output": {"path": str(output), "duration": media.duration,
                    "width": media.width, "height": media.height, "fps": media.fps,
                    "has_audio": media.has_audio, "bytes": output.stat().st_size,
                    "video_codec": media.video_codec, "audio_codec": media.audio_codec,
                    "average_total_bitrate": round(bitrate)}}

    def assert_output(output, plan):
        report = inspect(output, plan)
        if not report["passed"]:
            raise qa.CommandError("Final Master Editor QA failed: "+"; ".join(report["errors"][:5]))
        return report

    qa._visible_face_qa = visible
    qa.inspect_output = inspect
    qa.assert_output = assert_output
    eng.inspect_output = inspect
    eng.assert_output = assert_output


def _patch_engine():
    from . import engine as eng, qa, composition as c
    original_build = eng.MasterEngine._build_plan
    original_job = eng.MasterEngine.run_job

    def build(self, *args, **kwargs):
        # Every job starts neutral; a prior 3:4 job cannot influence the next.
        self.settings = replace(self.settings, width=W, height=H_916, crf=14)
        plan = original_build(self, *args, **kwargs)
        family, choice_reason = _family(plan)
        height = H_34 if family == "3:4" else H_916
        target_aspect = W/height
        native = abs(plan.media.aspect-target_aspect) <= .075
        self.settings = replace(self.settings, width=W, height=height, crf=14)
        plan.layouts = _normalise_layouts(plan, family, native)
        plan.caption_style = replace(plan.caption_style,
                                     size=max(54, min(72, plan.caption_style.size)),
                                     lines=min(2, plan.caption_style.lines),
                                     max_width=min(790, plan.caption_style.max_width),
                                     active_peak_scale=104,
                                     active_settle_scale=100,
                                     normal_scale=100)
        counts = Counter(row.mode for row in plan.layouts)
        plan.analysis.update({
            "engine_version": "7.3.0",
            "output_width": W,
            "output_height": height,
            "output_master": f"{W}x{height} H.264 High / AAC / BT.709 / yuv420p",
            "frame_family": family,
            "frame_family_reason": choice_reason,
            "frame_family_locked_for_entire_video": True,
            "mixed_ratio_segments": 0,
            "native_portrait_passthrough": native,
            "single_speaker_policy": "one edge-to-edge frame; never split, duplicate, blur-pad or letterbox",
            "face_count_policy": "1=full-screen; 2=two-person frame; 3=grid3; 4=grid4; 5+=four stable grouped panels",
            "blur_policy": "only landscape-source verified 2+ person moments <=2.00s; never native 9:16/3:4",
            "normal_frame_background": "sharp edge-to-edge cover; no top/bottom blur and no black gutters",
            "caption_sizing": "one job-wide 56-74 renderer size, maximum two lines, controlled 100-104-100 pulse",
            "layout_mode_counts": dict(counts),
            "layout_mode_seconds": {mode: round(sum(r.source_end-r.source_start for r in plan.layouts if r.mode == mode), 3)
                                    for mode in counts},
        })
        # Rebind aliases captured when engine/QA modules were imported.
        eng.layout_geometry = c.layout_geometry
        eng.projected_box = c.projected_box
        eng.validate_plan = qa.validate_plan
        errors = []
        for row in plan.layouts:
            errors.extend(c.validate_geometry(plan.media, row))
            if row.mode == "fit_blur" and (native or row.required_faces < 2 or row.source_end-row.source_start > 2.001):
                errors.append("illegal blur frame")
            if row.required_faces == 1 and row.mode != "focus":
                errors.append("single speaker was not full-screen")
        if errors:
            raise eng.MasterEngineError("V7.3 fixed-ratio QA failed: "+"; ".join(dict.fromkeys(errors)))
        return plan

    def run_job(self, input_path, output_path, **kwargs):
        result = original_job(self, input_path, output_path, **kwargs)
        report = Path(result["report"])
        if report.is_file():
            data = json.loads(report.read_text(encoding="utf-8"))
            data["engine"] = "Master Editor 7.3.0"
            data.setdefault("analysis", {})["engine_version"] = "7.3.0"
            eng.write_json(report, data)
        return result

    eng.MasterEngine._build_plan = build
    eng.MasterEngine.run_job = run_job


def _patch_telegram():
    try:
        from . import telegram_bridge as tb
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        from telegram.error import TelegramError
        from telegram.ext import ApplicationHandlerStop
    except ModuleNotFoundError as exc:
        if exc.name and exc.name.startswith("telegram"):
            return
        raise
    from .fonts import FONT_DIR
    from .utils import filter_escape
    original = tb.handle_master_callback

    async def callback(update, context):
        query = update.callback_query
        action = (query.data or "").split(":", 1)[-1] if query else ""
        if not action.startswith("change_captions:"):
            return await original(update, context)
        if not query or not query.message:
            raise ApplicationHandlerStop
        try:
            await query.answer()
        except TelegramError:
            pass
        if not tb._allowed(update):
            await query.message.reply_text(tb._access_text(), reply_markup=tb._public_access_menu())
            raise ApplicationHandlerStop
        token = action.split(":", 1)[1]
        uid = int(update.effective_user.id) if update.effective_user else 0
        job_raw = context.user_data.get("master_result_job_dir")
        clean_raw = context.user_data.get("master_captionless_output")
        job = Path(str(job_raw)) if job_raw else None
        clean = Path(str(clean_raw)) if clean_raw else None
        ass = job/"MASTER_EDIT_1080P.alternate_captions.ass" if job else None
        output = job/"MASTER_EDIT_CHANGED_CAPTION.mp4" if job else None
        created = float(context.user_data.get("master_result_created") or 0)
        if not (token == context.user_data.get("master_result_token") and clean and ass and output
                and clean.is_file() and ass.is_file() and ass.stat().st_size > 100
                and time.time()-created <= tb.RESULT_TTL_SECONDS):
            await query.message.reply_text("This caption option expired. Create a new Master edit.", reply_markup=tb._master_menu())
            raise ApplicationHandlerStop
        if uid in tb._ACTIVE:
            await query.message.reply_text("A Master job is already running. Wait for it to finish.")
            raise ApplicationHandlerStop
        state = tb.ActiveTask(uid)
        tb._ACTIVE[uid] = state
        notice = await query.message.reply_text("🎨 Applying the alternate video-matched caption template…")
        try:
            source_info = await asyncio.to_thread(tb.probe, clean)
            if output.is_file():
                try:
                    old = await asyncio.to_thread(tb.probe, output)
                    if (old.width, old.height) != (source_info.width, source_info.height):
                        output.unlink(missing_ok=True)
                except Exception:
                    output.unlink(missing_ok=True)
            if not output.is_file():
                vf = "format=pix_fmts=yuv420p,subtitles='"+filter_escape(ass)+"':fontsdir='"+filter_escape(FONT_DIR)+"'"
                command = ["ffmpeg", "-nostdin", "-y", "-hide_banner", "-loglevel", "error",
                    "-filter_threads", "1", "-threads:v", "2", "-i", str(clean),
                    "-map", "0:v:0", "-map", "0:a:0?", "-vf", vf,
                    "-c:v", "libx264", "-preset", "fast", "-crf", "14", "-profile:v", "high",
                    "-pix_fmt", "yuv420p", "-colorspace", "bt709", "-color_primaries", "bt709",
                    "-color_trc", "bt709", "-threads:v", "2", "-c:a", "copy",
                    "-movflags", "+faststart", str(output)]
                process = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT)
                state.process = process
                raw, _ = await process.communicate()
                if state.cancelled or process.returncode:
                    output.unlink(missing_ok=True)
                    raise RuntimeError("stopped" if state.cancelled else raw.decode("utf-8", "replace")[-500:])
            changed = await asyncio.to_thread(tb.probe, output)
            if ((changed.width, changed.height) != (source_info.width, source_info.height)
                    or abs(source_info.duration-changed.duration) > .45):
                output.unlink(missing_ok=True)
                raise RuntimeError("ratio/timing verification failed")
            markup = tb._result_menu(token, bool(context.user_data.get("master_captionless_sent")),
                                     bool(context.user_data.get("master_transcript_sent")), True)
            if output.stat().st_size <= tb.MAX_TELEGRAM_OUTPUT_BYTES:
                try:
                    await context.bot.send_video(chat_id=query.message.chat.id, video=output,
                        caption="✅ Caption template changed. Frame ratio and editing are unchanged.",
                        supports_streaming=True, read_timeout=3600, write_timeout=7200, reply_markup=markup)
                except TelegramError:
                    await context.bot.send_document(chat_id=query.message.chat.id, document=output,
                        caption="✅ Caption template changed. Frame ratio and editing are unchanged.",
                        read_timeout=3600, write_timeout=7200, reply_markup=markup)
            else:
                await query.message.reply_text("Changed-caption version is ready but exceeds Telegram's limit:\n"+str(output), reply_markup=markup)
            context.user_data["master_caption_changed"] = True
            await tb._safe_edit(notice, "✅ Alternate caption version ready.")
        except Exception as exc:
            await tb._safe_edit(notice, "❌ Could not change captions: "+str(exc)[:700], tb._result_menu(token))
        finally:
            tb._ACTIVE.pop(uid, None)
        raise ApplicationHandlerStop

    tb.handle_master_callback = callback


def install():
    global _installed
    if _installed:
        return
    _installed = True
    _patch_composition()
    _patch_captions()
    _patch_qa()
    _patch_engine()
    _patch_telegram()


def check_v73(*, real=True):
    """Deterministic post-install check, including a genuine 1080x1440 render."""
    import tempfile
    from . import composition as c, captions as cp
    from .caption_catalog import catalog
    from .config import Settings, load_caption_styles
    from .director import PROFILES
    from .media import probe
    from .models import (EditPlan, LayoutSegment, MediaInfo, SourceSpan,
                         Transcript, Word)
    from .qa import assert_output
    from .render import render
    from .render_runtime import FFmpegRuntime
    from .subjects import four_panel_members

    source_media = MediaInfo(Path("v73-check.mp4"), 8., 1920, 1080, 25., True)
    boxes = ((.08, .18, .14, .24), (.42, .20, .14, .24),
             (.72, .18, .14, .24), (.82, .55, .10, .18),
             (.10, .58, .10, .18), (.48, .58, .10, .18))
    specs = (("focus", 1, 3.), ("fit_blur", 2, 1.8),
             ("split_reaction", 2, 3.), ("diagonal_split", 2, 3.),
             ("grid3", 3, 3.), ("grid4", 4, 3.),
             ("grid4_context", 6, 3.))
    for mode, count, duration in specs:
        members = four_panel_members(boxes[:count]) if count > 4 else ()
        row = LayoutSegment(0., duration, mode, required_faces=count,
                            face_boxes=boxes[:count], panel_face_indexes=members,
                            reason=MARK_34)
        geo = c.layout_geometry(row, source_media)
        assert max(panel.y+panel.height for panel in geo.panels) == H_34
        graph = c.render_geometry("v", "o", row, source_media, 25., 0, 0., duration)
        blur_count = sum("gblur=" in line for line in graph)
        assert blur_count == (1 if mode == "fit_blur" else 0)
        assert not any("color=black" in line for line in graph)
    sample_words = [Word(0., .5, "CONTROLLED"), Word(.5, 1., "CAPTION")]
    sizes = [cp._reference_style(style, sample_words, H_916).size for style in catalog().values()]
    assert min(sizes) >= 48 and max(sizes) <= 74
    if not real:
        return {"ok": True, "real_3_4_render": False, "caption_size_range": [min(sizes), max(sizes)]}

    with tempfile.TemporaryDirectory(prefix="master_v73_34_") as directory:
        root = Path(directory)
        source = root/"source.mp4"
        stub = type("RuntimePlan", (), {"analysis": {"engine_version": "7.3.0"}})()
        with FFmpegRuntime(stub) as runtime:
            runtime.execute(lambda: runtime.prefix()+[
                "-f", "lavfi", "-i", "testsrc2=size=960x540:rate=25:duration=1.2",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=1.2",
                "-c:v", "libx264", "-preset", "fast", "-crf", "14",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000",
            ]+runtime.encoder_args()+["-t", "1.2", str(source)],
            stage="V7.3 true 3:4 source", timeout=90)
        media = probe(source)
        face = ((.39, .20, .18, .30),)
        rows = [LayoutSegment(0., 1.2, "focus", required_faces=1,
                              face_boxes=face, reason=MARK_34)]
        words = [Word(.02, .58, "RATIO"), Word(.60, 1.15, "LOCKED")]
        plan = EditPlan(media, Transcript(words, "RATIO LOCKED", "en"),
            PROFILES["podcast_interview"], load_caption_styles()["reference_bold_lime"],
            [SourceSpan(0., 1.2, 0.)], rows, [], None, 1.2,
            analysis={"render_fps": 25., "settings_fps": 25.,
                      "engine_version": "7.3.0", "output_width": W,
                      "output_height": H_34, "enforce_visible_face_qa": False})
        output, clean = root/"output.mp4", root/"clean.mp4"
        render(plan, output, root/"render", Settings(height=H_34, fps=25,
               preset="fast", crf=14), clean_output=clean)
        review = assert_output(output, plan)
        assert review["passed"] and (review["output"]["width"], review["output"]["height"]) == (W, H_34)
        graphs = "\n".join(path.read_text(encoding="utf-8")
                           for path in (root/"render").rglob("*.filters.txt"))
        assert "gblur=" not in graphs and "color=black" not in graphs
        return {"ok": True, "real_3_4_render": True,
                "resolution": [W, H_34], "caption_size_range": [min(sizes), max(sizes)]}
