from __future__ import annotations

import re
from collections import Counter
from typing import Iterable, List, Sequence, Tuple

from .models import Chapter, GraphicEvent, StoryBeat, Transcript, Word


STOP = {
    "a", "an", "and", "are", "as", "at", "be", "been", "but", "by",
    "for", "from", "had", "has", "have", "he", "her", "his", "i", "if",
    "in", "is", "it", "its", "me", "my", "of", "on", "or", "our",
    "she", "so", "that", "the", "their", "them", "they", "this", "to",
    "was", "we", "were", "what", "when", "with", "you", "your",
}
HOOK_TERMS = {
    "secret", "truth", "mistake", "warning", "never", "best", "worst",
    "why", "how", "problem", "because", "changed", "important", "imagine",
    "actually", "surprising", "risk", "money", "million", "billion",
}
CONTRAST = {"but", "however", "instead", "although", "yet", "except"}


def _token(value: str) -> str:
    return re.sub(r"[^a-z0-9']+", "", value.lower())


def _keywords(words: Iterable[str], limit: int = 4) -> Tuple[str, ...]:
    counts = Counter(_token(word) for word in words)
    rows = [word for word, _ in counts.most_common() if len(word) >= 3 and word not in STOP]
    return tuple(rows[:limit])


def build_story(transcript: Transcript, duration: float) -> Tuple[List[StoryBeat], List[Chapter]]:
    if not transcript.words:
        return [], [Chapter(0.0, duration, "Visual story", ())]
    groups: List[List[Word]] = []
    current: List[Word] = []
    for index, word in enumerate(transcript.words):
        current.append(word)
        text = word.text.strip()
        next_word = transcript.words[index + 1] if index + 1 < len(transcript.words) else None
        gap = (next_word.start - word.end) if next_word else 9.0
        if text.endswith((".", "?", "!")) or gap >= 0.72 or len(current) >= 24:
            groups.append(current)
            current = []
    if current:
        groups.append(current)

    beats: List[StoryBeat] = []
    for group in groups:
        text = " ".join(word.text for word in group).strip()
        tokens = [_token(word.text) for word in group]
        question = text.endswith("?") or (tokens and tokens[0] in {"why", "how", "what"})
        number = any(any(char.isdigit() for char in token) for token in tokens)
        contrast = any(token in CONTRAST for token in tokens)
        hooks = sum(token in HOOK_TERMS for token in tokens)
        importance = 0.30 + min(0.24, len(tokens) / 90.0)
        importance += min(0.28, hooks * 0.08)
        importance += 0.12 if question else 0.0
        importance += 0.10 if number else 0.0
        importance += 0.09 if contrast else 0.0
        importance = min(1.0, importance)
        if question:
            kind = "question"
        elif number:
            kind = "evidence"
        elif contrast:
            kind = "turn"
        elif hooks:
            kind = "hook"
        else:
            kind = "statement"
        beats.append(StoryBeat(
            start=max(0.0, group[0].start),
            end=min(duration, max(group[0].start + .10, group[-1].end)),
            text=text,
            kind=kind,
            importance=round(importance, 4),
            keywords=_keywords((word.text for word in group)),
        ))

    chapters: List[Chapter] = []
    chapter_start = 0
    for index in range(1, len(beats)):
        previous = beats[index - 1]
        current_beat = beats[index]
        elapsed = current_beat.start - beats[chapter_start].start
        old = set(previous.keywords)
        new = set(current_beat.keywords)
        topic_shift = bool(old and new and not old.intersection(new))
        if elapsed >= 70.0 or (elapsed >= 28.0 and topic_shift and current_beat.importance >= .55):
            indexes = tuple(range(chapter_start, index))
            title = " ".join(beats[chapter_start].keywords[:3]) or f"Part {len(chapters) + 1}"
            chapters.append(Chapter(beats[chapter_start].start, previous.end, title.title(), indexes))
            chapter_start = index
    indexes = tuple(range(chapter_start, len(beats)))
    title = " ".join(beats[chapter_start].keywords[:3]) or f"Part {len(chapters) + 1}"
    chapters.append(Chapter(beats[chapter_start].start, beats[-1].end, title.title(), indexes))
    return beats, chapters


def plan_graphics(beats: Sequence[StoryBeat], duration: float, density: float) -> List[GraphicEvent]:
    if not beats or density <= 0:
        return []
    target = max(1, int(duration / 60.0 * density))
    events: List[GraphicEvent] = []
    last = -999.0
    for beat in sorted(beats, key=lambda row: row.importance, reverse=True):
        if len(events) >= target:
            break
        if any(abs(beat.start - row.source_start) < 10.0 for row in events):
            continue
        if beat.kind not in {"evidence", "question", "hook", "turn"} or beat.importance < .56:
            continue
        text = beat.text.strip()
        if len(text) > 92:
            text = " ".join(text.split()[:12]) + "…"
        kind = "fact" if beat.kind == "evidence" else ("question" if beat.kind == "question" else "key_point")
        events.append(GraphicEvent(beat.start, min(duration, beat.start + 2.8), kind, text))
        last = beat.start
    return sorted(events, key=lambda row: row.source_start)
