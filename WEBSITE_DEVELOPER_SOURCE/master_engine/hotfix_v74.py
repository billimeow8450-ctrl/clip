"""Master Editor V7.4: immediate people-aware cuts and readable captions.

Loaded after V7.3.  The output family is still locked once per job.  This
layer backdates a confirmed participant entrance to its first observation,
keeps two-person panel identity spatially fixed, deliberately selects the
diagonal reference for widely separated pairs, and raises the measured
caption cap-height without allowing per-phrase size changes.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import replace
from functools import lru_cache
from pathlib import Path
from statistics import median


_installed = False
PAIR_MODES = {
    "split_reaction", "horizontal_split", "diagonal_split",
    "listener_context", "speaker_context", "duo_context",
    "cinema_duo", "offset_duo",
}


def _patch_responsive_director():
    from . import layout_stability as stability
    from . import shot_director as sd
    from .subjects import eligible

    def responsive_runs(samples, start, end):
        """Confirm with look-ahead, but cut at the first visible observation.

        Looking ahead is offline analysis, not visible latency.  Entrances are
        immediate; a single detector miss is held briefly to avoid flicker.
        """
        if not samples:
            return [[start, end, "unknown"]]
        raw_counts = [len([f for f in row.faces if eligible(f)]) for row in samples]
        counts = []
        current = raw_counts[0]
        for index, value in enumerate(raw_counts):
            now = samples[index].time
            if value > current:
                future = [raw_counts[n] for n in range(index, len(samples))
                          if samples[n].time <= now + .46]
                if samples[index].scene_delta >= .22 or sum(n >= value for n in future) >= 2:
                    current = value
            elif value < current:
                future = [raw_counts[n] for n in range(index, len(samples))
                          if samples[n].time <= now + .42]
                if not any(n >= current for n in future):
                    current = value
            else:
                current = value
            counts.append(current)

        labels = [sd._label(row, count) for row, count in zip(samples, counts)]
        original = tuple(labels)
        first = next((i for i, label in enumerate(labels)
                      if label in {"left", "right"}), None)
        if first and samples[first].time - start <= .45 and all(
                original[i] == "shared" and samples[i].speaker_state != "both"
                for i in range(first)):
            confirm = [label for i, label in enumerate(original)
                       if samples[first].time <= samples[i].time <= samples[first].time + .65]
            if len(confirm) >= 3 and all(label == original[first] for label in confirm):
                labels[:first] = [original[first]] * first

        original = tuple(labels)
        index = 0
        while index < len(labels):
            if original[index] != "shared" or samples[index].speaker_state == "both":
                index += 1
                continue
            stop = index + 1
            while (stop < len(labels) and original[stop] == "shared"
                   and samples[stop].speaker_state != "both"):
                stop += 1
            if (index > 0 and stop < len(labels)
                    and original[index - 1] in {"left", "right"}
                    and original[index - 1] == original[stop]
                    and samples[stop].time - samples[index].time <= .45):
                labels[index:stop] = [original[index - 1]] * (stop - index)
            index = stop

        raw = []
        for index, (sample, label) in enumerate(zip(samples, labels)):
            at = start if not raw else sample.time
            until = samples[index + 1].time if index + 1 < len(samples) else end
            if raw and raw[-1][2] == label:
                raw[-1][1] = until
            else:
                raw.append([at, until, label])
        for index in range(1, len(raw) - 1):
            if (raw[index][1] - raw[index][0] < .42
                    and raw[index - 1][2] == raw[index + 1][2]):
                raw[index][2] = raw[index - 1][2]
        result = []
        for a, b, label in raw:
            if result and result[-1][2] == label:
                result[-1][1] = b
            else:
                result.append([a, b, label])
        return result

    def violation(rows, hold):
        if not rows:
            return None
        since = rows[0].source_start
        for previous, row in zip(rows, rows[1:]):
            if row.native_cut:
                since = row.source_start
                continue
            remap = (row.mode == previous.mode == "grid4_context" and
                     (row.required_faces != previous.required_faces or
                      row.panel_face_indexes != previous.panel_face_indexes))
            if row.mode == previous.mode and not remap:
                continue
            held = row.source_start - since
            drop = row.required_faces < previous.required_faces
            # A confirmed second person is a content event.  It may override
            # the cosmetic hold time and starts at its first observation.
            upgrade = (row.required_faces >= 2 and
                       row.required_faces > previous.required_faces)
            short_blur = previous.mode == "fit_blur" and held >= .50
            enter_short_blur = (row.mode == "fit_blur" and
                                .50 <= row.source_end - row.source_start <= 2.001)
            turn = (previous.mode in {"speaker_context", "duo_context"}
                    and held >= .64 and row.turn_seconds > 3)
            if held < hold - .10 and (remap or not (
                    drop or upgrade or turn or short_blur or enter_short_blur)):
                return since, row.source_start
            since = row.source_start
        events = [row.source_start for previous, row in zip(rows, rows[1:])
                  if not row.native_cut and (row.mode != previous.mode or
                      row.mode == "grid4_context" and
                      row.panel_face_indexes != previous.panel_face_indexes)]
        for index, start in enumerate(events):
            window = [at for at in events[index:] if at < start + 10]
            if len(window) > 5:
                return start, window[5]
        starts = [row.source_start for row in rows if not row.native_cut]
        for index, start in enumerate(starts):
            window = [at for at in starts[index:] if at < start + 1.0]
            if len(window) > 2:
                return start, window[2]
        return None

    sd._runs = responsive_runs
    stability._violation = violation


def _pair_separation(row):
    boxes = list(row.face_boxes)
    for frame in row.face_keyframes:
        if len(frame.boxes) == 2:
            boxes.extend(frame.boxes)
    if len(row.face_boxes) != 2:
        return 0.0, 1.0
    horizontal = []
    vertical = []
    sequences = [row.face_boxes] + [frame.boxes for frame in row.face_keyframes
                                    if len(frame.boxes) == 2]
    for pair in sequences:
        a, b = pair
        horizontal.append(abs((a[0] + a[2] / 2) - (b[0] + b[2] / 2)))
        vertical.append(abs((a[1] + a[3] / 2) - (b[1] + b[3] / 2)))
    return median(horizontal), median(vertical)


def _row_frames(row):
    from .models import FaceKeyframe
    if row.face_keyframes:
        return list(row.face_keyframes)
    return [FaceKeyframe(row.source_start, tuple(row.face_boxes)),
            FaceKeyframe(row.source_end, tuple(row.face_boxes))]


def _merge_pair_rows(rows):
    merged = []
    for row in rows:
        if not merged:
            merged.append(row)
            continue
        previous = merged[-1]
        compatible = (
            row.mode == previous.mode and row.mode in PAIR_MODES
            and row.required_faces == previous.required_faces == 2
            and len(row.face_boxes) == len(previous.face_boxes) == 2
            and not row.native_cut
            and abs(row.source_start - previous.source_end) <= .06
        )
        if compatible:
            distances = [math.hypot(
                (a[0] + a[2] / 2) - (b[0] + b[2] / 2),
                (a[1] + a[3] / 2) - (b[1] + b[3] / 2),
            ) for a, b in zip(previous.face_boxes, row.face_boxes)]
            compatible = max(distances, default=1.0) <= .28
        if not compatible:
            merged.append(row)
            continue
        keyed = {}
        for frame in _row_frames(previous) + _row_frames(row):
            keyed[round(frame.time, 6)] = frame
        frames = tuple(keyed[key] for key in sorted(keyed))
        boxes = tuple(tuple(median(frame.boxes[n][axis] for frame in frames)
                            for axis in range(4)) for n in range(2))
        merged[-1] = replace(
            previous,
            source_end=row.source_end,
            active_side=None,
            face_boxes=boxes,
            face_centers=tuple((x + w / 2, y + h / 2) for x, y, w, h in boxes),
            face_keyframes=frames,
            turn_seconds=round(row.source_end - previous.source_start, 3),
            reason=previous.reason + "; spatially locked pair across speaker turn",
        )
    return merged


def _patch_layout_choice():
    from . import hotfix_v73 as v73

    original_geometry = v73._geometry_for

    def fixed_geometry(c, mode, active_side, face_count, panel_members, height):
        # Multi-person panels never swap places when the detected voice changes.
        if face_count >= 2:
            active_side = None
        return original_geometry(c, mode, active_side, face_count,
                                 panel_members, height)

    original_normalise = v73._normalise_layouts

    def normalise(plan, family, native):
        rows = original_normalise(plan, family, native)
        if native:
            return rows
        selected = []
        for row in rows:
            duration = row.source_end - row.source_start
            if (row.required_faces == 2 and len(row.face_boxes) == 2
                    and duration > 2.001):
                horizontal, vertical = _pair_separation(row)
                # Widely separated people need independent diagonal crops.
                # A close pair uses the calmer top/bottom reference.
                mode = ("diagonal_split" if horizontal >= .22
                        and horizontal >= vertical * .85 else "split_reaction")
                row = replace(
                    row,
                    mode=mode,
                    active_side=None,
                    reason=row.reason + (
                        "; wide pair assigned to stable diagonal reference"
                        if mode == "diagonal_split" else
                        "; close pair assigned to stable top-bottom reference"),
                )
            elif row.required_faces >= 2:
                row = replace(row, active_side=None)
            selected.append(row)
        return _merge_pair_rows(selected)

    v73._geometry_for = fixed_geometry
    v73._normalise_layouts = normalise


def _patch_motion():
    from . import composition as c
    from . import motion

    old_profile = motion.profile_values

    @lru_cache(maxsize=8)
    def profile(name):
        if name == "xml_reference":
            return old_profile(name)
        # Movement still starts only when the safe lane is crossed.  The high
        # ceiling prevents a valid offline move from falling back to a blind
        # centre crop merely because a person moved quickly between samples.
        return .08, 20000.0

    motion.profile_values = profile
    motion.camera_path.cache_clear()
    original_camera_path = motion.camera_path

    @lru_cache(maxsize=512)
    def camera_path(media, panel, layout):
        width, height, points, fallback = original_camera_path(media, panel, layout)
        if not fallback or not layout.face_keyframes:
            return width, height, points, fallback
        observed = [(frame.time, c.subject_face(layout, panel, frame.boxes))
                    for frame in layout.face_keyframes]
        observed = [(time, box) for time, box in observed if box is not None]
        if not observed:
            return width, height, points, fallback
        x1 = min(box[0] for _, box in observed)
        y1 = min(box[1] for _, box in observed)
        x2 = max(box[0] + box[2] for _, box in observed)
        y2 = max(box[1] + box[3] for _, box in observed)
        stable = (x1, y1, x2 - x1, y2 - y1)
        width, height, _, _ = c.placement(media, panel, stable)
        sx, sy, sw, sh = panel.safe

        def target(box, axis, scaled, canvas, origin, span):
            tracked = c.tracking_envelope(panel, box)
            low = max(origin - tracked[axis] * scaled, canvas - scaled)
            high = min(origin + span - (tracked[axis] + tracked[axis + 2]) * scaled, 0)
            preferred = origin + span / 2 - (
                tracked[axis] + tracked[axis + 2] / 2) * scaled
            if low <= high:
                return min(high, max(low, preferred))
            return min(0, max(canvas - scaled, preferred))

        rebuilt = []
        for time, box in observed:
            x = target(box, 0, width, panel.width, sx, sw)
            y = target(box, 1, height, panel.height, sy, sh)
            rebuilt.append((time, round(x, 4), round(y, 4)))
        if rebuilt[0][0] > layout.source_start:
            rebuilt.insert(0, (layout.source_start, rebuilt[0][1], rebuilt[0][2]))
        if rebuilt[-1][0] < layout.source_end:
            rebuilt.append((layout.source_end, rebuilt[-1][1], rebuilt[-1][2]))
        compact = []
        for point in rebuilt:
            if compact and point[1:] == compact[-1][1:]:
                compact[-1] = point
            else:
                compact.append(point)
        return width, height, tuple(compact), True

    motion.camera_path = camera_path


def _patch_captions():
    from . import captions as cp

    def reference_style(style, words, height):
        font = cp._font(style.font, 1000, style.italic, style.font_weight)
        factor = cp.ass_metric_scale(cp._font_path(
            style.font, style.italic, style.font_weight))
        box = font.getbbox("H")
        cap = max(1.0, (box[3] - box[1]) * factor)
        # V7.3 used 32px visible cap-height and was too small.  42px is a
        # measured middle setting: clearly larger, still below the giant style.
        size = max(68, min(92, round(42 * 1000 / cap)))
        tokens = [token.upper() if style.uppercase else token
                  for word in words for token in word.text.split()]
        max_width = min(float(style.max_width), 810.0)
        while size > 64 and any(
                cp._width(token, style, size) + 2 * style.outline_size + 8 > max_width
                for token in tokens):
            size -= 1
        return replace(
            style,
            size=size,
            max_width=max_width,
            active_peak_scale=104,
            active_settle_scale=100,
            normal_scale=100,
            lines=min(2, style.lines),
        )

    cp._reference_style = reference_style


def _patch_qa():
    from . import engine as eng
    from . import qa

    original_inspect = qa.inspect_output

    def inspect(output, plan):
        report = original_inspect(output, plan)
        try:
            qa.run([
                "ffmpeg", "-nostdin", "-hide_banner", "-v", "error", "-xerror",
                "-i", str(output), "-map", "0:v:0", "-f", "null", "-",
            ], timeout=max(180, plan.output_duration * 2.0))
            report["full_decode_verified"] = True
        except Exception as exc:
            report["full_decode_verified"] = False
            report["passed"] = False
            report.setdefault("errors", []).append(
                "output contains an unreadable/truncated video packet: " + str(exc)[:260])
        return report

    def assert_output(output, plan):
        report = inspect(output, plan)
        if not report["passed"]:
            raise qa.CommandError("Final Master Editor QA failed: " +
                                  "; ".join(report["errors"][:5]))
        return report

    qa.inspect_output = inspect
    qa.assert_output = assert_output
    eng.inspect_output = inspect
    eng.assert_output = assert_output


def _patch_engine():
    from . import engine as eng

    original_build = eng.MasterEngine._build_plan
    original_job = eng.MasterEngine.run_job

    def build(self, *args, **kwargs):
        plan = original_build(self, *args, **kwargs)
        plan.caption_style = replace(
            plan.caption_style,
            size=max(68, min(92, plan.caption_style.size)),
            active_peak_scale=104,
            active_settle_scale=100,
            normal_scale=100,
            lines=min(2, plan.caption_style.lines),
        )
        plan.analysis.update({
            "engine_version": "7.4.0",
            "speaker_entrance_policy": (
                "confirmed with look-ahead, cut backdated to first 0.20s observation"),
            "multi_panel_identity": "spatially locked; never swaps on speaker turn",
            "diagonal_policy": "sustained two-person wide separation >=0.22",
            "camera_policy": "freeze inside safe lane; zero-lag eased correction at boundary",
            "caption_sizing": (
                "one job-wide measured 42px cap-height, bounded 64-92 ASS size, two lines"),
            "strict_full_decode_qa": True,
        })
        return plan

    def run_job(self, input_path, output_path, **kwargs):
        result = original_job(self, input_path, output_path, **kwargs)
        report = Path(result["report"])
        if report.is_file():
            data = json.loads(report.read_text(encoding="utf-8"))
            data["engine"] = "Master Editor 7.4.0"
            data.setdefault("analysis", {})["engine_version"] = "7.4.0"
            eng.write_json(report, data)
        return result

    eng.MasterEngine._build_plan = build
    eng.MasterEngine.run_job = run_job


def install():
    global _installed
    if _installed:
        return
    _installed = True
    _patch_responsive_director()
    _patch_layout_choice()
    _patch_motion()
    _patch_captions()
    _patch_qa()
    _patch_engine()


def check_v74(*, real=True):
    """Decision, geometry, caption and genuine 3:4 render checks."""
    import tempfile
    from types import SimpleNamespace
    from . import captions as cp
    from . import composition as c
    from . import hotfix_v73 as v73
    from . import motion
    from .caption_catalog import catalog
    from .config import Settings, load_caption_styles
    from .director import PROFILES
    from .media import probe
    from .models import (EditPlan, FaceBox, FaceKeyframe, LayoutSegment,
                         MediaInfo, SourceSpan, Transcript, VisionSample, Word)
    from .qa import assert_output
    from .render import render
    from .render_runtime import FFmpegRuntime
    from . import shot_director as sd

    media = MediaInfo(Path("v74-check.mp4"), 4.2, 1920, 1080, 25.0, True)
    one = FaceBox(.08, .20, .16, .28, .99, track_id=1)
    two = FaceBox(.75, .21, .15, .27, .99, track_id=2)
    samples = [VisionSample(round(n * .2, 2), [one] if n == 0 else [one, two])
               for n in range(21)]
    rows = sd.build_layouts(media, PROFILES["podcast_interview"], samples,
                            Settings(), ())
    pair = next(row for row in rows if row.required_faces == 2)
    if pair.source_start > .201:
        raise AssertionError("two-person entrance was delayed")
    fake = SimpleNamespace(media=media, layouts=rows)
    final_rows = v73._normalise_layouts(fake, "9:16", False)
    pair = next(row for row in final_rows if row.required_faces == 2)
    if pair.mode != "diagonal_split":
        raise AssertionError("wide sustained pair did not select diagonal")
    left = c.layout_geometry(replace(pair, active_side="left"), media)
    right = c.layout_geometry(replace(pair, active_side="right"), media)
    if tuple(p.face_index for p in left.panels) != tuple(p.face_index for p in right.panels):
        raise AssertionError("speaker turn swapped panel identity")

    words = [Word(0.0, .5, "READABLE"), Word(.5, 1.0, "CAPTION")]
    sizes = []
    visible_caps = []
    for style in catalog().values():
        chosen = cp._reference_style(style, words, 1920)
        font = cp._font(style.font, 1000, style.italic, style.font_weight)
        factor = cp.ass_metric_scale(cp._font_path(
            style.font, style.italic, style.font_weight))
        bounds = font.getbbox("H")
        cap = (bounds[3] - bounds[1]) * factor * chosen.size / 1000
        sizes.append(chosen.size)
        visible_caps.append(cap)
    if min(sizes) < 64 or max(sizes) > 92 or median(visible_caps) < 40:
        raise AssertionError("caption size is outside controlled readable range")

    if not real:
        return {
            "ok": True,
            "responsive_pair_start": pair.source_start,
            "pair_mode": pair.mode,
            "stable_panel_indexes": [p.face_index for p in left.panels],
            "caption_size_range": [min(sizes), max(sizes)],
            "median_visible_cap_height": round(median(visible_caps), 1),
            "real_3_4_render": False,
        }

    with tempfile.TemporaryDirectory(prefix="master_v74_34_") as directory:
        root = Path(directory)
        source = root / "source.mp4"
        duration = 2.2
        stub = SimpleNamespace(analysis={"engine_version": "7.4.0"})
        with FFmpegRuntime(stub) as runtime:
            runtime.execute(lambda: runtime.prefix() + [
                "-f", "lavfi", "-i",
                f"testsrc2=size=960x540:rate=25:duration={duration}",
                "-f", "lavfi", "-i",
                f"sine=frequency=440:sample_rate=48000:duration={duration}",
                "-c:v", "libx264", "-preset", "fast", "-crf", "14",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000",
            ] + runtime.encoder_args() + ["-t", str(duration), str(source)],
                stage="V7.4 true 3:4 source", timeout=90)
        source_media = probe(source)
        boxes = ((.08, .19, .16, .29), (.73, .21, .16, .28))
        frames = (FaceKeyframe(0.0, boxes), FaceKeyframe(duration, boxes))
        layout = LayoutSegment(
            0.0, duration, "diagonal_split", required_faces=2,
            face_boxes=boxes,
            face_centers=tuple((x + w / 2, y + h / 2) for x, y, w, h in boxes),
            face_keyframes=frames,
            reason=v73.MARK_34,
        )
        plan = EditPlan(
            source_media,
            Transcript([Word(.02, 1.05, "READABLE"),
                        Word(1.08, 2.15, "CAPTION")],
                       "READABLE CAPTION", "en"),
            PROFILES["podcast_interview"],
            load_caption_styles()["reference_bold_lime"],
            [SourceSpan(0.0, duration, 0.0)], [layout], [], None, duration,
            analysis={
                "render_fps": 25.0,
                "settings_fps": 25.0,
                "engine_version": "7.4.0",
                "output_width": 1080,
                "output_height": 1440,
                "enforce_visible_face_qa": False,
            },
        )
        output = root / "output.mp4"
        clean = root / "clean.mp4"
        render(plan, output, root / "render",
               Settings(height=1440, fps=25, preset="fast", crf=14),
               clean_output=clean)
        review = assert_output(output, plan)
        graphs = "\n".join(path.read_text(encoding="utf-8")
                            for path in (root / "render").rglob("*.filters.txt"))
        if "gblur=" in graphs or "color=black" in graphs:
            raise AssertionError("sustained pair introduced blur or black canvas")
        return {
            "ok": True,
            "responsive_pair_start": pair.source_start,
            "pair_mode": pair.mode,
            "stable_panel_indexes": [p.face_index for p in left.panels],
            "caption_size_range": [min(sizes), max(sizes)],
            "median_visible_cap_height": round(median(visible_caps), 1),
            "real_3_4_render": True,
            "resolution": [review["output"]["width"], review["output"]["height"]],
            "full_decode_verified": review.get("full_decode_verified", False),
        }
