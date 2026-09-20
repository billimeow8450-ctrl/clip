"""Master Editor 7.8 deployment policy.

User-requested production policy layered on top of the supplied V7.7 engine:
- no blur-backed layouts; short multi-person moments stay sharp
- original source timing/speed preserved by default
- no audio polish when MASTER_AUDIO_POLISH=0 (no compressor/loudnorm/EQ)
- system-font fallback when optional bundled caption fonts are missing/corrupt
- coherent 7.8 reporting without rewriting the proven V7.1-V7.7 patches
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import replace
from pathlib import Path

_installed = False


def _flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _sharp_row(row, media=None):
    """Convert any legacy blur shot into a sharp deterministic composition."""
    from . import composition
    count = max(int(getattr(row, "required_faces", 0) or 0), len(getattr(row, "face_boxes", ()) or ()))
    if count >= 2 and getattr(row, "face_boxes", ()):
        candidate = replace(
            row,
            mode="duo_context" if count == 2 else "grid3" if count == 3 else "grid4" if count == 4 else "grid4_context",
            active_side=None,
            reason=str(getattr(row, "reason", "")) + "; V7.8 no-blur sharp replacement",
        )
        if media is None or not composition.validate_geometry(media, candidate):
            return candidate
    return replace(
        row,
        mode="passthrough",
        required_faces=0,
        face_boxes=(),
        face_centers=(),
        face_keyframes=(),
        panel_face_indexes=(),
        active_side=None,
        reason=str(getattr(row, "reason", "")) + "; V7.8 no-blur full-source fallback",
    )


def _patch_no_blur() -> None:
    from . import composition, engine, hotfix_v73, hotfix_v76

    # Planner: <=2s pair moments use one sharp duo context instead of fit_blur.
    previous_pair = hotfix_v76._pair_choice
    def pair_choice(plan, row):
        duration = row.source_end - row.source_start
        if duration <= 2.001:
            return ("duo_context", "verified short two-person moment; V7.8 sharp no-blur context")
        return previous_pair(plan, row)
    hotfix_v76._pair_choice = pair_choice

    # Catch every fit_blur emitted by any older normalizer/fallback.
    previous_normalise = hotfix_v73._normalise_layouts
    def normalise(plan, family, native):
        rows = previous_normalise(plan, family, native)
        return [_sharp_row(row, getattr(plan, "media", None)) if row.mode == "fit_blur" else row for row in rows]
    hotfix_v73._normalise_layouts = normalise

    # V7.6 audit encoded blur as mandatory for <=2s; V7.8 explicitly bans it.
    previous_audit = hotfix_v76._audit_plan
    def audit(plan):
        errors = [e for e in previous_audit(plan) if e not in {
            "short verified multi-person shot missed the blur layout",
            "blur layout escaped its exact <=2 second condition",
        }]
        if any(row.mode == "fit_blur" for row in plan.layouts):
            errors.append("V7.8 policy forbids blur-backed layouts")
        return list(dict.fromkeys(errors))
    hotfix_v76._audit_plan = audit

    # Last-resort renderer guard: even a stale/saved fit_blur plan is rendered sharp.
    previous_render_geometry = composition.render_geometry
    def render_geometry(label, output, layout, media, fps, index, source_time=None, source_end=None):
        if layout.mode == "fit_blur":
            layout = _sharp_row(layout, media)
        return previous_render_geometry(label, output, layout, media, fps, index, source_time, source_end)
    composition.render_geometry = render_geometry

    production = tuple(name for name in composition.PRODUCTION_LAYOUTS if name != "fit_blur")
    composition.PRODUCTION_LAYOUTS = production
    engine.PRODUCTION_LAYOUTS = production


def _patch_audio_defaults() -> None:
    from . import config, polish, render

    previous_from_env = config.Settings.from_env
    @classmethod
    def from_env(cls):
        settings = previous_from_env()
        return replace(
            settings,
            preserve_source_fps=_flag("MASTER_PRESERVE_SOURCE_FPS", True),
            enable_silence_tightening=_flag("MASTER_SILENCE_TIGHTEN", False),
            enable_audio_polish=_flag("MASTER_AUDIO_POLISH", False),
        )
    config.Settings.from_env = from_env

    # audio_profile(enabled=False) produces noise_reduction_db=-80. Treat that
    # sentinel as strict source-audio preservation: no EQ/compressor/loudnorm.
    previous_audio_filter = polish.audio_filter
    def audio_filter(profile):
        if getattr(profile, "noise_reduction_db", 0) <= -79 and abs(getattr(profile, "speech_gain_db", 0.0)) < .001:
            return "anull"
        return previous_audio_filter(profile)
    polish.audio_filter = audio_filter
    render.audio_filter = audio_filter


def _patch_fonts() -> None:
    from . import captions, fonts

    previous_bundled = fonts.bundled_font
    def bundled_font(family, weight=700, italic=False):
        path = previous_bundled(family, weight, italic)
        if path:
            try:
                if Path(path).is_file() and Path(path).stat().st_size > 1024:
                    return path
            except OSError:
                pass
        return ""
    fonts.bundled_font = bundled_font
    captions.bundled_font = bundled_font
    captions._font_path.cache_clear()

    def check_fonts():
        from PIL import ImageFont
        checked = []
        for family, weight, italic in [
            ("Montserrat", 600, False), ("Montserrat", 700, False), ("Montserrat", 800, False),
            ("Anton", 400, False), ("Barlow Condensed", 700, False), ("Barlow Condensed", 700, True),
            ("DM Serif Display", 400, False), ("DM Serif Display", 400, True),
        ]:
            path = bundled_font(family, weight, italic)
            if not path:
                pattern = family + ":style=" + ("Bold" if weight >= 700 else "Regular") + (" Italic" if italic else "")
                try:
                    path = subprocess.check_output(["fc-match", "-f", "%{file}", pattern], text=True, timeout=5).strip()
                except Exception:
                    path = ""
            if not path or not Path(path).is_file():
                raise ValueError("No usable caption font/fallback for: " + family)
            ImageFont.truetype(path, 48)
            checked.append(Path(path).name)
        return checked
    fonts.check_fonts = check_fonts


def _patch_reporting() -> None:
    from . import caption_catalog, engine, selfcheck

    previous_build = engine.MasterEngine._build_plan
    previous_run = engine.MasterEngine.run_job

    def build(self, *args, **kwargs):
        plan = previous_build(self, *args, **kwargs)
        # Defensive second pass in case a future planner path bypasses V7.6 normalisation.
        plan.layouts = [_sharp_row(row, plan.media) if row.mode == "fit_blur" else row for row in plan.layouts]
        counts = {}
        for row in plan.layouts:
            counts[row.mode] = counts.get(row.mode, 0) + 1
        plan.analysis.update({
            "engine_version": "7.8.0",
            "blur_policy": "disabled; sharp layouts or complete-source fallback only",
            "pair_layout_policy": "<=3s sharp duo context; longer pair uses measured split/diagonal/context layouts",
            "layout_mode_counts": counts,
            "audio_policy": "source timing/speed preserved; silence tightening and audio polish default off",
        })
        return plan

    def run(self, *args, **kwargs):
        result = previous_run(self, *args, **kwargs)
        try:
            report = Path(result["report"])
            data = json.loads(report.read_text(encoding="utf-8"))
            data["engine"] = "Master Editor 7.8.0"
            data.setdefault("analysis", {}).update({
                "engine_version": "7.8.0",
                "blur_policy": "disabled; no blur-backed composition",
                "audio_policy": "source timing/speed preserved by default; no audio polish",
            })
            report.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass
        return result

    engine.MasterEngine._build_plan = build
    engine.MasterEngine.run_job = run

    previous_export = caption_catalog.export_catalog
    def export_catalog():
        data = previous_export()
        data["version"] = "7.8.0"
        return data
    caption_catalog.export_catalog = export_catalog

    previous_design = selfcheck.check_design
    def check_design():
        data = previous_design()
        data["short_multi_speaker_blur_limit_seconds"] = 0.0
        data["blur_disabled"] = True
        data["automatic_layouts"] = [name for name in data.get("automatic_layouts", []) if name != "fit_blur"]
        return data
    selfcheck.check_design = check_design


def install() -> None:
    global _installed
    if _installed:
        return
    _installed = True
    _patch_no_blur()
    _patch_audio_defaults()
    _patch_fonts()
    _patch_reporting()
