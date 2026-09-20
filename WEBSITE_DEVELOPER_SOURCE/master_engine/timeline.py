from __future__ import annotations

import math
import subprocess
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from .models import MediaInfo, SourceSpan, Transcript, Word
from .utils import CommandError, cancelled, emit


def sample_audio_energy(
    media: MediaInfo,
    *,
    window_seconds: float = 0.10,
    progress=None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> List[float]:
    if not media.has_audio:
        return []
    try:
        import numpy as np
    except ImportError as exc:
        raise CommandError("NumPy is required for audio analysis") from exc
    rate = 16000
    frame_count = max(1, int(rate * window_seconds))
    byte_count = frame_count * 4
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(media.path),
        "-vn", "-ac", "1", "-ar", str(rate), "-f", "f32le", "pipe:1",
    ]
    proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    values: List[float] = []
    assert proc.stdout is not None
    try:
        while True:
            if cancelled(cancel_check):
                proc.terminate()
                raise CommandError("Master Editor job cancelled")
            data = proc.stdout.read(byte_count)
            if not data:
                break
            samples = np.frombuffer(data, dtype=np.float32)
            rms = float(np.sqrt(np.mean(samples * samples))) if samples.size else 0.0
            values.append(rms)
            if len(values) % 200 == 0:
                emit(progress, "audio_analysis", min(0.99, len(values) * window_seconds / media.duration), "Mapping speech, pauses and music")
    finally:
        if proc.poll() is None:
            proc.terminate()
        _, stderr = proc.communicate()
    if proc.returncode not in (0, None):
        raise CommandError("Audio analysis failed: " + stderr.decode("utf-8", "ignore")[-500:])
    emit(progress, "audio_analysis", 1.0, "Audio energy map ready")
    return values


def energy_at(values: Sequence[float], time_value: float, window_seconds: float = 0.10) -> float:
    if not values:
        return 0.0
    index = max(0, min(len(values) - 1, int(time_value / window_seconds)))
    return float(values[index])


def _quiet_gap(values: Sequence[float], start: float, end: float, window: float = 0.10) -> bool:
    if not values or end <= start:
        return False
    lo = max(0, int(start / window))
    hi = min(len(values), max(lo + 1, int(math.ceil(end / window))))
    region = sorted(float(v) for v in values[lo:hi])
    all_values = sorted(float(v) for v in values)
    if not region or not all_values:
        return False
    floor = all_values[max(0, int(len(all_values) * 0.20) - 1)]
    median = region[len(region) // 2]
    return median <= max(0.004, floor * 2.2)


def build_spans(
    media: MediaInfo,
    transcript: Transcript,
    energy: Sequence[float],
    *,
    silence_cut_seconds: float,
    enabled: bool,
) -> List[SourceSpan]:
    if not enabled or len(transcript.words) < 2:
        return [SourceSpan(0.0, media.duration, 0.0)]
    cuts: List[Tuple[float, float]] = []
    keep_edge = 0.28
    for previous, current in zip(transcript.words, transcript.words[1:]):
        gap_start = previous.end
        gap_end = current.start
        gap = gap_end - gap_start
        if gap < silence_cut_seconds:
            continue
        cut_start = gap_start + keep_edge
        cut_end = gap_end - keep_edge
        if cut_end - cut_start < 0.55:
            continue
        if _quiet_gap(energy, cut_start, cut_end):
            cuts.append((cut_start, cut_end))
    # Keep the edit natural: never create micro source fragments.
    merged: List[Tuple[float, float]] = []
    for start, end in cuts:
        if merged and start <= merged[-1][1] + 0.15:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    # Never leave a tiny final source island.  FFmpeg 4.4 can hit a native
    # swscale/framesync crash when a complex 4K composition receives only one
    # frame.  Keeping the final pause is editorially invisible and guarantees
    # every retained span has enough real decoded frames.
    while merged and media.duration-merged[-1][1] < .55:
        merged.pop()
    spans: List[SourceSpan] = []
    cursor = 0.0
    output_cursor = 0.0
    for cut_start, cut_end in merged:
        if cut_start - cursor >= 0.55:
            spans.append(SourceSpan(cursor, cut_start, output_cursor))
            output_cursor += cut_start - cursor
        cursor = max(cursor, cut_end)
    if media.duration - cursor >= 0.55:
        spans.append(SourceSpan(cursor, media.duration, output_cursor))
    return spans or [SourceSpan(0.0, media.duration, 0.0)]


def map_source_time(spans: Sequence[SourceSpan], source_time: float) -> Optional[float]:
    for span in spans:
        if span.source_start - 1e-6 <= source_time <= span.source_end + 1e-6:
            return span.output_start + max(0.0, source_time - span.source_start)
    return None


def map_words(words: Iterable[Word], spans: Sequence[SourceSpan]) -> List[Word]:
    result: List[Word] = []
    for word in words:
        for span in spans:
            if word.end < span.source_start or word.start > span.source_end:
                continue
            clipped_start = max(word.start, span.source_start)
            clipped_end = min(word.end, span.source_end)
            if clipped_end <= clipped_start:
                continue
            start = span.output_start + clipped_start - span.source_start
            end = span.output_start + clipped_end - span.source_start
            result.append(Word(start=start, end=max(start + 0.04, end), text=word.text, probability=word.probability))
            break
    return result
