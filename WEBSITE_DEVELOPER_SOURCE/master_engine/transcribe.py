from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Callable, List, Optional

from .config import CACHE_DIR, Settings
from .models import MediaInfo, Transcript, Word
from .utils import CommandError, emit, write_json


def _key(media: MediaInfo, settings: Settings) -> str:
    stat = media.path.stat()
    value = f"{media.path}:{stat.st_size}:{stat.st_mtime_ns}:{settings.whisper_model}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def _clean(text: str) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    return re.sub(r"\s+([,.;:!?])", r"\1", value)


def _load_cache(path: Path) -> Optional[Transcript]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        words = [Word(**row) for row in data.get("words", [])]
        return Transcript(words=words, text=str(data.get("text") or ""), language=str(data.get("language") or "und"))
    except Exception:
        return None


def transcribe(
    media: MediaInfo,
    settings: Settings,
    *,
    progress=None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> Transcript:
    if not media.has_audio:
        return Transcript(words=[], text="", language="und")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = CACHE_DIR / f"transcript_{_key(media, settings)}.json"
    existing = _load_cache(cache) if cache.exists() else None
    if existing:
        emit(progress, "transcript", 1.0, "Cached word timings loaded")
        return existing
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise CommandError(
            "faster-whisper is missing from the editingbot Python environment"
        ) from exc
    emit(progress, "transcript", 0.02, f"Loading {settings.whisper_model}")
    try:
        model = WhisperModel(
            settings.whisper_model,
            device=settings.whisper_device,
            compute_type=settings.whisper_compute_type,
            cpu_threads=max(1, min(12, int(__import__("os").cpu_count() or 4))),
            num_workers=1,
            download_root=str(CACHE_DIR / "whisper"),
        )
    except Exception:
        model = WhisperModel(
            "small.en",
            device="cpu",
            compute_type="int8",
            cpu_threads=max(1, min(8, int(__import__("os").cpu_count() or 4))),
            num_workers=1,
            download_root=str(CACHE_DIR / "whisper"),
        )
    segments, info = model.transcribe(
        str(media.path),
        beam_size=5,
        best_of=5,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 420},
        word_timestamps=True,
        condition_on_previous_text=True,
    )
    words: List[Word] = []
    for segment in segments:
        if cancel_check and cancel_check():
            raise CommandError("Master Editor job cancelled")
        for raw in list(getattr(segment, "words", None) or []):
            text = _clean(getattr(raw, "word", ""))
            if not text:
                continue
            start = max(0.0, float(getattr(raw, "start", 0.0) or 0.0))
            end = max(start + 0.04, float(getattr(raw, "end", start + 0.04) or start + 0.04))
            words.append(
                Word(
                    start=start,
                    end=min(media.duration, end),
                    text=text,
                    probability=float(getattr(raw, "probability", 1.0) or 1.0),
                )
            )
        if words:
            emit(progress, "transcript", min(0.98, words[-1].end / media.duration), "Creating precise word timing")
    result = Transcript(
        words=words,
        text=_clean(" ".join(word.text for word in words)),
        language=str(getattr(info, "language", "und") or "und"),
    )
    write_json(
        cache,
        {
            "language": result.language,
            "text": result.text,
            "words": [word.__dict__ for word in result.words],
        },
    )
    emit(progress, "transcript", 1.0, f"{len(words)} timed words ready")
    return result
