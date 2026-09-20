from __future__ import annotations
from functools import lru_cache
import math
from pathlib import Path
from statistics import median
_installed = False

def _keyframes(layout):
    from .models import FaceKeyframe
    if layout.face_keyframes:
        return tuple(layout.face_keyframes)
    boxes = tuple(layout.face_boxes)
    return (FaceKeyframe(layout.source_start, boxes), FaceKeyframe(layout.source_end, boxes))

def _sampled_frames(layout, fps):
    frames = _keyframes(layout)
    if len(frames) < 2:
        return frames
    sampled = []
    step = 1.0 / max(1.0, float(fps or 25.0))
    for first, second in zip(frames, frames[1:]):
        span = max(0.0, second.time - first.time)
        count = max(1, int(math.ceil(span / step)))
        for number in range(count):
            u = number / count
            if len(first.boxes) != len(second.boxes):
                boxes = first.boxes
            else:
                boxes = tuple((tuple((a + (b - a) * u for a, b in zip(one, two))) for one, two in zip(first.boxes, second.boxes)))
            sampled.append(type(first)(first.time + span * u, boxes))
    sampled.append(frames[-1])
    return tuple(sampled)

def _envelope(composition, layout, panel, face, *, face_only=False):
    if face_only:
        return composition.padded_face(face)
    if layout.mode in {'grid3', 'grid4', 'grid4_context'}:
        return composition.padded_face(face)
    return composition.tracking_envelope(panel, face)

def _centered_path(composition, motion, media, panel, layout, base):
    width, height, points, fallback = base
    sampled = _sampled_frames(layout, media.fps)
    rows = []
    for frame in sampled:
        face = composition.subject_face(layout, panel, frame.boxes)
        if face is None:
            return None
        rows.append((frame.time, face))
    if not rows or width < panel.width or height < panel.height:
        return None
    deltas = []
    for axis, scaled, canvas, origin, span in ((0, width, panel.width, panel.safe[0], panel.safe[2]), (1, height, panel.height, panel.safe[1], panel.safe[3])):
        low, high = (-float('inf'), float('inf'))
        preferred = []
        target_fraction = 0.5 if axis == 0 else 0.36
        target = origin + span * target_fraction
        for timestamp, face in rows:
            offset = motion.value_at(points, timestamp, axis + 1)
            protected = _envelope(composition, layout, panel, face)
            low = max(low, origin - offset - protected[axis] * scaled, canvas - scaled - offset)
            high = min(high, origin + span - offset - (protected[axis] + protected[axis + 2]) * scaled, -offset)
            preferred.append(target - offset - (face[axis] + face[axis + 2] / 2) * scaled)
        if low > high:
            return None
        wanted = median(preferred)
        deltas.append(min(high, max(low, wanted)))
    shifted = tuple(((timestamp, round(x + deltas[0], 4), round(y + deltas[1], 4)) for timestamp, x, y in points))
    for timestamp, face in rows:
        protected = _envelope(composition, layout, panel, face)
        ox = motion.value_at(shifted, timestamp, 1)
        oy = motion.value_at(shifted, timestamp, 2)
        if ox + protected[0] * width < panel.safe[0] - 2 or oy + protected[1] * height < panel.safe[1] - 2 or ox + (protected[0] + protected[2]) * width > panel.safe[0] + panel.safe[2] + 2 or (oy + (protected[1] + protected[3]) * height > panel.safe[1] + panel.safe[3] + 2):
            return None
    if all((point[1:] == shifted[0][1:] for point in shifted)):
        shifted = ((layout.source_start, *shifted[0][1:]),)
    return (width, height, shifted, fallback)

def _sticky_face_axis(times, bounds, initial):
    if not bounds or any((low > high for low, high in bounds)):
        return None
    common_low = max((low for low, _ in bounds))
    common_high = min((high for _, high in bounds))
    if common_low <= common_high:
        fixed = min(common_high, max(common_low, initial))
        return [fixed] * len(times)
    values = [min(bounds[0][1], max(bounds[0][0], initial))]
    for low, high in bounds[1:]:
        current = values[-1]
        if low <= current <= high:
            values.append(current)
            continue
        guard = min(36.0, max(3.0, (high - low) * 0.25))
        values.append(min(high, low + guard) if current < low else max(low, high - guard))
    return values

def _face_priority_path(composition, motion, media, panel, layout, base):
    raw = []
    for frame in _keyframes(layout):
        face = composition.subject_face(layout, panel, frame.boxes)
        if face is not None:
            raw.append((frame.time, face))
    if len(raw) < 2:
        return None
    sampled = _sampled_frames(layout, media.fps)
    reference = (median((face[0] for _, face in raw)), median((face[1] for _, face in raw)), max((face[2] for _, face in raw)), max((face[3] for _, face in raw)))
    placed_width, placed_height, _, _ = composition.placement(media, panel, reference)
    fill = max(panel.width / media.width, panel.height / media.height)
    desired = max(fill, placed_width / media.width, placed_height / media.height)
    centre_x = reference[0] + reference[2] / 2
    if 0.001 < centre_x < 0.999:
        centre_width = max(panel.width * 0.5 / centre_x, panel.width * 0.5 / (1.0 - centre_x))
        desired = max(desired, min(centre_width / media.width, fill * 1.55))
    scales = []
    for attempt in range(20):
        scale = max(fill, desired * 0.94 ** attempt)
        if not scales or abs(scale - scales[-1]) > 0.0005:
            scales.append(scale)
    if not scales or scales[-1] > fill + 0.0005:
        scales.append(fill)
    times = [timestamp for timestamp, _ in raw]
    for full_panel in (False, True):
        safe = (0, 0, panel.width, panel.height) if full_panel else panel.safe
        for scale in scales:
            width = max(panel.width, int(media.width * scale) // 2 * 2)
            height = max(panel.height, int(media.height * scale) // 2 * 2)
            axes = []
            for axis, scaled, canvas, origin, span in ((0, width, panel.width, safe[0], safe[2]), (1, height, panel.height, safe[1], safe[3])):
                bounds = []
                for _, face in raw:
                    protected = _envelope(composition, layout, panel, face, face_only=True)
                    bounds.append((max(origin - protected[axis] * scaled, canvas - scaled), min(origin + span - (protected[axis] + protected[axis + 2]) * scaled, 0)))
                target = origin + span * (0.5 if axis == 0 else 0.36)
                initial = target - (reference[axis] + reference[axis + 2] / 2) * scaled
                values = _sticky_face_axis(times, bounds, initial)
                if values is None:
                    axes = []
                    break
                axes.append(values)
            if len(axes) != 2:
                continue
            points = tuple(((timestamp, round(x, 4), round(y, 4)) for timestamp, x, y in zip(times, *axes)))
            valid = True
            for frame in sampled:
                face = composition.subject_face(layout, panel, frame.boxes)
                if face is None:
                    valid = False
                    break
                protected = _envelope(composition, layout, panel, face, face_only=True)
                ox = motion.value_at(points, frame.time, 1)
                oy = motion.value_at(points, frame.time, 2)
                if ox + protected[0] * width < safe[0] - 2 or oy + protected[1] * height < safe[1] - 2 or ox + (protected[0] + protected[2]) * width > safe[0] + safe[2] + 2 or (oy + (protected[1] + protected[3]) * height > safe[1] + safe[3] + 2):
                    valid = False
                    break
            if valid:
                if all((point[1:] == points[0][1:] for point in points)):
                    points = ((layout.source_start, *points[0][1:]),)
                return (width, height, points, full_panel)
    return None

def _patch_camera():
    from . import composition, motion
    previous = motion.camera_path

    @lru_cache(maxsize=512)
    def camera_path(media, panel, layout):
        base = previous(media, panel, layout)
        geometry = composition.layout_geometry(layout, media)
        if geometry.diagonal or panel.members or panel.face_index < 0 or (layout.mode in {'grid3', 'grid4', 'grid4_context'}):
            return base
        centred = _centered_path(composition, motion, media, panel, layout, base)
        if centred is not None and (not base[3]):
            return centred
        face_safe = _face_priority_path(composition, motion, media, panel, layout, base)
        return face_safe or centred or base
    motion.camera_path = camera_path

def _inside(offset_x, offset_y, width, height, box, safe):
    x, y, w, h = box
    left = offset_x + x * width
    top = offset_y + y * height
    right = left + w * width
    bottom = top + h * height
    return left >= safe[0] - 2 and top >= safe[1] - 2 and (right <= safe[0] + safe[2] + 2) and (bottom <= safe[1] + safe[3] + 2)

def _patch_motion_qa():
    from . import composition, motion
    previous = motion.validate_motion

    def validate_motion(media, layout):
        geometry = composition.layout_geometry(layout, media)
        if geometry.diagonal or layout.mode in {'grid3', 'grid4', 'grid4_context', 'duo_context'}:
            return previous(media, layout)
        errors = []
        sampled = _sampled_frames(layout, media.fps)
        for panel in geometry.panels:
            if panel.face_index < 0 or panel.members:
                continue
            width, height, points, full_panel = motion.camera_path(media, panel, layout)
            safe = (0, 0, panel.width, panel.height) if full_panel else panel.safe
            for frame in sampled:
                face = composition.subject_face(layout, panel, frame.boxes)
                if face is None:
                    errors.append('planned panel lost its tracked speaker')
                    continue
                offset_x = motion.value_at(points, frame.time, 1)
                offset_y = motion.value_at(points, frame.time, 2)
                body = _envelope(composition, layout, panel, face)
                if _inside(offset_x, offset_y, width, height, body, safe):
                    continue
                head = _envelope(composition, layout, panel, face, face_only=True)
                if not _inside(offset_x, offset_y, width, height, head, safe):
                    errors.append('tracked speaker face leaves its safe lane')
        return list(dict.fromkeys(errors))
    motion.validate_motion = validate_motion

def _strict_solo_visibility(output, plan):
    rows = [(index, row) for index, row in enumerate(plan.layouts) if row.mode in {'focus', 'solo_medium', 'speaker_detail'} and row.required_faces == 1 and (row.source_end - row.source_start >= 0.8)]
    if not rows or not plan.analysis.get('enforce_visible_face_qa', False):
        return {'checked': False, 'segments': 0, 'failures': [], 'failed_layout_indexes': []}
    try:
        import cv2
        from .vision import FaceDetector
        from .timeline import map_source_time
    except Exception:
        return {'checked': False, 'segments': len(rows), 'failures': [], 'failed_layout_indexes': [], 'warning': 'OpenCV face-centre QA unavailable'}
    if len(rows) > 40:
        rows = [rows[round(i * (len(rows) - 1) / 39)] for i in range(40)]
    cap = cv2.VideoCapture(str(output))
    detector = FaceDetector(cv2)
    if getattr(detector, 'kind', 'safe_full_source') == 'safe_full_source':
        cap.release()
        return {'checked': False, 'segments': len(rows), 'failures': [], 'failed_layout_indexes': [], 'warning': 'YuNet face-centre QA unavailable'}
    failures, failed = ([], [])
    try:
        for occurrence, (layout_index, row) in enumerate(rows, 1):
            hits = checked = 0
            for number in range(7):
                source_time = row.source_start + (row.source_end - row.source_start) * (number + 0.5) / 7
                mapped = map_source_time(plan.spans, source_time)
                if mapped is None:
                    continue
                checked += 1
                cap.set(cv2.CAP_PROP_POS_MSEC, mapped * 1000.0)
                ok, frame = cap.read()
                if ok:
                    height, width = frame.shape[:2]
                    visible = detector.detect(frame)
                    if any((0.16 * width <= x + w * 0.5 <= 0.84 * width and 0.06 * height <= y + h * 0.5 <= 0.68 * height for x, y, w, h, _ in visible)):
                        hits += 1
            required = max(2, math.ceil(checked * 0.57))
            if checked and hits < required:
                failures.append(f'solo occurrence {occurrence} kept a safely framed face in only {hits}/{checked} sampled frames')
                failed.append(layout_index)
    finally:
        cap.release()
    return {'checked': True, 'segments': len(rows), 'failures': failures, 'failed_layout_indexes': failed, 'policy': 'majority face presence required for every sampled solo shot'}

def _patch_output_qa():
    from . import engine, qa
    previous = engine.inspect_output

    def inspect(output, plan):
        result = previous(output, plan)
        strict = _strict_solo_visibility(output, plan)
        if strict.get('warning'):
            result.setdefault('warnings', []).append(strict['warning'])
        if strict.get('checked'):
            old = result.get('visible_face_qa') or {}
            failures = list(dict.fromkeys(list(old.get('failures') or []) + list(strict.get('failures') or [])))
            indexes = sorted(set(list(old.get('failed_layout_indexes') or []) + list(strict.get('failed_layout_indexes') or [])))
            result['visible_face_qa'] = {**old, 'checked': True, 'strict_solo': strict, 'failures': failures, 'failed_layout_indexes': indexes}
            for failure in strict.get('failures') or []:
                if failure not in result['errors']:
                    result['errors'].append(failure)
            result['passed'] = not result['errors']
        return result
    qa.inspect_output = inspect
    engine.inspect_output = inspect

    def assert_output(output, plan):
        from .utils import CommandError
        result = inspect(output, plan)
        if not result['passed']:
            raise CommandError('Final Master Editor QA failed: ' + '; '.join(result['errors'][:5]))
        return result
    qa.assert_output = assert_output
    engine.assert_output = assert_output

def _patch_engine():
    from . import engine
    previous_build = engine.MasterEngine._build_plan
    previous_run = engine.MasterEngine.run_job

    def build(self, *args, **kwargs):
        plan = previous_build(self, *args, **kwargs)
        plan.analysis.update({'engine_version': '7.7.0', 'solo_camera_policy': 'body-safe fixed crop when feasible; face-priority fixed crop or zero-delay boundary correction when body envelope cannot fit', 'solo_face_anchor': 'horizontal centre; upper-third vertical target', 'solo_output_qa': 'seven samples per eligible solo shot; safely framed face required in a majority'})
        return plan

    def run(self, *args, **kwargs):
        result = previous_run(self, *args, **kwargs)
        try:
            import json
            report = Path(result['report'])
            data = json.loads(report.read_text(encoding='utf-8'))
            data['engine'] = 'Master Editor 7.7.0'
            data.setdefault('analysis', {})['engine_version'] = '7.7.0'
            report.write_text(json.dumps(data, indent=2), encoding='utf-8')
        except Exception:
            pass
        return result
    engine.MasterEngine._build_plan = build
    engine.MasterEngine.run_job = run

def install():
    global _installed
    if _installed:
        return
    _installed = True
    _patch_camera()
    _patch_motion_qa()
    _patch_output_qa()
    _patch_engine()

def _projected_face(composition, motion, media, layout, face, at):
    panel = composition.layout_geometry(layout, media).panels[0]
    width, height, points, fallback = motion.camera_path(media, panel, layout)
    return (motion.value_at(points, at, 1) + (face[0] + face[2] / 2) * width, motion.value_at(points, at, 2) + (face[1] + face[3] / 2) * height, panel, points, fallback)

def check_v77(*, real=True):
    from . import composition, motion
    from .hotfix_v73 import _mark
    from .models import FaceKeyframe, LayoutSegment, MediaInfo
    media = MediaInfo(Path('v77-check.mp4'), 4.0, 3840, 2160, 25.0, True)
    centred_box = (0.41, 0.12, 0.18, 0.28)
    stationary = LayoutSegment(0.0, 4.0, 'focus', required_faces=1, face_boxes=(centred_box,), face_keyframes=(FaceKeyframe(0.0, (centred_box,)), FaceKeyframe(4.0, (centred_box,))), reason=_mark('V7.7 fixed centre regression', '9:16'))
    face_x, face_y, panel, points, _ = _projected_face(composition, motion, media, stationary, centred_box, 0.0)
    if len(points) != 1:
        raise AssertionError('stationary centred speaker did not receive one fixed crop')
    if not panel.safe[0] + panel.safe[2] * 0.34 <= face_x <= panel.safe[0] + panel.safe[2] * 0.66:
        raise AssertionError('stationary speaker is not horizontally centred')
    if not panel.safe[1] + panel.safe[3] * 0.08 <= face_y <= panel.safe[1] + panel.safe[3] * 0.58:
        raise AssertionError('stationary speaker face is outside the upper anchor')
    edge_box = (0.75, 0.015, 0.18, 0.34)
    edge = LayoutSegment(0.0, 4.0, 'focus', required_faces=1, face_boxes=(edge_box,), face_keyframes=(FaceKeyframe(0.0, (edge_box,)), FaceKeyframe(4.0, (edge_box,))), reason=_mark('V7.7 supplied off-screen regression', '9:16'))
    edge_x, edge_y, edge_panel, edge_points, _ = _projected_face(composition, motion, media, edge, edge_box, 0.0)
    if len(edge_points) != 1:
        raise AssertionError('stationary edge speaker caused camera drift')
    if not (edge_panel.width * 0.24 <= edge_x <= edge_panel.width * 0.76 and edge_panel.height * 0.04 <= edge_y <= edge_panel.height * 0.58):
        raise AssertionError('supplied off-screen speaker regression was not centred')
    failures = composition.validate_geometry(media, edge)
    if failures:
        raise AssertionError('off-screen regression geometry: ' + '; '.join(failures))
    edge_34 = LayoutSegment(0.0, 4.0, 'focus', required_faces=1, face_boxes=(edge_box,), face_keyframes=edge.face_keyframes, reason=_mark('V7.7 3:4 edge regression', '3:4'))
    edge_34_x, _, edge_34_panel, edge_34_points, _ = _projected_face(composition, motion, media, edge_34, edge_box, 0.0)
    if edge_34_panel.height != 1440 or len(edge_34_points) != 1 or (not edge_34_panel.width * 0.24 <= edge_34_x <= edge_34_panel.width * 0.76):
        raise AssertionError('3:4 edge speaker did not keep its fixed safe crop')
    left = (0.04, 0.12, 0.15, 0.24)
    right = (0.81, 0.12, 0.15, 0.24)
    moving = LayoutSegment(0.0, 4.0, 'focus', required_faces=1, face_boxes=(left,), face_keyframes=(FaceKeyframe(0.0, (left,)), FaceKeyframe(1.8, (left,)), FaceKeyframe(2.2, (right,)), FaceKeyframe(4.0, (right,))), reason=_mark('V7.7 zero-delay crossing regression', '9:16'))
    moving_panel = composition.layout_geometry(moving, media).panels[0]
    _, _, moving_points, _ = motion.camera_path(media, moving_panel, moving)
    if len(moving_points) < 2:
        raise AssertionError('boundary-crossing face did not get a moving crop')
    failures = composition.validate_geometry(media, moving)
    if failures:
        raise AssertionError('moving speaker geometry: ' + '; '.join(failures))
    result = {'ok': True, 'version': '7.7.0', 'supplied_offscreen_regression': 'face safely centred', 'stationary_camera': 'one fixed point', 'moving_camera': 'zero-delay face-safe path', 'moving_points': len(moving_points), 'ratio_3_4_focus': 'fixed face-safe crop', 'real_9_16_render': False}
    if not real:
        return result
    import tempfile
    from types import SimpleNamespace
    from .config import Settings, load_caption_styles
    from .director import PROFILES
    from .media import probe
    from .models import EditPlan, SourceSpan, Transcript, Word
    from .qa import assert_output
    from .render import render
    from .render_runtime import FFmpegRuntime
    with tempfile.TemporaryDirectory(prefix='master_v77_focus_') as directory:
        root = Path(directory)
        source = root / 'source.mp4'
        duration = 2.4
        stub = SimpleNamespace(analysis={'engine_version': '7.7.0'})
        with FFmpegRuntime(stub) as runtime:
            runtime.execute(lambda: runtime.prefix() + ['-f', 'lavfi', '-i', f'testsrc2=size=960x540:rate=25:duration={duration}', '-f', 'lavfi', '-i', f'sine=frequency=440:sample_rate=48000:duration={duration}', '-c:v', 'libx264', '-preset', 'fast', '-crf', '14', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-ar', '48000'] + runtime.encoder_args() + ['-t', str(duration), str(source)], stage='V7.7 focus source', timeout=90)
        source_media = probe(source)
        frames = (FaceKeyframe(0.0, (left,)), FaceKeyframe(0.95, (left,)), FaceKeyframe(1.35, (right,)), FaceKeyframe(duration, (right,)))
        layout = LayoutSegment(0.0, duration, 'focus', required_faces=1, face_boxes=(left,), face_keyframes=frames, reason=_mark('V7.7 real moving-focus render', '9:16'))
        plan = EditPlan(source_media, Transcript([Word(0.02, 1.15, 'FACE'), Word(1.18, 2.3, 'SAFE')], 'FACE SAFE', 'en'), PROFILES['podcast_interview'], load_caption_styles()['reference_bold_lime'], [SourceSpan(0.0, duration, 0.0)], [layout], [], None, duration, analysis={'render_fps': 25.0, 'settings_fps': 25.0, 'engine_version': '7.7.0', 'frame_family': '9:16', 'output_width': 1080, 'output_height': 1920, 'enforce_visible_face_qa': False})
        output, clean = (root / 'output.mp4', root / 'clean.mp4')
        render(plan, output, root / 'render', Settings(height=1920, fps=25, preset='fast', crf=14), clean_output=clean)
        review = assert_output(output, plan)
        clean_review = assert_output(clean, plan)
        saved_graphs = '\n'.join((path.read_text(encoding='utf-8') for path in (root / 'render').rglob('*.filters.txt')))
        live_graph = '\n'.join(composition.render_geometry('vin', 'vout', layout, source_media, 25.0, 77))
        if '(t+' not in live_graph:
            raise AssertionError('moving focus rendered without live camera path')
        if 'gblur=' in live_graph or 'gblur=' in saved_graphs:
            raise AssertionError('solo focus introduced a blur-backed frame')
        runtime_data = dict(plan.analysis.get('ffmpeg_runtime') or {})
        if runtime_data.get('native_crash_retries') != 0:
            raise AssertionError('V7.7 focus render required an FFmpeg crash retry')
        result.update({'real_9_16_render': True, 'resolution': [review['output']['width'], review['output']['height']], 'clean_resolution': [clean_review['output']['width'], clean_review['output']['height']], 'full_decode_verified': review.get('full_decode_verified', False), 'blur_filters': 0, 'native_crash_retries': 0})
    return result
