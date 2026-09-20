from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List

from .media import probe
from .composition import geometry, layout_geometry, validate_geometry
from .timeline import map_source_time
from .models import EditPlan
from .utils import CommandError, run


MULTI_FACE_MODES = {
    "fit_blur", "split_reaction", "horizontal_split", "diagonal_split",
    "reaction_pip", "listener_context", "speaker_context", "grid3", "grid4",
    "grid4_context", "duo_context", "offset_duo", "triple_column",
    "hero_strip", "cinema_duo",
}


def validate_plan(plan: EditPlan) -> List[str]:
    errors: List[str] = []
    if not plan.spans:
        errors.append("timeline has no source spans")
    if not plan.layouts:
        errors.append("vision director has no layout segments")
    if plan.output_duration <= 0:
        errors.append("output duration is zero")
    previous_end = -1.0
    for span in plan.spans:
        if span.source_start < previous_end - 1e-4:
            errors.append("source spans overlap or are out of order")
        if span.source_end <= span.source_start:
            errors.append("source span has invalid duration")
        previous_end = span.source_end

    if plan.layouts and plan.layouts[0].source_start > .06:
        errors.append("layout plan does not start at the source beginning")
    if plan.layouts and plan.layouts[-1].source_end < plan.media.duration - .06:
        errors.append("layout plan does not cover the source ending")

    last_order = None
    last_order_switch = -9999.0
    previous_layout = None
    mode_since = plan.layouts[0].source_start if plan.layouts else 0.0
    layout_starts: List[float] = []
    for layout in plan.layouts:
        errors.extend(validate_geometry(plan.media, layout))
        if layout.mode == "group_context":
            errors.append("removed room-wide group frame reappeared")
        if layout.mode == "fit_blur":
            if layout.required_faces < 2:
                errors.append("blur frame requires at least two verified people")
            if layout.source_end-layout.source_start > 2.001:
                errors.append("blur frame exceeds the exact 2.00 second limit")
        if not layout.native_cut:
            layout_starts.append(layout.source_start)
        if layout.native_cut:
            last_order=None
            last_order_switch=-9999.0
            mode_since=layout.source_start
        if layout.source_end <= layout.source_start:
            errors.append("layout segment has invalid duration")
        if previous_layout:
            if layout.source_start > previous_layout.source_end + .08:
                errors.append("layout plan contains a visual gap")
            if layout.source_start < previous_layout.source_end - .08:
                errors.append("layout plan contains overlapping shots")
        if layout.mode in MULTI_FACE_MODES:
            if layout.required_faces < 2:
                errors.append(f"{layout.mode} lacks verified face requirement")
            if len(layout.face_centers) < layout.required_faces:
                errors.append(f"{layout.mode} lacks verified face centers")
            if len(layout.face_boxes) < layout.required_faces:
                errors.append(f"{layout.mode} lacks verified human face boxes")
            for x, y, w, h in layout.face_boxes:
                if w <= 0 or h <= 0 or x < 0 or y < 0 or x+w > 1.02 or y+h > 1.02:
                    errors.append(f"{layout.mode} contains an invalid face-safe crop anchor")
            order = layout.active_side
            if last_order is None and order:
                last_order = order
                last_order_switch = layout.source_start
            elif order and last_order and order != last_order:
                lock = float(plan.analysis.get("speaker_order_lock_seconds") or 4.0)
                if layout.source_start - last_order_switch < lock - .08:
                    errors.append("speaker order switched inside the configured lock")
                last_order = order
                last_order_switch = layout.source_start
        if previous_layout and layout.mode != previous_layout.mode:
            safety_drop = layout.required_faces < previous_layout.required_faces
            verified_upgrade = (
                layout.required_faces >= 2
                and layout.required_faces > previous_layout.required_faces
                and layout.source_start-mode_since >= .90
            )
            held = layout.source_start - mode_since
            hold = float(plan.analysis.get("layout_hold_seconds") or 4.0)
            speech_boundary=(previous_layout.mode in {"speaker_context","duo_context"}
                             and held>=.64 and layout.turn_seconds>3)
            short_blur_boundary=previous_layout.mode=="fit_blur" and held>=.50
            enter_short_blur=(layout.mode=="fit_blur"
                              and .50<=layout.source_end-layout.source_start<=2.001)
            if held < hold - .10 and not safety_drop and not verified_upgrade and not layout.native_cut and not speech_boundary:
                if not short_blur_boundary and not enter_short_blur:
                    errors.append("frame family changed before the adaptive layout hold")
            mode_since = layout.source_start
        previous_layout = layout

    for index, start in enumerate(layout_starts):
        changes = sum(start <= value < start + 1.0 for value in layout_starts[index:])
        if changes > 2:
            errors.append("camera plan changes more than twice inside one second")
            break

    for event in plan.broll:
        if event.kind in {"image", "video"} and (not event.path or not event.path.exists()):
            errors.append(f"B-roll asset is missing for query: {event.query}")
        if event.source_end <= event.source_start:
            errors.append("B-roll event has invalid duration")
        if not event.provider or not event.license:
            errors.append("B-roll provenance/license metadata is missing")
        if any(
            event.source_start < graphic.source_end + .75
            and event.source_end > graphic.source_start - .75
            for graphic in plan.graphics
        ):
            errors.append("B-roll and graphic overlays collide in the same attention window")

    major_events = sorted(
        [row.source_start for row in plan.broll]
        + [row.source_start for row in plan.graphics]
        + [
            row.source_start for previous, row in zip(plan.layouts, plan.layouts[1:])
            if row.mode != previous.mode and not row.native_cut
        ]
    )
    for start in major_events:
        if sum(start <= value < start + 10.0 for value in major_events) > 5:
            errors.append("visual effects plan exceeds the controlled ten-second density budget")
            break
    if not (54 <= plan.caption_style.size <= 82) or plan.caption_style.lines > 2:
        errors.append("caption template has unsafe size or line count")
    if not (0.50 <= plan.caption_style.y_normal / 1920.0 <= 0.80):
        errors.append("caption baseline is outside the controlled lower-centre safe band")
    return errors


def _visible_face_qa(output: Path, plan: EditPlan) -> Dict[str, object]:
    required_segments = [(index,row) for index,row in enumerate(plan.layouts)
        if row.required_faces>0 and row.source_end-row.source_start>=.8]
    if not required_segments or not plan.analysis.get("enforce_visible_face_qa", False):
        return {"checked": False, "segments": 0, "failures": []}
    try:
        import cv2
        from .vision import FaceDetector
    except Exception:
        return {"checked": False, "segments": len(required_segments), "failures": [], "warning": "OpenCV face QA unavailable"}
    cap = cv2.VideoCapture(str(output))
    detector = FaceDetector(cv2)
    failures = []
    failed_indexes=[]
    try:
        # Spread samples through long edits, not just the first 24 shots.
        checked_rows=required_segments if len(required_segments)<=32 else [required_segments[round(i*(len(required_segments)-1)/31)] for i in range(32)]
        for occurrence, (layout_index,row) in enumerate(checked_rows, 1):
            # Map SOURCE samples through the final edit timeline, including cuts.
            start = max(0.0, row.source_start)
            end = min(plan.media.duration, row.source_end)
            hits = 0
            samples = 5
            checked = 0
            for index in range(samples):
                t = start + (end-start) * (index+.5) / samples
                if any(event.source_start <= t < event.source_end for event in plan.broll):
                    continue
                mapped = map_source_time(plan.spans, t)
                if mapped is None:
                    continue
                checked += 1
                cap.set(cv2.CAP_PROP_POS_MSEC, mapped*1000.0)
                ok, frame = cap.read()
                if ok and row.mode == 'grid4_context':
                    # Review each sharp panel at its own resolution. Shrinking
                    # a 20-person output to 800px misses small, visible faces.
                    fh,fw=frame.shape[:2]
                    panels=layout_geometry(row,plan.media).panels
                    counts=[]
                    for panel in panels:
                        x=round(panel.x*fw/1080); y=round(panel.y*fh/1920)
                        w=round(panel.width*fw/1080); h=round(panel.height*fh/1920)
                        patch=frame[y:y+h,x:x+w]
                        counts.append(len(detector.detect(patch)))
                    expected=[1 if len(row.face_boxes)==3 else len(panel.members)
                              for panel in panels]
                    if all(found>=needed for found,needed in zip(counts,expected)):
                        hits+=1
                    continue
                reviewed=frame if ok else None
                if ok and len(detector.detect(reviewed)) >= max(1,row.required_faces):
                    hits += 1
            if checked and hits == 0:
                failures.append(
                    f"occurrence {occurrence} contained no sample with all {max(1,row.required_faces)} expected faces (0/{checked}; mode={row.mode})"
                )
                failed_indexes.append(layout_index)
    finally:
        cap.release()
    return {"checked": True, "segments": len(required_segments), "failures": failures,"failed_layout_indexes":failed_indexes}


def inspect_output(output: Path, plan: EditPlan) -> Dict[str, object]:
    media = probe(output)
    errors: List[str] = []
    warnings: List[str] = []
    if (media.width, media.height) != (1080, 1920):
        errors.append(f"wrong output geometry {media.width}x{media.height}")
    allowed_delta = max(.10, 2.0/max(12.0,media.fps))
    if abs(media.duration-plan.output_duration) > allowed_delta:
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
    bitrate = output.stat().st_size * 8.0 / max(.1, media.duration)
    if bitrate < 1_500_000:
        warnings.append("1080p output compressed unusually small; inspect very simple/static footage")
    result = run([
        "ffmpeg", "-hide_banner", "-nostats", "-v", "info", "-i", str(output),
        "-vf", "fps=1/12,blackframe=amount=98:threshold=16", "-an", "-f", "null", "-",
    ], timeout=max(180, media.duration*1.8))
    black_rows = re.findall(r"pblack:(\d+)", result.stderr or "")
    if black_rows and len(black_rows) >= max(3, int(media.duration/12*.40)):
        errors.append("too many sampled frames are fully black")
    face_qa = _visible_face_qa(output, plan)
    errors.extend(list(face_qa.get("failures") or []))
    return {
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
        "visible_face_qa": face_qa,
        "output": {
            "path": str(output), "duration": media.duration,
            "width": media.width, "height": media.height, "fps": media.fps,
            "has_audio": media.has_audio, "bytes": output.stat().st_size,
            "video_codec": media.video_codec, "audio_codec": media.audio_codec,
            "average_total_bitrate": round(bitrate),
        },
    }


def assert_output(output: Path, plan: EditPlan) -> Dict[str, object]:
    report = inspect_output(output, plan)
    if not report["passed"]:
        raise CommandError("Final Master Editor QA failed: " + "; ".join(report["errors"][:5]))
    return report
