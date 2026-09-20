from __future__ import annotations

import argparse
import importlib
import json
import signal
import sys
from dataclasses import replace
from pathlib import Path

from .config import Settings, load_caption_styles, prepare_directories
from .engine import MasterEngine
from .media import require_binaries


def self_test() -> int:
    errors = []
    try:
        require_binaries()
        prepare_directories()
    except Exception as exc:
        errors.append(str(exc))
    styles = load_caption_styles()
    required = ["numpy", "PIL", "cv2", "faster_whisper", "yt_dlp", "telegram"]
    missing = []
    modules = {}
    for name in required:
        try:
            modules[name] = importlib.import_module(name)
        except Exception as exc:
            missing.append(name)
            errors.append(f"Cannot import {name}: {exc}")
    detector_kind = "unavailable"
    if "cv2" in modules and "numpy" in modules:
        try:
            from .vision import FaceDetector
            detector = FaceDetector(modules["cv2"])
            detector.detect(modules["numpy"].zeros((320, 320, 3), dtype="uint8"))
            detector_kind = detector.kind
            if detector_kind != "yunet":
                errors.append("Bundled YuNet is unavailable; install a compatible OpenCV 4.8+ or 5 runtime")
        except Exception as exc:
            errors.append(f"Face model check failed: {exc}")
    from .composition import PRODUCTION_LAYOUTS
    from .selfcheck import check_design
    try:
        design = check_design()
    except Exception as exc:
        design = {}
        errors.append(f"Design regression failed: {exc}")
    if "PIL" in modules:
        from .captions import _font_path
        if not _font_path("DejaVu Sans"):
            errors.append("Install fontconfig and fonts-dejavu-core for measured captions")
    print(json.dumps({
        "ok": not errors,
        "engine": "Master Editor 7.8.0",
        "caption_template_count": len(styles),
        "caption_family_count": len({style.family for style in styles.values()}),
        "missing_python_modules": missing,
        "errors": errors,
        "face_detector": detector_kind,
        "design_checks": design,
        "duration_limit": None,
        "legacy_renderer_dependency": False,
        "source_methods": ["send_full_file", "file_timestamp", "youtube_timestamp"],
        "render_mode": "automatic_plan_then_full_edit",
        "reference_layouts": list(PRODUCTION_LAYOUTS),
        "added_music": False,
        "added_broll": False,
        "blur_rule":"disabled; sharp composition only",
        "result_actions":["remove_captions","transcript"],
        "access_commands":["/key DAYS COUNT","/key YOUR_KEY","/revoke USER_ID KEY"],
        "transcription_note": "Whisper weights load on the first audio job; network/model access is not tested here",
    }, ensure_ascii=False))
    return 0 if not errors else 2


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m master_engine")
    parser.add_argument("--input")
    parser.add_argument("--output")
    parser.add_argument(
        "--mode",
        choices=("full",),
        default="full",
    )
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--render-self-test", action="store_true")
    parser.add_argument("--list-caption-templates", action="store_true")
    parser.add_argument("--captions",choices=("auto","none"),default="auto")
    parser.add_argument("--split",choices=("on","off"),default="on")
    parser.add_argument("--motion-profile",choices=("smooth","xml_reference"),default="smooth")
    args = parser.parse_args(argv)
    if args.render_self_test:
        from .render_selfcheck import check_renderer
        try:
            result = check_renderer(compatibility=Settings.from_env().ffmpeg_compatibility)
        except Exception as exc:
            print(json.dumps({"ok": False, "real_ffmpeg_render": False, "error": str(exc)}, ensure_ascii=False))
            return 2
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["ok"] else 2
    if args.list_caption_templates:
        from .caption_catalog import export_catalog
        print(json.dumps(export_catalog(), ensure_ascii=False))
        return 0
    if args.self_test:
        return self_test()
    if not args.input or not args.output:
        parser.error("--input and --output are required")
    cancelled = {"value": False}

    def stop(*_):
        cancelled["value"] = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    def progress(stage: str, fraction: float, detail: str) -> None:
        print(json.dumps({"type": "progress", "stage": stage, "fraction": fraction, "detail": detail}, ensure_ascii=False), flush=True)

    try:
        settings=replace(Settings.from_env(),captions_enabled=args.captions!="none",split_enabled=args.split=="on",motion_profile=args.motion_profile)
        result = MasterEngine(settings).run_job(
            Path(args.input), Path(args.output), mode=args.mode, progress=progress,
            cancel_check=lambda: cancelled["value"],
        )
    except Exception as exc:
        print(json.dumps({"type": "error", "error": str(exc)}, ensure_ascii=False), flush=True)
        return 1
    print(json.dumps({
        "type": "complete",
        "outputs": [str(path) for path in result["outputs"]],
        "captionless_output": str(result["captionless_output"]),
        "transcript":str(result["transcript"]),
        "report": str(result["report"]),
        "plan": str(result["plan"]),
        "mode": result["mode"],
    }, ensure_ascii=False), flush=True)
    return 0
