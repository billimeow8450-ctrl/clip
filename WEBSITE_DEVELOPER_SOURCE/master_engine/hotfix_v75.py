"""Master Editor V7.5: align final QA with immediate speaker entrances.

V7.4 deliberately backdates a confirmed face-count increase to the first
observation so the layout follows a new speaker without visible delay.  The
legacy final validator still required 0.90 seconds before the same increase,
which rejected a plan that the director had correctly approved.  This layer
removes only that contradictory error.  Ratio locking, same-count anti-flicker
holds, face geometry checks, and every other QA rule remain unchanged.
"""
from __future__ import annotations

import json
from pathlib import Path


HOLD_ERROR = "frame family changed before the adaptive layout hold"
_installed = False
_legacy_validate_plan = None


def _verified_face_increase(previous, layout, multi_face_modes):
    """Return true only when the destination has evidence for every person."""
    if not (
        layout.required_faces >= 2
        and layout.required_faces > previous.required_faces
        and layout.mode in multi_face_modes
        and len(layout.face_boxes) >= layout.required_faces
        and len(layout.face_centers) >= layout.required_faces
    ):
        return False
    return all(
        w > 0 and h > 0 and x >= 0 and y >= 0
        and x + w <= 1.02 and y + h <= 1.02
        for x, y, w, h in layout.face_boxes[:layout.required_faces]
    )


def _legacy_hold_rejections(plan, multi_face_modes):
    """Mirror the V6/V7.3 hold test and classify each exact rejection."""
    if not plan.layouts:
        return []
    previous = None
    mode_since = plan.layouts[0].source_start
    rejected = []
    for layout in plan.layouts:
        if layout.native_cut:
            mode_since = layout.source_start
        if previous is not None and layout.mode != previous.mode:
            held = layout.source_start - mode_since
            safety_drop = layout.required_faces < previous.required_faces
            legacy_upgrade = (
                layout.required_faces >= 2
                and layout.required_faces > previous.required_faces
                and held >= .90
            )
            hold = float(plan.analysis.get("layout_hold_seconds") or 4.0)
            speech_boundary = (
                previous.mode in {"speaker_context", "duo_context"}
                and held >= .64 and layout.turn_seconds > 3
            )
            short_blur_boundary = previous.mode == "fit_blur" and held >= .50
            enter_short_blur = (
                layout.mode == "fit_blur"
                and .50 <= layout.source_end - layout.source_start <= 2.001
            )
            legacy_rejects = (
                held < hold - .10
                and not safety_drop
                and not legacy_upgrade
                and not layout.native_cut
                and not speech_boundary
                and not short_blur_boundary
                and not enter_short_blur
            )
            if legacy_rejects:
                rejected.append(_verified_face_increase(
                    previous, layout, multi_face_modes))
            mode_since = layout.source_start
        previous = layout
    return rejected


def _patch_plan_qa():
    from . import engine as eng
    from . import qa

    global _legacy_validate_plan
    original = qa.validate_plan
    _legacy_validate_plan = original

    def validate(plan):
        errors = list(original(plan))
        target_count = errors.count(HOLD_ERROR)
        if not target_count:
            return errors
        classified = _legacy_hold_rejections(plan, qa.MULTI_FACE_MODES)
        # Fail closed if the upstream validator ever changes independently.
        if len(classified) != target_count:
            return errors
        removable = sum(classified)
        if not removable:
            return errors
        kept = []
        for error in errors:
            if error == HOLD_ERROR and removable:
                removable -= 1
                continue
            kept.append(error)
        return kept

    qa.validate_plan = validate
    # engine.py imported the function by value, so its alias must be rebound.
    eng.validate_plan = validate


def _patch_engine_version():
    from . import engine as eng

    original_build = eng.MasterEngine._build_plan
    original_job = eng.MasterEngine.run_job

    def build(self, *args, **kwargs):
        plan = original_build(self, *args, **kwargs)
        plan.analysis.update({
            "engine_version": "7.5.0",
            "immediate_speaker_entry_qa": (
                "verified face-count increases bypass only the cosmetic layout hold"),
            "same_count_layout_hold_enforced": True,
            "frame_family_locked_for_entire_video": True,
        })
        return plan

    def run_job(self, input_path, output_path, **kwargs):
        result = original_job(self, input_path, output_path, **kwargs)
        report = Path(result["report"])
        if report.is_file():
            data = json.loads(report.read_text(encoding="utf-8"))
            data["engine"] = "Master Editor 7.5.0"
            data.setdefault("analysis", {})["engine_version"] = "7.5.0"
            eng.write_json(report, data)
        return result

    eng.MasterEngine._build_plan = build
    eng.MasterEngine.run_job = run_job


def install():
    global _installed
    if _installed:
        return
    _installed = True
    _patch_plan_qa()
    _patch_engine_version()


def check_v75():
    """Prove the reported case passes and a cosmetic early switch still fails."""
    from . import engine as eng
    from . import qa
    from .config import load_caption_styles
    from .director import PROFILES
    from .models import (EditPlan, LayoutSegment, MediaInfo, SourceSpan,
                         Transcript)

    media = MediaInfo(Path("v75-qa-check.mp4"), 3.2, 1920, 1080, 25.0, True)
    one = ((.12, .18, .20, .34),)
    all_boxes = ((.03, .18, .14, .30), (.29, .18, .14, .30),
                 (.55, .18, .14, .30), (.81, .18, .14, .30))
    pair = (all_boxes[0], all_boxes[3])

    def centres(boxes):
        return tuple((x + w / 2, y + h / 2) for x, y, w, h in boxes)

    def plan(rows):
        return EditPlan(
            media, Transcript([], "", "en"), PROFILES["podcast_interview"],
            load_caption_styles()["reference_bold_lime"],
            [SourceSpan(0.0, 3.2, 0.0)], rows, [], None, 3.2,
            analysis={
                "layout_hold_seconds": 3.0,
                "speaker_order_lock_seconds": 3.0,
                "output_width": 1080,
                "output_height": 1920,
            },
        )

    immediate = plan([
        LayoutSegment(0.0, .2, "focus", required_faces=1,
                      face_boxes=one, face_centers=centres(one)),
        LayoutSegment(.2, 3.2, "diagonal_split", required_faces=2,
                      face_boxes=pair, face_centers=centres(pair),
                      turn_seconds=3.0),
    ])
    before = _legacy_validate_plan(immediate)
    after = qa.validate_plan(immediate)
    if HOLD_ERROR not in before or after:
        raise AssertionError("confirmed immediate speaker entrance QA was not repaired")

    # The same exception must work when a third or fourth verified participant
    # enters; these are content changes, not cosmetic frame-family flicker.
    for old_count, new_count, old_mode, new_mode in (
        (2, 3, "diagonal_split", "grid3"),
        (3, 4, "grid3", "grid4"),
    ):
        old_boxes = all_boxes[:old_count]
        new_boxes = all_boxes[:new_count]
        growth = plan([
            LayoutSegment(0.0, .2, old_mode, required_faces=old_count,
                          face_boxes=old_boxes, face_centers=centres(old_boxes)),
            LayoutSegment(.2, 3.2, new_mode, required_faces=new_count,
                          face_boxes=new_boxes, face_centers=centres(new_boxes),
                          turn_seconds=3.0),
        ])
        if HOLD_ERROR not in _legacy_validate_plan(growth) or qa.validate_plan(growth):
            raise AssertionError(f"{old_count}-to-{new_count} entrance QA was not repaired")

    cosmetic = plan([
        LayoutSegment(0.0, .2, "split_reaction", required_faces=2,
                      face_boxes=pair, face_centers=centres(pair)),
        LayoutSegment(.2, 3.2, "diagonal_split", required_faces=2,
                      face_boxes=pair, face_centers=centres(pair),
                      turn_seconds=3.0),
    ])
    guarded = qa.validate_plan(cosmetic)
    if HOLD_ERROR not in guarded:
        raise AssertionError("same-count anti-flicker hold was accidentally disabled")
    if eng.validate_plan is not qa.validate_plan:
        raise AssertionError("engine retained the stale validator alias")
    return {
        "ok": True,
        "verified_speaker_entries": "1-to-2, 2-to-3 and 3-to-4 at 0.20s pass",
        "same_count_early_switch": "still rejected",
        "ratio_lock": "unchanged",
        "engine_alias_rebound": True,
    }
