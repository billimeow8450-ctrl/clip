"""Bounded CPU rendering and one native-crash recovery per render job.

The compatibility path changes implementation flags, never the edit plan,
captions, resolution, CRF, source selection or source/output timing.
"""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Callable, Optional, Sequence

from . import __version__
from .config import CACHE_DIR
from .utils import CommandError, cancelled, emit, run


def _private_text(value: str) -> str:
    # Commands normally reference local files, but never persist signed URLs.
    return re.sub(r"https?://[^\s'\"<>]+", "<remote-media-url>", str(value))


class FFmpegRuntime:
    def __init__(self, plan, *, compatibility: bool = False, progress=None,
                 cancel_check=None, diagnostic_dir: Optional[Path] = None):
        self.plan = plan
        self.compatibility = bool(compatibility)
        self.progress = progress
        self.cancel_check = cancel_check
        self.diagnostic_dir = diagnostic_dir or CACHE_DIR / "render_diagnostics"
        self.failures = []
        self.recoveries = []
        self.completed_commands = 0
        self.native_retries = 0
        self.last_stage = "render"
        self.report_path = None

    def prefix(self) -> list:
        result = ["ffmpeg", "-nostdin", "-y", "-hide_banner", "-loglevel", "warning",
                  "-filter_threads", "1", "-filter_complex_threads", "1"]
        if self.compatibility:
            # FFmpeg's diagnostic CPU override avoids SIMD code paths. x264
            # has its own CPU dispatch, disabled separately in encoder_args.
            result.extend(["-cpuflags", "0"])
        return result

    def input_args(self) -> list:
        return ["-threads:v", "1" if self.compatibility else "2", "-threads:a", "1"]

    def encoder_args(self) -> list:
        result = ["-threads:v", "1" if self.compatibility else "2", "-threads:a", "1"]
        if self.compatibility:
            result.extend(["-x264-params", "asm=0"])
        return result

    def execute(self, command: Callable[[], Sequence[str]], *, stage: str,
                timeout: float, script: Optional[Path] = None):
        self.last_stage = stage
        for attempt in range(2):
            if cancelled(self.cancel_check):
                raise CommandError("Master Editor job cancelled", reason="cancelled")
            if attempt:
                self.native_retries += 1
            args = list(command())
            started = time.monotonic()
            try:
                result = run(args, timeout=timeout * (2 if self.compatibility else 1),
                             cancel_check=self.cancel_check)
                self.completed_commands += 1
                if attempt:
                    self.recoveries.append(stage)
                return result
            except CommandError as exc:
                record = {
                    "stage": stage, "compatibility": self.compatibility,
                    "returncode": exc.returncode, "signal": exc.signal_name,
                    "reason": exc.reason,
                    "elapsed_seconds": round(time.monotonic() - started, 3),
                    "command": [_private_text(arg) for arg in args],
                    "stderr": _private_text(exc.stderr[-1048576:]),
                    "stdout": _private_text(exc.stdout[-65536:]),
                    "stderr_truncated": len(exc.stderr) > 1048576,
                    "message": _private_text(str(exc)),
                }
                if script is not None and script.exists():
                    record["filter_graph"] = _private_text(script.read_text(encoding="utf-8"))
                self.failures.append(record)
                if (self.compatibility or attempt or not exc.native_crash
                        or cancelled(self.cancel_check)):
                    raise
                self.compatibility = True
                emit(self.progress, "render_recovery", 0.0,
                     f"FFmpeg {exc.signal_name}: retrying the same shot with the compatibility CPU renderer; edit plan unchanged")
        raise AssertionError("Unreachable FFmpeg retry state")

    def _environment(self) -> dict:
        media = self.plan.media
        result = {
            "ffmpeg_path": shutil.which("ffmpeg"), "architecture": platform.machine(),
            "system": platform.system(), "cpu_count": os.cpu_count(),
            "source": {"width": media.width, "height": media.height,
                       "fps": media.fps, "duration": media.duration,
                       "video_codec": media.video_codec, "audio_codec": media.audio_codec,
                       "rotation": media.rotation},
        }
        if cancelled(self.cancel_check):
            result["extra_probes_skipped"] = "job cancelled"
            return result
        try:
            result["ffmpeg_version"] = run(["ffmpeg", "-version"], timeout=15).stdout[:32768]
        except Exception as exc:
            result["ffmpeg_version_error"] = str(exc)[:2000]
        try:
            # Exclude tags, transcript, title and original URL metadata.
            result["source_streams"] = json.loads(run([
                "ffprobe", "-v", "error", "-show_entries",
                "stream=index,codec_name,codec_type,pix_fmt,width,height,r_frame_rate,avg_frame_rate,color_space,color_range,color_transfer,color_primaries,sample_rate,channels",
                "-of", "json", str(media.path),
            ], timeout=30).stdout)
        except Exception as exc:
            result["source_probe_error"] = _private_text(str(exc))[:2000]
        return result

    def finish(self, exception=None) -> None:
        self.plan.analysis["ffmpeg_runtime"] = {
            "engine_version": __version__, "filter_threads": 1,
            "codec_threads": 1 if self.compatibility else 2,
            "one_layout_per_graph": True, "pixel_format": "yuv420p",
            "compatibility_cpu": self.compatibility,
            "successful_commands": self.completed_commands,
            "recovered_stages": list(self.recoveries),
            "native_crash_retries": self.native_retries,
        }
        if not self.failures:
            return
        try:
            environment = self._environment()
        except Exception as exc:
            environment = {"collection_error": _private_text(str(exc))[:2000]}
        payload = {
            "engine": __version__, "created_at": int(time.time()),
            "job_completed": exception is None,
            "runtime": self.plan.analysis["ffmpeg_runtime"],
            "environment": environment, "failures": self.failures,
            "diagnosis_note": "A swscaler alignment warning alone does not identify the cause of a native FFmpeg crash.",
            "limits": "No source media, transcript or environment credentials are included; stderr is limited to its final 1 MiB.",
        }
        try:
            self.diagnostic_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            path = self.diagnostic_dir / f"ffmpeg_{uuid.uuid4().hex}.json"
            descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
            self.report_path = path
            self.plan.analysis["ffmpeg_diagnostic_report"] = str(path)
            if isinstance(exception, CommandError):
                exception.diagnostic_path = path
        except OSError as exc:
            # Log failure must not hide or misidentify the original render failure.
            self.plan.analysis["ffmpeg_diagnostic_save_error"] = str(exc)

    def __enter__(self):
        return self

    def __exit__(self, kind, exception, traceback):
        self.finish(exception)
        if isinstance(exception, CommandError) and exception.native_crash:
            state = ("Compatibility rendering also stopped" if self.native_retries else
                     "Compatibility rendering stopped" if self.compatibility else "Rendering stopped")
            details = ("The saved report contains the exact command, filter graph and FFmpeg version."
                       if self.report_path else "No diagnostic report could be saved; share the complete terminal output.")
            exception.args = (
                f"FFmpeg crashed ({exception.signal_name}, return code {exception.returncode}) during {self.last_stage}. "
                f"{state}; no incomplete video was sent. {details}",
            )
        return False
