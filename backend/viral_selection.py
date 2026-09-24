"""Bot-derived local viral clip selection for the web worker.

This deliberately contains no Telegram state or handlers.  It is the same
local fallback philosophy used by the bot: transcript semantics are ranked
first, the strongest candidates are verified with audio/visual signals, and
overlapping or incomplete moments are rejected.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


@dataclass(frozen=True)
class Paragraph:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class Candidate:
    start: float
    end: float
    score: int
    reason: str
    hook: str
    text: str


def paragraphs_from_words(words: Iterable[object], *, gap: float = 1.1) -> list[Paragraph]:
    """Group word timestamps into readable thought-sized transcript windows."""
    result: list[Paragraph] = []
    current: list[str] = []
    start = end = 0.0
    for word in words:
        text = re.sub(r"\s+", " ", str(getattr(word, "text", "") or "")).strip()
        if not text:
            continue
        word_start = float(getattr(word, "start", 0.0) or 0.0)
        word_end = max(word_start, float(getattr(word, "end", word_start) or word_start))
        if current and (word_start - end > gap or len(" ".join(current)) > 340):
            result.append(Paragraph(start, end, " ".join(current)))
            current = []
        if not current:
            start = word_start
        current.append(text)
        end = word_end
        if text.endswith((".", "!", "?")) and end - start >= 4.0:
            result.append(Paragraph(start, end, " ".join(current)))
            current = []
    if current:
        result.append(Paragraph(start, end, " ".join(current)))
    return result


def _text_score(text: str) -> tuple[float, list[str]]:
    value = text.lower()
    score, reasons = 35.0, []
    groups = {
        "emotional language": ("shocking", "crazy", "secret", "truth", "never", "worst", "best", "afraid", "love", "hate", "died", "risk"),
        "conflict or controversy": ("lie", "fraud", "fake", "scam", "illegal", "banned", "exposed", "proof", "mistake", "fight"),
        "strong quotable phrasing": ("the reason", "the truth is", "nobody tells", "i realized", "the biggest", "you need to", "here's why", "imagine"),
    }
    for reason, terms in groups.items():
        hits = sum(term in value for term in terms)
        if hits:
            score += min(18, hits * 4.5)
            reasons.append(reason)
    if "?" in text:
        score += 7; reasons.append("curiosity question")
    if "!" in text:
        score += 4
    if re.search(r"\b\d+(?:\.\d+)?%?\b", text):
        score += 4; reasons.append("specific detail")
    words = len(text.split())
    if 30 <= words <= 180:
        score += 5
    return min(100.0, score), reasons


def _audio_energy(source: Path, start: float, end: float) -> float:
    try:
        out = subprocess.run(
            ["ffmpeg", "-hide_banner", "-ss", f"{start:.3f}", "-t", f"{min(20.0, end-start):.3f}", "-i", str(source), "-vn", "-af", "volumedetect", "-f", "null", "-"],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, timeout=35,
        )
        match = re.search(r"max_volume:\s*(-?\d+(?:\.\d+)?)\s*dB", out.stderr or "")
        return max(0.0, min(100.0, ((float(match.group(1)) + 30) / 27) * 100)) if match else 50.0
    except Exception:
        return 50.0


def _visual_energy(source: Path, start: float, end: float) -> float:
    try:
        import cv2  # type: ignore
        cap = cv2.VideoCapture(str(source))
        previous, changes = None, []
        for index in range(10):
            cap.set(cv2.CAP_PROP_POS_MSEC, (start + (end-start) * index / 9) * 1000)
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            gray = cv2.cvtColor(cv2.resize(frame, (160, 90)), cv2.COLOR_BGR2GRAY)
            if previous is not None:
                changes.append(float(cv2.absdiff(gray, previous).mean()))
            previous = gray
        cap.release()
        return max(0.0, min(100.0, (sum(changes) / len(changes)) * 5.2)) if changes else 50.0
    except Exception:
        return 50.0


def _hook(text: str) -> str:
    sentence = next((v.strip() for v in re.split(r"(?<=[.!?])\s+", text) if 18 <= len(v.strip()) <= 95), text).strip(" .!?")
    return sentence[:79].rsplit(" ", 1)[0] + "..." if len(sentence) > 82 else sentence or "You need to hear this"


def _overlap(a: Candidate, b: Candidate) -> float:
    shared = max(0.0, min(a.end, b.end) - max(a.start, b.start))
    return shared / max(0.1, min(a.end-a.start, b.end-b.start))


def choose_candidates(source: Path, paragraphs: Sequence[Paragraph], duration: float, count: int, target_seconds: int) -> list[Candidate]:
    """Select non-overlapping, natural clips using the bot's local scoring model."""
    raw: list[Candidate] = []
    for index, paragraph in enumerate(paragraphs):
        group = paragraphs[max(0, index-1):min(len(paragraphs), index+2)]
        text = " ".join(item.text for item in group).strip()
        if len(text.split()) < 12:
            continue
        core_start, core_end = group[0].start, group[-1].end
        # Complete the thought with surrounding context, bounded by the chosen duration.
        wanted = max(20.0, min(float(target_seconds or 60), 90.0))
        start = max(0.0, min(core_start - max(4.0, (wanted-(core_end-core_start))/2), duration-wanted))
        end = min(duration, max(core_end + 6.0, start + wanted))
        base, reasons = _text_score(text)
        audio, visual = _audio_energy(source, start, end), _visual_energy(source, start, end)
        pace = len(text.split()) / max(1.0, end-start)
        pace_score = max(0.0, min(100.0, 100.0 - abs(pace-2.55)*32.0))
        score = int(round(min(100.0, base*.52 + audio*.20 + visual*.18 + pace_score*.10)))
        reason = ", ".join((reasons + (["strong vocal energy"] if audio >= 68 else []) + (["good visual movement"] if visual >= 62 else []))[:3]) or "balanced transcript, audio and visual signals"
        raw.append(Candidate(start, end, max(1, score), reason, _hook(text), text))
    selected: list[Candidate] = []
    for item in sorted(raw, key=lambda candidate: candidate.score, reverse=True):
        if any(_overlap(item, chosen) >= .60 for chosen in selected):
            continue
        selected.append(item)
        if len(selected) >= count:
            break
    return sorted(selected, key=lambda candidate: candidate.start)
