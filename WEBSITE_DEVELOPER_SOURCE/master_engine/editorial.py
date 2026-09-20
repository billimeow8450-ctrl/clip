"""Transcript-grounded result copy and an explicitly uncalibrated rubric score.

No invented quotation, clickbait claim, scraped popularity figure or claim of
platform measurement. Source speech remains in its original order.
"""
import re
from collections import Counter
from statistics import mean


def build_editorial(transcript,media,profile):
    text=re.sub(r"\s+"," ",transcript.text).strip()
    sentences=[s.strip() for s in re.split(r"(?<=[.!?])\s+",text) if s.strip()]
    opening=" ".join(w.text for w in transcript.words if w.start<=8).strip() or text[:240]
    candidates=[s for s in sentences if 5<=len(s.split())<=28][:5]
    candidates=candidates+([opening] if opening else [])
    def hook_weight(value):
        tokens=value.lower().split()
        return (3 if "?" in value else 0)+sum(word in tokens for word in ("why","never","but","because","first","lost","changed","realised","realized"))
    chosen=max(candidates,key=hook_weight) if candidates else ""
    first_sentence=re.split(r"(?<=[.!?])\s+",chosen,maxsplit=1)[0]
    if 4<=len(first_sentence.split())<=10:
        chosen=first_sentence
    tokens=chosen.split()
    hook=" ".join(tokens[:10]).strip(" ,;:")
    if len(tokens)>10:
        hook=hook.rstrip(".!?")+"…"
    if not hook:
        hook="Edited video"
    content_tokens=re.findall(r"[A-Za-z']+",text.lower())
    opening_tokens=set(re.findall(r"[A-Za-z']+",opening.lower()))
    seconds=max(.1,media.duration)
    wpm=len(transcript.words)*60/seconds
    quality=mean([w.probability for w in transcript.words]) if transcript.words else 0
    early_question=bool("?" in opening or opening_tokens.intersection({"why","how","what","would","did"}))
    contrast=bool(opening_tokens.intersection({"but","never","until","however","instead","actually"}))
    concrete=bool(re.search(r"\d",text) or set(content_tokens).intersection({"mother","father","child","children","work","money","marriage","family","doctor"}))
    dimensions={
        "opening_hook": 10+8*early_question+6*contrast,
        "specificity": 7+8*concrete,
        "spoken_pace": 15 if 95<=wpm<=190 else 9 if 65<=wpm<=225 else 4,
        "transcript_clarity": round(15*max(0,min(1,quality))),
        "self_contained_structure": 15 if len(sentences)>=2 and text.endswith((".","?","!")) else 8,
        "duration_fit": 10 if 25<=seconds<=120 else 6,
    }
    enough=len(transcript.words)>=12
    score=min(92,max(1,round(sum(dimensions.values())))) if enough else None
    topics={
        "podcast_interview":["#Podcast","#Interview"],
        "story_commentary":["#Podcast","#Storytime"],
        "finance_business":["#Business","#Money"],
        "education_explainer":["#Learning","#Explained"],
    }.get(profile.category,["#Conversation","#Podcast"])
    description=(sentences[0] if sentences else text)[:200]
    return {"title":hook,"hook":hook,"description":description,
        "hashtags":" ".join(topics),"viral_potential_score":score,
        "score_type":"local editorial rubric estimate; not a prediction or TikTok measurement",
        "score_components":dimensions,"speech_words_per_minute":round(wpm,1),
        "hook_source":"verbatim transcript excerpt, shortened without adding claims",
        "score_note":"Estimated, not guaranteed" if enough else "Insufficient speech to score"}


def telegram_caption(analysis):
    data=analysis.get("editorial") or {}
    score=data.get("viral_potential_score")
    rating=f"{score}/100 (estimate)" if score is not None else "N/A — insufficient speech"
    label=str(analysis.get("caption_style") or "Auto-selected").replace("_"," ").title()
    return (f"🎬 {data.get('title') or 'Master Edit'}\n"
        f"🔥 Viral Potential: {rating}\n\n"
        f"{data.get('description') or ''}\n\n"
        f"{data.get('hashtags') or ''}\n\n"
        f"🎨 {label}\nNo added music or B-roll.")[:1000]
