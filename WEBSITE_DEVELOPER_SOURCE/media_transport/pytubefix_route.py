from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

from .errors import ErrorKind, MediaTransportError
from .manifest import MediaProbe


def available() -> bool:
    try:
        import pytubefix  # noqa: F401
        return True
    except Exception:
        return False


def _resolution_height(value: Any) -> int:
    text = str(value or "")
    m = re.search(r"(\d{3,4})p", text)
    return int(m.group(1)) if m else 0


def synthetic_info(url: str) -> dict[str, Any]:
    try:
        from pytubefix import YouTube
        yt = YouTube(url)
        formats: list[dict[str, Any]] = []
        for stream in yt.streams:
            resolution = getattr(stream, "resolution", None)
            abr = getattr(stream, "abr", None)
            codecs = list(getattr(stream, "codecs", None) or [])
            includes_video = bool(getattr(stream, "includes_video_track", False))
            includes_audio = bool(getattr(stream, "includes_audio_track", False))
            if not includes_video and resolution:
                includes_video = True
            if not includes_audio and abr:
                includes_audio = True
            vcodec = codecs[0] if includes_video and codecs else "none"
            acodec = "none"
            if includes_audio:
                if len(codecs) > 1:
                    acodec = codecs[-1]
                elif not includes_video and codecs:
                    acodec = codecs[0]
                else:
                    acodec = "mp4a.40.2"
            mime = str(getattr(stream, "mime_type", "") or "")
            ext = "mp4" if "mp4" in mime else ("webm" if "webm" in mime else "mp4")
            filesize = getattr(stream, "filesize", None) or getattr(stream, "filesize_approx", None)
            formats.append({
                "format_id": str(getattr(stream, "itag", "")),
                "height": _resolution_height(resolution),
                "width": 0,
                "fps": getattr(stream, "fps", None),
                "vcodec": vcodec if includes_video else "none",
                "acodec": acodec if includes_audio else "none",
                "ext": ext,
                "filesize": int(filesize) if filesize else None,
                "abr": float(str(abr).rstrip("kbps")) if abr and str(abr).rstrip("kbps").replace(".", "", 1).isdigit() else 0,
            })
        return {
            "id": str(getattr(yt, "video_id", "") or ""),
            "title": str(getattr(yt, "title", "") or "YouTube Video"),
            "duration": float(getattr(yt, "length", 0) or 0),
            "description": str(getattr(yt, "description", "") or ""),
            "formats": formats,
            "_transport": "pytubefix",
        }
    except Exception as exc:
        raise MediaTransportError(
            f"pytubefix metadata fallback failed: {exc}",
            kind=ErrorKind.UNKNOWN,
            route="pytubefix",
        ) from exc


def _stream_height(stream: Any) -> int:
    return _resolution_height(getattr(stream, "resolution", None))


def download_video(
    url: str,
    work_dir: Path,
    stem: str,
    target_height: Optional[int],
    ffmpeg_bin: str,
) -> Path:
    """Independent final fallback. Prefer progressive MP4; otherwise merge adaptive video+audio."""
    try:
        from pytubefix import YouTube
        yt = YouTube(url)
        streams = list(yt.streams)
        progressive = [
            s for s in streams
            if bool(getattr(s, "is_progressive", False))
            and "mp4" in str(getattr(s, "mime_type", "") or "")
            and _stream_height(s) > 0
            and (not target_height or _stream_height(s) <= int(target_height) + 4)
        ]
        if progressive:
            stream = max(progressive, key=lambda s: (_stream_height(s), int(getattr(s, "fps", 0) or 0)))
            path = Path(stream.download(output_path=str(work_dir), filename=f"{stem}.mp4"))
            return path.resolve()

        videos = [
            s for s in streams
            if bool(getattr(s, "includes_video_track", False))
            and not bool(getattr(s, "includes_audio_track", False))
            and _stream_height(s) > 0
            and (not target_height or _stream_height(s) <= int(target_height) + 4)
        ]
        audios = [
            s for s in streams
            if bool(getattr(s, "includes_audio_track", False))
            and not bool(getattr(s, "includes_video_track", False))
        ]
        if not videos or not audios:
            raise RuntimeError("pytubefix did not expose compatible video+audio streams")
        video = max(videos, key=lambda s: (_stream_height(s), int(getattr(s, "fps", 0) or 0)))
        audio = max(audios, key=lambda s: int(getattr(s, "filesize", 0) or getattr(s, "filesize_approx", 0) or 0))
        vpath = Path(video.download(output_path=str(work_dir), filename=f"{stem}.video" + (".mp4" if "mp4" in str(getattr(video, "mime_type", "")) else ".webm")))
        apath = Path(audio.download(output_path=str(work_dir), filename=f"{stem}.audio" + (".m4a" if "mp4" in str(getattr(audio, "mime_type", "")) else ".webm")))
        out = work_dir / f"{stem}.mp4"
        cmd = [
            ffmpeg_bin, "-y", "-v", "error",
            "-i", str(vpath), "-i", str(apath),
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart", str(out),
        ]
        completed = subprocess.run(cmd, capture_output=True, text=True)
        if completed.returncode != 0 or not out.exists():
            raise RuntimeError(completed.stderr[-800:] or "ffmpeg merge failed")
        vpath.unlink(missing_ok=True)
        apath.unlink(missing_ok=True)
        return out.resolve()
    except MediaTransportError:
        raise
    except Exception as exc:
        raise MediaTransportError(
            f"pytubefix video fallback failed: {exc}",
            kind=ErrorKind.UNKNOWN,
            route="pytubefix",
        ) from exc


def download_audio(url: str, work_dir: Path, stem: str, ffmpeg_bin: str, quality: str = "160k") -> Path:
    try:
        from pytubefix import YouTube
        yt = YouTube(url)
        streams = [s for s in yt.streams if bool(getattr(s, "includes_audio_track", False))]
        audio_only = [s for s in streams if not bool(getattr(s, "includes_video_track", False))]
        candidates = audio_only or streams
        if not candidates:
            raise RuntimeError("No audio stream available")
        stream = max(candidates, key=lambda s: int(getattr(s, "filesize", 0) or getattr(s, "filesize_approx", 0) or 0))
        suffix = ".m4a" if "mp4" in str(getattr(stream, "mime_type", "") or "") else ".webm"
        source = Path(stream.download(output_path=str(work_dir), filename=f"{stem}.source{suffix}"))
        out = work_dir / f"{stem}.mp3"
        cmd = [ffmpeg_bin, "-y", "-v", "error", "-i", str(source), "-vn", "-c:a", "libmp3lame", "-b:a", quality.lower(), str(out)]
        completed = subprocess.run(cmd, capture_output=True, text=True)
        if completed.returncode != 0 or not out.exists():
            raise RuntimeError(completed.stderr[-800:] or "ffmpeg audio conversion failed")
        source.unlink(missing_ok=True)
        return out.resolve()
    except Exception as exc:
        raise MediaTransportError(
            f"pytubefix audio fallback failed: {exc}",
            kind=ErrorKind.UNKNOWN,
            route="pytubefix",
        ) from exc
