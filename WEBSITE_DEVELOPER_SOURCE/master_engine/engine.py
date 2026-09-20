from __future__ import annotations

import os
import shutil
import time
import uuid
from dataclasses import replace
from collections import Counter
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from .editorial import build_editorial
from .models import GraphicEvent
from .caption_catalog import select_style
from .composition import LAYOUT_NAMES, PRODUCTION_LAYOUTS, geometry, layout_geometry, projected_box
from .config import CACHE_DIR, Settings, load_caption_styles, prepare_directories
from .director import classify
from .media import probe, require_binaries
from .models import EditPlan, SourceSpan
from .polish import audio_profile, capability_report, colour_profile, quality_assessment
from .qa import assert_output, inspect_output, validate_plan
from .render import render
from .story import build_story, plan_graphics
from .timeline import build_spans, sample_audio_energy
from .transcribe import transcribe
from .transcript_export import write_transcript
from .utils import CommandError, emit, write_json
from .vision import analyze, build_layouts


VALID_MODES = {"full"}


class MasterEngineError(RuntimeError):
    pass


def _local_safe_layout(row, suffix: str):
    """Choose a local repair without ever restoring the removed wide frame."""
    count=len(row.face_boxes)
    duration=row.source_end-row.source_start
    members=()
    if count>=2 and duration<=2.0+.001:
        mode="fit_blur"
    elif count>4:
        from .subjects import four_panel_members
        mode="grid4_context"
        members=four_panel_members(row.face_boxes)
    elif count==4:
        mode="grid4"
    elif count==3:
        mode="grid3"
    elif count==2:
        mode="duo_context"
    elif count==1:
        mode="focus"
    else:
        mode="passthrough"
    return replace(row,mode=mode,active_side=None,panel_face_indexes=members,
                   reason=row.reason+suffix)


def _render_fps(source_fps: float, settings: Settings) -> float:
    if not settings.preserve_source_fps:
        return settings.fps
    source_fps = float(source_fps or 0.0)
    if 12.0 <= source_fps <= 60.1:
        return round(source_fps, 6)
    return settings.fps


def _clear_graphic_lane(media,layouts,start,end,top,bottom):
    from .motion import camera_path,value_at
    from .composition import subject_face
    for layout in layouts:
        if layout.source_end<=start or layout.source_start>=end:
            continue
        for panel in layout_geometry(layout,media).panels:
            w,h,points,_=camera_path(media,panel,layout)
            checks=[(frame.time,frame.boxes) for frame in layout.face_keyframes] or [(layout.source_start,layout.face_boxes)]
            for t,boxes in checks:
                # Full-source passthrough still needs face protection for a title.
                tracked=boxes if panel.face_index==-1 else (subject_face(layout,panel,boxes),)
                for box in tracked:
                    if box is None:
                        continue
                    x=panel.x+value_at(points,t,1)+box[0]*w
                    y=panel.y+value_at(points,t,2)+box[1]*h
                    if x<985 and x+box[2]*w>95 and y<bottom and y+box[3]*h>top:
                        return False
    return True


class MasterEngine:
    """Independent plan-first editor; it imports no legacy editing function."""

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or Settings.from_env()
        prepare_directories()

    def _build_plan(
        self,
        input_path: Path,
        *,
        progress=None,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> EditPlan:
        require_binaries()
        emit(progress, "probe", 0.0, "Reading source streams")
        media = probe(Path(input_path))
        emit(progress, "probe", 1.0, f"{media.width}x{media.height}, {media.duration:.1f}s; unrestricted duration")
        transcript = transcribe(media, self.settings, progress=progress, cancel_check=cancel_check)
        profile, analysis = classify(media, transcript)
        render_fps = _render_fps(media.fps, self.settings)
        emit(progress, "director", .10, f"Detected {profile.category.replace('_', ' ')}")
        energy = sample_audio_energy(media, progress=progress, cancel_check=cancel_check) if media.has_audio else []
        spans = build_spans(
            media, transcript, energy,
            silence_cut_seconds=profile.silence_cut_seconds,
            enabled=self.settings.enable_silence_tightening,
        )
        output_duration = sum(span.duration for span in spans)
        samples = analyze(
            media, profile, energy, self.settings,
            progress=progress, cancel_check=cancel_check,transcript=transcript,
        )
        beats, chapters = build_story(transcript, media.duration)
        layouts = build_layouts(media, profile, samples, self.settings, beats)
        output_states = []
        for span in spans:
            for layout in layouts:
                start,end=max(span.source_start,layout.source_start),min(span.source_end,layout.source_end)
                if end > start:
                    state=(layout.mode,layout.active_side)
                    if not output_states or output_states[-1][1] != state:
                        output_states.append((span.output_start+start-span.source_start,state))
        if any(b[0]-a[0] < max(3.0,self.settings.layout_min_hold_seconds)-.1 for a,b in zip(output_states,output_states[1:])):
            spans=[SourceSpan(0,media.duration,0)]
            output_duration=media.duration
            analysis["silence_tightening_note"]="Skipped because cutting these gaps would accelerate layout/speaker switches"
        style, caption_analysis = select_style(media, profile, transcript, samples,
            history_path=CACHE_DIR/'caption_choices.sqlite3',scope=self.settings.caption_scope,job_key=getattr(self,'_caption_job_key',None))
        editorial = build_editorial(transcript,media,profile)
        graphics = []  # No on-screen hooks; delivery metadata is retained.
        broll=[]
        sfx=[]
        music=None
        removed = max(0.0, media.duration-output_duration)
        mode_counts: Dict[str, int] = {}
        for row in layouts:
            mode_counts[row.mode] = mode_counts.get(row.mode, 0) + 1
        search_topics = [
            word for word, _ in Counter(
                keyword for beat in beats for keyword in beat.keywords
            ).most_common(12)
        ]
        quality=quality_assessment(media,samples)
        analysis.update({
            "engine_version": "7.0.0",
            "engine_isolation": "standalone package; no legacy renderer/caption/split imported",
            "settings_fps": render_fps,
            "render_fps": render_fps,
            "source_fps": round(media.fps, 3),
            "source_rotation_degrees":media.rotation,
            "output_width": self.settings.width,
            "output_height": self.settings.height,
            "output_master": "1080x1920 H.264 High / AAC / BT.709 / yuv420p",
            "source_quality_warning": ("none" if not quality["low_resolution"]
                else "source is below 720 pixels on its short edge; adaptive restoration is applied but cannot recreate missing detail"),
            "quality_enhancement":quality,
            "source_duration_seconds": round(media.duration, 3),
            "output_duration_seconds": round(output_duration, 3),
            "silence_removed_seconds": round(removed, 3),
            "caption_style": style.name if self.settings.captions_enabled else "captions_off",
            "captions_enabled": self.settings.captions_enabled,
            "editorial": editorial,
            "caption_selection": caption_analysis,
            "caption_template_count": caption_analysis["template_count"],
            "caption_nominal_preset_size": style.size,
            "reference_patch": "podcast-director-7",
            "caption_sizing": "fixed base size; controlled 100-104-100 percent phrase pulse; short phrases stay steady",
            "camera_policy": "frozen inside the protected subject lane; zero-delay smooth correction only at a boundary crossing",
            "caption_outline": style.outline_size,
            "moving_accents": list(style.accents),
            "layout_segments": len(layouts),
            "layout_mode_counts": mode_counts,
            "layout_mode_seconds": {mode:round(sum(r.source_end-r.source_start for r in layouts if r.mode==mode),3) for mode in mode_counts},
            "blur_policy": "fit_blur is allowed only for a verified multi-speaker segment lasting 2.00 seconds or less",
            "multi_face_segments": sum(row.required_faces >= 2 for row in layouts),
            "max_detected_faces":max((len(row.faces) for row in samples),default=0),
            "max_planned_faces":max((len(row.face_boxes) for row in layouts),default=0),
            "crowd_policy":"three people use grid3, four use grid4, and 5+ are partitioned across four stable context panels",
            "crowd_panel_groups":[{"start":row.source_start,"end":row.source_end,
                "members":[list(group) for group in row.panel_face_indexes]}
                for row in layouts if row.mode=='grid4_context'],
            "speaker_order_lock_seconds": max(3.0,self.settings.split_min_hold_seconds),
            "layout_hold_seconds": max(3.0,self.settings.layout_min_hold_seconds),
            "camera_hold_seconds": max(3.0,self.settings.layout_min_hold_seconds),
            "composition_policy": "single close-up; <=2s verified multi-speaker blur; longer duo/split/diagonal; stable 3/4/crowd grids; room-wide group strip removed",
            "source_cut_policy":"full-frame-rate scene scan before tracking; captions split at layout boundaries",
            "camera_motion_keyframes":sum(len(row.face_keyframes) for row in layouts),
            "layout_changes":sum(a.mode!=b.mode for a,b in zip(layouts,layouts[1:])),
            "split_enabled":self.settings.split_enabled,
            "broll_events": len(broll),
            "graphic_events": len(graphics),
            "search_topic_candidates": search_topics,
            "music": "disabled: original source audio only",
            "broll_policy":"disabled: no stock, placeholder cards or AI B-roll",
            "trend_snapshot": "2026-09-01",
            "capabilities": capability_report(),
            "enforce_visible_face_qa": bool(any(row.required_faces > 0 for row in layouts)),
            "reference_layouts": list(PRODUCTION_LAYOUTS),
            "retained_compatible_layouts":list(LAYOUT_NAMES),
            "post_render_remove_captions": True,
            "post_render_transcript":True,
        })
        plan = EditPlan(
            media=media,
            transcript=transcript,
            profile=profile,
            caption_style=style,
            spans=spans,
            layouts=layouts,
            broll=broll,
            music_path=music,
            output_duration=output_duration,
            analysis=analysis,
            story_beats=beats,
            chapters=chapters,
            graphics=graphics,
            sfx=sfx,
            audio_profile=audio_profile(energy, self.settings.enable_audio_polish),
            colour_profile=colour_profile(media,samples, self.settings.enable_colour_polish),
        )
        errors = validate_plan(plan)
        if errors and all("density budget" in error or "attention window" in error for error in errors):
            plan.broll=[]
            plan.graphics=[]
            plan.sfx=[]
            plan.analysis.update({"broll_events":0,"graphic_events":0,"optional_effects_note":"Suppressed to preserve the visual-attention budget"})
            errors=validate_plan(plan)
        if errors:
            raise CommandError("Master director QA failed: " + "; ".join(errors[:8]))
        emit(
            progress, "director", 1.0,
            f"Plan passed: {len(layouts)} planned shots, {len(set(row.mode for row in layouts))} layout types; source audio only",
        )
        return plan

    def run_job(
        self,
        input_path: Path,
        output_path: Path,
        *,
        mode: str = "full",
        progress=None,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> Dict[str, object]:
        if mode not in VALID_MODES:
            raise MasterEngineError(f"Unknown mode: {mode}")
        output_path = output_path.resolve()
        clean_output=output_path.with_name(output_path.stem+"_NO_CAPTIONS"+output_path.suffix)
        transcript_path=output_path.with_suffix(".transcript.txt")
        if any(path.exists() for path in (output_path,clean_output,transcript_path,
                output_path.with_suffix(".master_report.json"),
                output_path.with_suffix(".master_plan.json"))):
            raise MasterEngineError("Output or sidecar already exists; choose a new path to preserve existing files")
        self._caption_job_key = uuid.uuid4().hex
        started = time.time()
        from .config import WORK_DIR
        work = WORK_DIR / f"job_{uuid.uuid4().hex[:12]}"
        work.mkdir(parents=True, exist_ok=False)
        report_path = output_path.with_suffix(".master_report.json")
        plan_path = output_path.with_suffix(".master_plan.json")
        outputs: List[Path] = []
        attempted_outputs: List[Path] = []
        qa_rows: List[dict] = []
        try:
            plan = self._build_plan(input_path, progress=progress, cancel_check=cancel_check)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            write_json(plan_path, plan.to_dict())
            write_transcript(plan,transcript_path)
            attempted_outputs.extend((output_path,clean_output,transcript_path))
            render(plan, output_path, work / "full", self.settings, clean_output=clean_output,
                   progress=progress, cancel_check=cancel_check)
            review = inspect_output(output_path, plan)
            if not review["passed"] and review.get("visible_face_qa",{}).get("failures"):
                other_errors = [error for error in review["errors"] if error not in review["visible_face_qa"]["failures"]]
                if other_errors:
                    raise CommandError("Output QA failed: "+"; ".join(other_errors))
                # One deterministic repair: preserve the whole original source
                # instead of returning a cropped person or looping indefinitely.
                original_review = review["visible_face_qa"]
                failed_ids=set(original_review.get("failed_layout_indexes") or [])
                # Repair only a failed occurrence. Never turn ten correctly
                # planned shots into one full-video blurred strip.
                plan.layouts = [_local_safe_layout(row,"; local sampled-visibility repair")
                    if index in failed_ids else row for index,row in enumerate(plan.layouts)]
                plan.analysis["local_face_repairs"] = original_review
                plan.analysis["layout_mode_counts"] = dict(Counter(row.mode for row in plan.layouts))
                errors = validate_plan(plan)
                if errors:
                    raise CommandError("Safe composition repair failed: "+"; ".join(errors))
                write_json(plan_path, plan.to_dict())
                emit(progress,"repair",0.0,"Repairing only the uncertain shot; keeping the rest of the planned layouts")
                render(plan, output_path, work / "safe_repair", self.settings, clean_output=clean_output,
                       progress=progress, cancel_check=cancel_check)
                review = inspect_output(output_path, plan)
                review["warnings"].append("Only uncertain occurrences used a shared-group repair; other shots were preserved")
                if review.get("visible_face_qa",{}).get("failed_layout_indexes"):
                    failed=set(review["visible_face_qa"]["failed_layout_indexes"])
                    plan.layouts=[replace(row,mode="passthrough",required_faces=0,face_boxes=(),face_centers=(),
                        active_side=None,face_keyframes=(),panel_face_indexes=(),reason=row.reason+"; detector uncertain, complete sharp source retained")
                        if index in failed else row for index,row in enumerate(plan.layouts)]
                    plan.analysis["full_source_repair_indexes"]=sorted(failed)
                    plan.analysis["layout_mode_counts"]=dict(Counter(row.mode for row in plan.layouts))
                    errors=validate_plan(plan)
                    if errors:
                        raise CommandError("Local source repair failed: "+"; ".join(errors))
                    write_json(plan_path,plan.to_dict())
                    render(plan,output_path,work/"local_source_repair",self.settings,clean_output=clean_output,
                           progress=progress,cancel_check=cancel_check)
                    review=inspect_output(output_path,plan)
                    review["warnings"].append("Detector-uncertain shots alone retain the source; not every-frame tracking certification")
            if not review["passed"]:
                raise CommandError("Final Master Editor QA failed: "+"; ".join(review["errors"][:5]))
            qa_rows.append(review)
            clean_review=inspect_output(clean_output,plan)
            if not clean_review["passed"]:
                raise CommandError("Caption-free Master QA failed: "+"; ".join(clean_review["errors"][:5]))
            clean_review["variant"]="captions_removed"
            qa_rows.append(clean_review)
            outputs.append(output_path)
            credits = [
                {
                    "provider": row.provider, "license": row.license,
                    "credit": row.credit, "url": row.source_url, "query": row.query,
                }
                for row in plan.broll
            ]
            write_json(report_path, {
                "engine": "Master Editor 7.0.0",
                "created_at": int(time.time()),
                "elapsed_seconds": round(time.time()-started, 2),
                "mode": mode,
                "outputs": [str(path) for path in outputs],
                "captionless_output": str(clean_output),
                "transcript":str(transcript_path),
                "analysis": plan.analysis,
                "qa": qa_rows,
                "chapters": [row.__dict__ for row in plan.chapters],
                "asset_provenance": credits,
                "creator_rewards_note": (
                    "This editor improves production structure and records its decisions. It cannot guarantee RPM, "
                    "Creator Rewards eligibility, reach, originality ownership, or platform approval."
                ),
            })
            emit(progress, "qa", 1.0, "Every rendered MP4 passed structural QA")
            return {
                "outputs": outputs,
                "captionless_output": clean_output,
                "transcript":transcript_path,
                "report": report_path,
                "plan": plan_path,
                "mode": mode,
            }
        except Exception as exc:
            for path in attempted_outputs + [output_path,clean_output,transcript_path, report_path, plan_path]:
                try:
                    path.unlink(missing_ok=True)
                except Exception:
                    pass
            if isinstance(exc, MasterEngineError):
                raise
            raise MasterEngineError(str(exc)) from exc
        finally:
            if os.getenv("MASTER_KEEP_WORK", "0").strip().lower() not in {"1", "true", "yes"}:
                shutil.rmtree(work, ignore_errors=True)

    def run(
        self,
        input_path: Path,
        output_path: Path,
        *,
        progress=None,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> Tuple[Path, Path]:
        result = self.run_job(
            input_path, output_path, mode="full",
            progress=progress, cancel_check=cancel_check,
        )
        return Path(result["outputs"][0]), Path(result["report"])
