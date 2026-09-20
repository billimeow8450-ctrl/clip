from __future__ import annotations
from collections import Counter
from dataclasses import replace
from functools import lru_cache
import json
import math
from pathlib import Path
from statistics import median
_installed = False
PAIR_MODES = {'duo_context', 'split_reaction', 'diagonal_split', 'cinema_duo', 'speaker_context', 'listener_context', 'horizontal_split', 'offset_duo'}

def _centres(boxes):
    return tuple(((x + w / 2, y + h / 2) for x, y, w, h in boxes))

def _keyframes(row):
    from .models import FaceKeyframe
    if row.face_keyframes:
        return tuple(row.face_keyframes)
    boxes = tuple(row.face_boxes)
    return (FaceKeyframe(row.source_start, boxes), FaceKeyframe(row.source_end, boxes))

def _pair_motion(row):
    frames = [frame for frame in _keyframes(row) if len(frame.boxes) == 2]
    if not frames:
        return 1.0
    movement = 0.0
    for index in range(2):
        centres = [(frame.boxes[index][0] + frame.boxes[index][2] / 2, frame.boxes[index][1] + frame.boxes[index][3] / 2) for frame in frames]
        movement = max(movement, max((x for x, _ in centres)) - min((x for x, _ in centres)), max((y for _, y in centres)) - min((y for _, y in centres)))
    return movement

def _pair_separation(row):
    values = []
    for frame in _keyframes(row):
        if len(frame.boxes) != 2:
            continue
        a, b = frame.boxes
        values.append(abs(a[0] + a[2] / 2 - (b[0] + b[2] / 2)))
    return median(values) if values else 0.0

def _beat_at_entry(plan, row):
    candidates = [beat for beat in plan.story_beats if row.source_start - 0.3 <= beat.start <= min(row.source_end, row.source_start + 0.85)]
    if not candidates:
        return None
    return max(candidates, key=lambda beat: beat.importance)

def _layout_errors(media, row):
    from . import composition as c
    from . import motion
    errors = list(c.validate_geometry(media, row))
    if row.face_boxes and (not row.face_keyframes):
        errors.extend(motion.validate_motion(media, row))
    return list(dict.fromkeys(errors))

def _patch_distinct_geometry():
    from . import hotfix_v73 as v73
    original = v73._geometry_for

    def geometry_for(c, mode, active_side, face_count, panel_members, height):
        if face_count < 2:
            return original(c, mode, active_side, face_count, panel_members, height)
        lead = max(0, face_count - 1) if active_side == 'right' else 0
        other = 0 if lead else max(0, face_count - 1)

        def panel(y, panel_height, face, fraction=0.35, members=()):
            return v73._panel(c, 0, y, v73.W, panel_height, face, fraction=fraction, members=members)
        if mode == 'diagonal_split' and face_count == 2:
            margin = 18
            left, right = (48, v73.W - 48)
            top_y = max(28, round(height * 0.024))
            top_bottom = math.floor(0.62 * height - 0.2 * right - margin)
            bottom_y = math.ceil(0.62 * height - 0.2 * left + margin)
            bottom_bottom = height - max(24, round(height * 0.018))
            if top_bottom <= top_y or bottom_bottom <= bottom_y:
                return original(c, mode, active_side, face_count, panel_members, height)
            return c.Geometry((c.Panel(0, 0, v73.W, height, lead, (left, top_y, right - left, top_bottom - top_y), face_fraction=0.27), c.Panel(0, 0, v73.W, height, other, (left, bottom_y, right - left, bottom_bottom - bottom_y), face_fraction=0.27)), round(height * 0.53), True)
        if mode == 'duo_context' and face_count == 2:
            members = tuple(range(2))
            return c.Geometry((panel(0, height, -3, 0.25, members),), round(height * 0.7))
        if mode == 'speaker_context' and face_count == 2:
            cut = round(height * 0.6)
            return c.Geometry((panel(0, cut, lead, 0.34), panel(cut, height - cut, other, 0.39)), cut)
        if mode == 'listener_context' and face_count == 2:
            cut = round(height * 0.42)
            return c.Geometry((panel(0, cut, lead, 0.39), panel(cut, height - cut, other, 0.34)), cut)
        if mode == 'cinema_duo' and face_count == 2:
            cut = round(height * 0.55)
            return c.Geometry((panel(0, cut, lead, 0.34), panel(cut, height - cut, other, 0.36)), cut)
        if mode == 'offset_duo' and face_count == 2:
            cut = round(height * 0.47)
            return c.Geometry((panel(0, cut, lead, 0.37), panel(cut, height - cut, other, 0.34)), cut)
        return original(c, mode, active_side, face_count, panel_members, height)
    v73._geometry_for = geometry_for

def _patch_frozen_diagonal():
    from . import composition as c
    from . import hotfix_v73 as v73
    from . import motion
    original_camera = motion.camera_path

    def safe_sticky_axis(times, bounds, initial, speed, smoothing):
        common_low = max((bound[0] for bound in bounds))
        common_high = min((bound[1] for bound in bounds))
        if common_low <= common_high:
            fixed = min(common_high, max(common_low, initial))
            return [fixed] * len(times)
        if any((low > high for low, high in bounds)):
            return None
        points = [min(bounds[0][1], max(bounds[0][0], initial))]
        for index in range(1, len(times)):
            low, high = bounds[index]
            current = points[-1]
            if low <= current <= high:
                points.append(current)
                continue
            guard = min(max(40.0, smoothing * 240.0), max(3.0, (high - low) * 0.42))
            inner_low = min(high, low + guard)
            inner_high = max(low, high - guard)
            target = inner_low if current < low else inner_high
            travel = speed * max(0.001, times[index] - times[index - 1])
            if abs(target - current) > travel + 0.001:
                return None
            points.append(target)
        return points
    motion._sticky_axis = safe_sticky_axis
    if hasattr(original_camera, 'cache_clear'):
        original_camera.cache_clear()

    def linear_expression(points, index, source_time, offset=0, source_end=None):
        if source_end is not None:
            points = ((source_time, motion.value_at(points, source_time, 1), motion.value_at(points, source_time, 2)),) + tuple((point for point in points if source_time < point[0] < source_end)) + ((source_end, motion.value_at(points, source_end, 1), motion.value_at(points, source_end, 2)),)
        if len(points) < 2 or max((point[index] for point in points)) - min((point[index] for point in points)) < 0.15:
            return f'{points[0][index] + offset:.3f}'
        clock = f'(t+{source_time:.6f})'
        pieces = []
        for first, second in zip(points, points[1:]):
            delta = second[index] - first[index]
            if abs(delta) < 0.001:
                continue
            u = f'clip(({clock}-{first[0]:.6f})/{max(1e-06, second[0] - first[0]):.6f},0,1)'
            pieces.append(f'({delta:.6f}*({u}))')

        def balanced(parts):
            if not parts:
                return '0'
            if len(parts) == 1:
                return parts[0]
            middle = len(parts) // 2
            return '(' + balanced(parts[:middle]) + '+' + balanced(parts[middle:]) + ')'
        return f'({points[0][index] + offset:.3f}+{balanced(pieces)})'

    def interpolated_frames(layout, fps):
        frames = list(_keyframes(layout))
        if len(frames) < 2:
            return frames
        sampled = []
        step = 1.0 / max(1.0, fps)
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
        return sampled

    def path_envelope(layout, panel, box):
        if panel.members:
            return box
        if layout.mode in {'grid3', 'grid4', 'grid4_context'}:
            return c.padded_face(box)
        return c.tracking_envelope(panel, box)

    @lru_cache(maxsize=512)
    def camera_path(media, panel, layout):
        geometry = c.layout_geometry(layout, media)
        if layout.mode == 'duo_context' and panel.members:
            observed = []
            for frame in _keyframes(layout):
                box = c.subject_face(layout, panel, frame.boxes)
                if box is not None:
                    observed.append((frame.time, box))
            scale = min(panel.width / media.width, panel.height / media.height)
            width = max(2, int(media.width * scale) // 2 * 2)
            height = max(2, int(media.height * scale) // 2 * 2)
            sx, sy, safe_width, safe_height = panel.safe
            axes = []
            for axis, scaled, origin, span in ((0, width, sx, safe_width), (1, height, sy, safe_height)):
                bounds = []
                for _, box in observed:
                    protected = path_envelope(layout, panel, box)
                    bounds.append((origin - protected[axis] * scaled, origin + span - (protected[axis] + protected[axis + 2]) * scaled))
                if not bounds:
                    break
                low = max((value[0] for value in bounds))
                high = min((value[1] for value in bounds))
                if low > high:
                    break
                preferred = ((panel.width if axis == 0 else panel.height) - scaled) / 2
                axes.append(min(high, max(low, preferred)))
            if len(axes) == 2:
                return (width, height, ((layout.source_start, round(axes[0], 4), round(axes[1], 4)),), False)
        if layout.mode != 'diagonal_split' or not geometry.diagonal:
            if panel.face_index in {-1, -4}:
                return original_camera(media, panel, layout)
            observed = [(frame.time, c.subject_face(layout, panel, frame.boxes)) for frame in _keyframes(layout)]
            observed = [(timestamp, box) for timestamp, box in observed if box is not None]
            if len(observed) < 2:
                return original_camera(media, panel, layout)
            representative = (median((box[0] for _, box in observed)), median((box[1] for _, box in observed)), max((box[2] for _, box in observed)), max((box[3] for _, box in observed)))
            placed_width, placed_height, _, _ = c.placement(media, panel, representative)
            start_scale = max(placed_width / media.width, placed_height / media.height)
            fill_scale = max(panel.width / media.width, panel.height / media.height)
            times = [timestamp for timestamp, _ in observed]
            sampled = interpolated_frames(layout, media.fps)
            smoothing, speed = motion.profile_values(layout.motion_profile)

            def build(scale, full_panel):
                width = max(panel.width, int(media.width * scale) // 2 * 2)
                height = max(panel.height, int(media.height * scale) // 2 * 2)
                if full_panel:
                    safe_x, safe_y, safe_w, safe_h = (0, 0, panel.width, panel.height)
                else:
                    safe_x, safe_y, safe_w, safe_h = panel.safe
                axes = []
                for axis, scaled, canvas, origin, span in ((0, width, panel.width, safe_x, safe_w), (1, height, panel.height, safe_y, safe_h)):
                    bounds = []
                    for _, box in observed:
                        protected = path_envelope(layout, panel, box)
                        low = max(origin - protected[axis] * scaled, canvas - scaled)
                        high = min(origin + span - (protected[axis] + protected[axis + 2]) * scaled, 0)
                        bounds.append((low, high))
                    if any((low > high for low, high in bounds)):
                        return None
                    protected = path_envelope(layout, panel, observed[0][1])
                    initial = origin + span / 2 - (protected[axis] + protected[axis + 2] / 2) * scaled
                    values = safe_sticky_axis(times, bounds, initial, speed / math.sqrt(2), smoothing)
                    if values is None:
                        return None
                    axes.append(values)
                points = tuple(((timestamp, round(x, 4), round(y, 4)) for timestamp, x, y in zip(times, *axes)))
                if all((point[1:] == points[0][1:] for point in points)):
                    points = ((layout.source_start, *points[0][1:]),)
                for frame in sampled:
                    box = c.subject_face(layout, panel, frame.boxes)
                    if box is None:
                        return None
                    x, y, box_w, box_h = path_envelope(layout, panel, box)
                    offset_x = motion.value_at(points, frame.time, 1)
                    offset_y = motion.value_at(points, frame.time, 2)
                    if offset_x + x * width < safe_x - 2 or offset_y + y * height < safe_y - 2 or offset_x + (x + box_w) * width > safe_x + safe_w + 2 or (offset_y + (y + box_h) * height > safe_y + safe_h + 2):
                        return None
                return (width, height, points, full_panel)
            scales = []
            for attempt in range(18):
                scale = max(fill_scale, start_scale * 0.92 ** attempt)
                if not scales or abs(scale - scales[-1]) > 0.0005:
                    scales.append(scale)
            if not scales or scales[-1] > fill_scale + 0.0005:
                scales.append(fill_scale)
            for full_panel in (False, True):
                for scale in scales:
                    candidate = build(scale, full_panel)
                    if candidate is not None:
                        return candidate
            return original_camera(media, panel, layout)
        observed = []
        for frame in _keyframes(layout):
            box = c.subject_face(layout, panel, frame.boxes)
            if box is not None:
                observed.append((frame.time, box))
        if not observed:
            width, height, x, y = c.placement(media, panel, c.subject_face(layout, panel))
            return (width, height, ((layout.source_start, float(x), float(y)),), True)
        x1 = min((box[0] for _, box in observed))
        y1 = min((box[1] for _, box in observed))
        x2 = max((box[0] + box[2] for _, box in observed))
        y2 = max((box[1] + box[3] for _, box in observed))
        stable = (x1, y1, x2 - x1, y2 - y1)
        width, height, initial_x, initial_y = c.placement(media, panel, stable)
        start_scale = max(width / media.width, height / media.height)
        fill_scale = max(panel.width / media.width, panel.height / media.height)
        sx, sy, safe_width, safe_height = panel.safe
        for attempt in range(18):
            scale = max(fill_scale, start_scale * 0.95 ** attempt)
            width = max(panel.width, int(media.width * scale) // 2 * 2)
            height = max(panel.height, int(media.height * scale) // 2 * 2)
            axes = []
            for axis, scaled, canvas, origin, span in ((0, width, panel.width, sx, safe_width), (1, height, panel.height, sy, safe_height)):
                bounds = []
                for _, box in observed:
                    protected = path_envelope(layout, panel, box)
                    low = origin - protected[axis] * scaled
                    high = origin + span - (protected[axis] + protected[axis + 2]) * scaled
                    bounds.append((low, high))
                low = max((value[0] for value in bounds))
                high = min((value[1] for value in bounds))
                if low > high:
                    axes = []
                    break
                protected = path_envelope(layout, panel, stable)
                preferred = origin + span / 2 - (protected[axis] + protected[axis + 2] / 2) * scaled
                axes.append(min(high, max(low, preferred)))
            if len(axes) == 2:
                return (width, height, ((layout.source_start, round(axes[0], 4), round(axes[1], 4)),), False)
        return (width, height, ((layout.source_start, float(initial_x), float(initial_y)),), True)

    def validate_motion(media, layout):
        errors = []
        geometry = c.layout_geometry(layout, media)
        canvas_height = v73._height(layout)
        sampled = interpolated_frames(layout, media.fps)
        for number, panel in enumerate(geometry.panels):
            if panel.face_index in {-1, -4}:
                continue
            width, height, points, fallback = camera_path(media, panel, layout)
            if layout.mode == 'diagonal_split' and (fallback or len(points) != 1):
                errors.append('diagonal camera is not one fixed face-safe crop')
            sx, sy, sw, sh = (0, 0, panel.width, panel.height) if fallback else panel.safe
            for frame in sampled:
                box = c.subject_face(layout, panel, frame.boxes)
                if box is None:
                    errors.append('planned panel lost its tracked person')
                    continue
                x, y, w, h = path_envelope(layout, panel, box)
                offset_x = motion.value_at(points, frame.time, 1)
                offset_y = motion.value_at(points, frame.time, 2)
                left = offset_x + x * width
                top = offset_y + y * height
                right = left + w * width
                bottom = top + h * height
                if left < sx - 2 or top < sy - 2 or right > sx + sw + 2 or (bottom > sy + sh + 2):
                    errors.append('tracked speaker leaves the visible frame path')
                if layout.mode != 'diagonal_split':
                    continue
                for px, py in ((left, top), (right, top), (left, bottom), (right, bottom)):
                    line = 0.62 * canvas_height - 0.2 * px
                    if number == 0 and py > line - 8 or (number == 1 and py < line + 8):
                        errors.append('diagonal tracked person intersects its divider')
        return list(dict.fromkeys(errors))
    motion.camera_path = camera_path
    motion.expression = linear_expression
    motion.validate_motion = validate_motion

def _pair_choice(plan, row):
    from . import composition as c
    duration = row.source_end - row.source_start
    if duration <= 2.001:
        return ('fit_blur', 'verified two-person moment <=2.00s')
    if duration <= 3.001:
        return ('duo_context', 'brief two-person exchange kept in one sharp group frame')
    beat = _beat_at_entry(plan, row)
    kind = beat.kind if beat and beat.importance >= 0.55 else ''
    motion = _pair_motion(row)
    separation = _pair_separation(row)
    if duration >= 4.0 and kind in {'question', 'hook'} and (separation >= 0.25) and (motion <= 0.055):
        candidate = replace(row, mode='diagonal_split', active_side=None)
        if not _layout_errors(plan.media, candidate):
            return ('diagonal_split', f'{kind} beat; stable separated pair; frozen diagonal passed full-path QA')
    variants = {'evidence': ('cinema_duo', 'number/evidence beat; measured 55/45 context'), 'turn': ('offset_duo', 'contrast/turn beat; measured 47/53 context'), 'hook': ('speaker_context', 'hook beat; stable 60/40 context')}
    if duration >= 5.0 and kind in variants and (motion <= 0.08):
        mode, explanation = variants[kind]
        candidate = replace(row, mode=mode, active_side=None)
        if not _layout_errors(plan.media, candidate):
            return (mode, explanation)
    return ('split_reaction', 'default long-dialogue top/bottom split; safer than diagonal for this face path')

def _patch_content_planner():
    from . import composition as c
    from . import hotfix_v73 as v73
    from . import hotfix_v74 as v74
    from .subjects import four_panel_members
    from .models import LayoutSegment

    def normalise(plan, family, native):
        marker = lambda reason: v73._mark(reason, family, native)
        if native:
            return [LayoutSegment(0.0, plan.media.duration, 'passthrough', required_faces=0, reason=marker('native portrait source preserved edge-to-edge'))]
        planned = []
        for source in plan.layouts:
            count = max(source.required_faces, len(source.face_boxes))
            duration = source.source_end - source.source_start
            boxes = tuple(source.face_boxes)
            members = ()
            side = None
            if count <= 0 or not boxes:
                mode, count, boxes = ('passthrough', 0, ())
                decision = 'detector uncertain; complete sharp source retained'
            elif count == 1:
                mode = 'focus'
                decision = 'one verified person; stable full-frame portrait'
            elif count == 2:
                draft = replace(source, active_side=None, required_faces=2, face_boxes=boxes, face_centers=_centres(boxes), reason=marker(source.reason))
                mode, decision = _pair_choice(plan, draft)
            elif count == 3:
                mode, decision = ('grid3', 'three verified people; stable three-panel grid')
            elif count == 4:
                mode, decision = ('grid4', 'four verified people; stable four-panel grid')
            else:
                mode = 'grid4_context'
                members = four_panel_members(boxes, plan.media.aspect)
                decision = f'{count} verified people grouped into four stable panels'
            row = replace(source, mode=mode, active_side=side, required_faces=count, face_boxes=boxes, face_centers=_centres(boxes), panel_face_indexes=members, caption_y=0, reason=marker(source.reason + '; V7.6 decision: ' + decision))
            failures = _layout_errors(plan.media, row)
            if failures:
                fallback = 'fit_blur' if count >= 2 and duration <= 2.001 else 'grid4_context' if count > 4 else 'grid4' if count == 4 else 'grid3' if count == 3 else 'duo_context' if count == 2 else 'focus' if count == 1 else 'passthrough'
                fallback_members = four_panel_members(boxes, plan.media.aspect) if fallback == 'grid4_context' else ()
                row = replace(row, mode=fallback, active_side=None, panel_face_indexes=fallback_members, reason=marker(source.reason + '; V7.6 face-safe fallback from ' + mode + ': ' + '; '.join(failures)))
                if _layout_errors(plan.media, row):
                    row = replace(row, mode='passthrough', required_faces=0, face_boxes=(), face_centers=(), face_keyframes=(), panel_face_indexes=(), reason=marker('crop uncertain; complete sharp source retained'))
            planned.append(row)
        held = []
        mode_since = planned[0].source_start if planned else 0.0
        previous = None
        for row in planned:
            if previous and row.mode != previous.mode:
                same_population = row.required_faces == previous.required_faces
                elapsed = row.source_start - mode_since
                permitted = row.native_cut or not same_population or previous.mode == 'fit_blur' or (previous.mode in {'duo_context', 'speaker_context'} and row.turn_seconds > 3 and (elapsed >= 0.64))
                if same_population and elapsed < 3.0 and (not permitted):
                    candidate = replace(row, mode=previous.mode, active_side=None, reason=row.reason + '; retained prior layout to prevent flicker')
                    if not _layout_errors(plan.media, candidate):
                        row = candidate
                if row.mode != previous.mode:
                    mode_since = row.source_start
            held.append(row)
            previous = row
        merged = v74._merge_pair_rows(held)
        duration_safe = []
        for row in merged:
            duration = row.source_end - row.source_start
            if row.required_faces == 2 and row.mode == 'duo_context' and (duration > 3.001):
                mode, decision = _pair_choice(plan, row)
                candidate = replace(row, mode=mode, active_side=None, reason=marker(row.reason + '; V7.6 merged-duration decision: ' + decision))
                if _layout_errors(plan.media, candidate):
                    candidate = replace(candidate, mode='split_reaction', active_side=None, reason=marker(row.reason + '; merged brief shots became long dialogue; top/bottom safety fallback'))
                row = candidate
            duration_safe.append(row)
        return v74._merge_pair_rows(duration_safe)
    v73._normalise_layouts = normalise

def _audit_plan(plan):
    from . import composition as c
    from . import hotfix_v73 as v73
    from . import motion
    errors = []
    if not plan.layouts:
        return ['layout plan is empty']
    family = str(plan.analysis.get('frame_family') or '')
    marker = 'output-family=3:4' if family == '3:4' else 'output-family=9:16'
    previous_end = plan.layouts[0].source_start
    for row in plan.layouts:
        count = row.required_faces
        duration = row.source_end - row.source_start
        if abs(row.source_start - previous_end) > 0.08:
            errors.append('layout plan has a gap or overlap')
        previous_end = row.source_end
        if marker not in row.reason:
            errors.append('a shot escaped the job-wide ratio lock')
        if count == 1 and row.mode != 'focus':
            errors.append('solo shot is not full-frame focus')
        if count >= 2 and duration <= 2.001 and (row.mode != 'fit_blur'):
            errors.append('short verified multi-person shot missed the blur layout')
        if row.mode == 'fit_blur' and (count < 2 or duration > 2.001):
            errors.append('blur layout escaped its exact <=2 second condition')
        if count == 2 and duration > 3.001 and (row.mode not in {'split_reaction', 'diagonal_split', 'cinema_duo', 'speaker_context', 'listener_context'}):
            errors.append('long two-person dialogue lacks a deliberate split')
        errors.extend(_layout_errors(plan.media, row))
        if row.mode == 'diagonal_split':
            for panel in c.layout_geometry(row, plan.media).panels:
                _, _, points, fallback = motion.camera_path(plan.media, panel, row)
                if fallback or len(points) != 1:
                    errors.append('diagonal is not completely frozen')
    return list(dict.fromkeys(errors))

def _patch_engine():
    from . import engine as eng
    original_build = eng.MasterEngine._build_plan
    original_job = eng.MasterEngine.run_job

    def build(self, *args, **kwargs):
        plan = original_build(self, *args, **kwargs)
        failures = _audit_plan(plan)
        if failures:
            raise eng.MasterEngineError('V7.6 pre-render layout plan failed: ' + '; '.join(failures[:8]))
        counts = Counter((row.mode for row in plan.layouts))
        shot_map = [{'start': round(row.source_start, 3), 'end': round(row.source_end, 3), 'faces': row.required_faces, 'layout': row.mode, 'camera': 'fixed' if row.mode == 'diagonal_split' else 'sticky-safe-lane', 'decision': row.reason.split('V7.6 decision:', 1)[-1].strip()} for row in plan.layouts]
        plan.analysis.update({'engine_version': '7.6.0', 'layout_plan_validated_before_render': True, 'layout_plan': shot_map, 'layout_mode_counts': dict(counts), 'pair_layout_policy': '<=2s blur; 2.01-3s sharp group; >3s top-bottom by default; diagonal only for stable semantic beats passing fixed-crop QA', 'diagonal_camera_policy': 'one frozen crop for the complete segment', 'camera_policy': 'one frozen crop while protected speaker remains inside lane; continuous zero-lag correction from the measured boundary interval', 'camera_full_path_qa': 'every output-frame interpolation checked before FFmpeg rendering', 'diagonal_selection': 'semantic event + stable motion + full-path polygon clearance; wide spacing alone is insufficient', 'multi_person_layout_policy': '3=grid3; 4=grid4; 5+=four grouped stable panels', 'blur_policy': 'verified 2+ people only, exact segment duration <=2.00s', 'caption_policy': 'V7.4 controlled readable fixed base size and 100-104-100 pulse retained', 'ratio_lock_revalidated': True})
        progress = kwargs.get('progress')
        if progress is not None:
            eng.emit(progress, 'director', 1.0, f'Pre-render plan locked: {len(shot_map)} shots; ' + ', '.join((f'{name}={value}' for name, value in sorted(counts.items()))))
        return plan

    def run_job(self, input_path, output_path, **kwargs):
        result = original_job(self, input_path, output_path, **kwargs)
        report = Path(result['report'])
        if report.is_file():
            data = json.loads(report.read_text(encoding='utf-8'))
            data['engine'] = 'Master Editor 7.6.0'
            data.setdefault('analysis', {})['engine_version'] = '7.6.0'
            eng.write_json(report, data)
        return result
    eng.MasterEngine._build_plan = build
    eng.MasterEngine.run_job = run_job

def install():
    global _installed
    if _installed:
        return
    _installed = True
    _patch_distinct_geometry()
    _patch_frozen_diagonal()
    _patch_content_planner()
    _patch_engine()

def check_v76(*, real=True):
    import tempfile
    from types import SimpleNamespace
    from . import composition as c
    from . import hotfix_v73 as v73
    from . import motion
    from .config import Settings, load_caption_styles
    from .director import PROFILES
    from .media import probe
    from .models import EditPlan, FaceKeyframe, LayoutSegment, MediaInfo, SourceSpan, StoryBeat, Transcript, Word
    from .qa import assert_output
    from .render import render
    from .render_runtime import FFmpegRuntime
    media = MediaInfo(Path('v76-check.mp4'), 12.0, 1920, 1080, 25.0, True)
    boxes = ((0.08, 0.18, 0.15, 0.27), (0.76, 0.19, 0.15, 0.27))
    safe_boxes = ((0.1, 0.2, 0.08, 0.1), (0.82, 0.2, 0.08, 0.1))

    def make_row(start, end, frames=None, reason='', row_boxes=boxes):
        if frames is None:
            frames = (FaceKeyframe(start, row_boxes), FaceKeyframe(end, row_boxes))
        return LayoutSegment(start, end, 'diagonal_split', required_faces=2, face_boxes=row_boxes, face_centers=_centres(row_boxes), face_keyframes=frames, turn_seconds=end - start, reason=reason)
    ordinary_source = make_row(0.0, 5.0)
    ordinary = SimpleNamespace(media=media, layouts=[ordinary_source], story_beats=[], analysis={})
    ordinary_result = v73._normalise_layouts(ordinary, '9:16', False)
    if ordinary_result[0].mode != 'split_reaction':
        raise AssertionError('ordinary wide pair did not use top/bottom split')
    safe_frames = (FaceKeyframe(0.0, safe_boxes), FaceKeyframe(5.0, safe_boxes))
    semantic_source = make_row(0.0, 5.0, safe_frames, row_boxes=safe_boxes)
    semantic = SimpleNamespace(media=media, layouts=[semantic_source], analysis={}, story_beats=[StoryBeat(0.0, 2.0, 'question', 'question', 0.9)])
    diagonal = v73._normalise_layouts(semantic, '9:16', False)[0]
    if diagonal.mode != 'diagonal_split':
        raise AssertionError('safe semantic pair did not retain diagonal option')
    for panel in c.layout_geometry(diagonal, media).panels:
        _, _, points, fallback = motion.camera_path(media, panel, diagonal)
        if fallback or len(points) != 1:
            raise AssertionError('diagonal camera was not frozen')
    unsafe_semantic = SimpleNamespace(media=media, layouts=[make_row(0.0, 5.0)], analysis={}, story_beats=[StoryBeat(0.0, 2.0, 'question', 'question', 0.9)])
    if v73._normalise_layouts(unsafe_semantic, '9:16', False)[0].mode != 'split_reaction':
        raise AssertionError('unsafe close-up pair retained diagonal')
    for kind, expected in (('evidence', 'cinema_duo'), ('turn', 'offset_duo')):
        themed = SimpleNamespace(media=media, layouts=[make_row(0.0, 5.5)], analysis={}, story_beats=[StoryBeat(0.0, 2.0, kind, kind, 0.9)])
        chosen = v73._normalise_layouts(themed, '9:16', False)[0].mode
        if chosen != expected:
            raise AssertionError(f'{kind} beat selected {chosen}, not {expected}')
    moved_boxes = ((0.23, 0.18, 0.15, 0.27), (0.6, 0.22, 0.15, 0.27))
    moving_frames = (FaceKeyframe(0.0, boxes), FaceKeyframe(2.5, moved_boxes), FaceKeyframe(5.0, boxes))
    moving_source = make_row(0.0, 5.0, moving_frames)
    moving = SimpleNamespace(media=media, layouts=[moving_source], analysis={}, story_beats=[StoryBeat(0.0, 2.0, 'question', 'question', 0.9)])
    if v73._normalise_layouts(moving, '9:16', False)[0].mode != 'split_reaction':
        raise AssertionError('moving pair was incorrectly put in diagonal')
    short = SimpleNamespace(media=media, story_beats=[], analysis={}, layouts=[make_row(0.0, 1.8), make_row(1.8, 4.4), make_row(4.4, 8.8)])
    modes = [row.mode for row in v73._normalise_layouts(short, '9:16', False)]
    if modes != ['fit_blur', 'duo_context', 'split_reaction']:
        raise AssertionError(f'duration-based pair plan is wrong: {modes}')
    if len(c.layout_geometry(v73._normalise_layouts(short, '9:16', False)[1], media).panels) != 1:
        raise AssertionError('brief duo context is not one sharp group frame')
    merged_brief = SimpleNamespace(media=media, story_beats=[], analysis={}, layouts=[make_row(0.0, 2.6), make_row(2.6, 5.2)])
    merged_rows = v73._normalise_layouts(merged_brief, '9:16', False)
    if len(merged_rows) != 1 or merged_rows[0].mode != 'split_reaction':
        raise AssertionError('merged brief exchanges escaped long-dialogue policy')
    solo_left = ((0.05, 0.18, 0.16, 0.25),)
    solo_right = ((0.8, 0.18, 0.16, 0.25),)
    solo_frames = (FaceKeyframe(0.0, solo_left), FaceKeyframe(2.5, solo_left), FaceKeyframe(3.0, solo_right), FaceKeyframe(5.0, solo_right))
    moving_solo = LayoutSegment(0.0, 5.0, 'focus', required_faces=1, face_boxes=solo_left, face_centers=_centres(solo_left), face_keyframes=solo_frames, reason=v73._mark('V7.6 path check', '9:16'))
    if c.validate_geometry(media, moving_solo):
        raise AssertionError('moving solo speaker can leave the rendered crop')
    solo_panel = c.layout_geometry(moving_solo, media).panels[0]
    _, _, solo_path, _ = motion.camera_path(media, solo_panel, moving_solo)
    if len(solo_path) < 2 or solo_path[0][1:] == solo_path[-1][1:]:
        raise AssertionError('boundary-crossing solo did not trigger camera motion')
    stationary_solo = replace(moving_solo, face_keyframes=(FaceKeyframe(0.0, solo_left), FaceKeyframe(5.0, solo_left)))
    still_panel = c.layout_geometry(stationary_solo, media).panels[0]
    if len(motion.camera_path(media, still_panel, stationary_solo)[2]) != 1:
        raise AssertionError('camera moved while the speaker remained safe')
    result = {'ok': True, 'reported_shot': 'top-bottom split', 'safe_semantic_shot': 'frozen diagonal', 'moving_semantic_shot': 'top-bottom fallback', 'boundary_camera': 'zero-lag linear correction passed every-frame QA', 'stationary_camera': 'one frozen point', 'duration_modes': modes, 'real_3_4_render': False}
    if not real:
        return result
    with tempfile.TemporaryDirectory(prefix='master_v76_34_') as directory:
        root = Path(directory)
        source = root / 'source.mp4'
        duration = 2.4
        stub = SimpleNamespace(analysis={'engine_version': '7.6.0'})
        with FFmpegRuntime(stub) as runtime:
            runtime.execute(lambda: runtime.prefix() + ['-f', 'lavfi', '-i', f'testsrc2=size=960x540:rate=25:duration={duration}', '-f', 'lavfi', '-i', f'sine=frequency=440:sample_rate=48000:duration={duration}', '-c:v', 'libx264', '-preset', 'fast', '-crf', '14', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-ar', '48000'] + runtime.encoder_args() + ['-t', str(duration), str(source)], stage='V7.6 frozen diagonal source', timeout=90)
        source_media = probe(source)
        frames = (FaceKeyframe(0.0, safe_boxes), FaceKeyframe(duration, safe_boxes))
        layout = LayoutSegment(0.0, duration, 'diagonal_split', required_faces=2, face_boxes=safe_boxes, face_centers=_centres(safe_boxes), face_keyframes=frames, reason=v73._mark('V7.6 render check', '3:4'))
        plan = EditPlan(source_media, Transcript([Word(0.02, 1.15, 'FROZEN'), Word(1.18, 2.3, 'DIAGONAL')], 'FROZEN DIAGONAL', 'en'), PROFILES['podcast_interview'], load_caption_styles()['reference_bold_lime'], [SourceSpan(0.0, duration, 0.0)], [layout], [], None, duration, analysis={'render_fps': 25.0, 'settings_fps': 25.0, 'engine_version': '7.6.0', 'frame_family': '3:4', 'output_width': 1080, 'output_height': 1440, 'enforce_visible_face_qa': False})
        output = root / 'output.mp4'
        clean = root / 'clean.mp4'
        render(plan, output, root / 'render', Settings(height=1440, fps=25, preset='fast', crf=14), clean_output=clean)
        review = assert_output(output, plan)
        graphs = '\n'.join((path.read_text(encoding='utf-8') for path in (root / 'render').rglob('*.filters.txt')))
        if 'gblur=' in graphs or '(t+' in graphs:
            raise AssertionError('frozen diagonal render introduced blur or camera motion')
        result.update({'real_3_4_render': True, 'resolution': [review['output']['width'], review['output']['height']], 'full_decode_verified': review.get('full_decode_verified', False)})
        return result
