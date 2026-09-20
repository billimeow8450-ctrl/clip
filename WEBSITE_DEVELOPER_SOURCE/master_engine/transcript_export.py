"""Create a readable transcript of the edited timeline with exact timestamps."""
from __future__ import annotations

import re
from pathlib import Path

from .timeline import map_words


def _stamp(value: float) -> str:
    centis=max(0,round(float(value)*100))
    hours,remainder=divmod(centis,360000)
    minutes,remainder=divmod(remainder,6000)
    seconds,centis=divmod(remainder,100)
    return (f"{hours:02d}:{minutes:02d}:{seconds:02d}.{centis:02d}"
            if hours else f"{minutes:02d}:{seconds:02d}.{centis:02d}")


def _groups(words):
    groups=[]
    current=[]
    for word in sorted(words,key=lambda item:(item.start,item.end)):
        text=re.sub(r"\s+"," ",str(word.text or "")).strip()
        if not text:
            continue
        if current and (word.start-current[-1][1]>.65 or len(current)>=14
                        or word.end-current[0][0]>6.5):
            groups.append(current)
            current=[]
        current.append((word.start,word.end,text))
        if text.endswith((".","?","!")) and len(current)>=3:
            groups.append(current)
            current=[]
    if current:
        groups.append(current)
    return groups


def write_transcript(plan, path: Path) -> Path:
    """Export what remains in the final edit, not removed source pauses."""
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    words=map_words(plan.transcript.words,plan.spans)
    lines=["MASTER EDIT TRANSCRIPT",f"Language: {plan.transcript.language or 'unknown'}",""]
    for group in _groups(words):
        text=" ".join(item[2] for item in group)
        lines.append(f"[{_stamp(group[0][0])} - {_stamp(group[-1][1])}] {text}")
        lines.append("")
    if not words:
        fallback=re.sub(r"\s+"," ",str(plan.transcript.text or "")).strip()
        lines.append(fallback or "No speech was detected in this edit.")
        lines.append("")
    path.write_text("\n".join(lines),encoding="utf-8")
    return path
