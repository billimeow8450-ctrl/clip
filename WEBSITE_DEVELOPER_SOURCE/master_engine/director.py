from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Dict, Iterable, Sequence, Tuple

from .models import EditProfile, MediaInfo, Transcript


CATEGORY_TERMS: Dict[str, set[str]] = {
    "podcast_interview": {
        "podcast", "interview", "guest", "host", "episode", "conversation",
        "asked", "question", "answer", "welcome", "studio", "microphone",
    },
    "education_explainer": {
        "learn", "explain", "because", "example", "lesson", "tutorial", "how",
        "why", "science", "history", "study", "research", "means", "step",
    },
    "finance_business": {
        "money", "business", "market", "stock", "invest", "revenue", "profit",
        "sales", "customer", "company", "tax", "income", "wealth", "startup",
        "bitcoin", "crypto", "economy", "career", "salary", "rpm",
    },
    "health_fitness": {
        "health", "doctor", "sleep", "diet", "protein", "exercise", "workout",
        "body", "brain", "stress", "medical", "muscle", "weight", "nutrition",
    },
    "gaming_sports": {
        "game", "gaming", "player", "score", "team", "match", "goal", "level",
        "win", "lost", "football", "cricket", "basketball", "coach", "stream",
    },
    "product_demo": {
        "product", "feature", "screen", "click", "install", "app", "website",
        "review", "camera", "phone", "device", "software", "tool", "demo",
    },
    "vlog_travel": {
        "travel", "trip", "hotel", "flight", "city", "country", "food", "vlog",
        "today", "morning", "night", "journey", "visit", "street", "restaurant",
    },
    "music_nightlife": {
        "song", "music", "artist", "album", "concert", "dance", "club", "dj",
        "beat", "studio", "singer", "rap", "guitar", "festival",
    },
    "story_commentary": {
        "story", "happened", "remember", "suddenly", "thought", "felt", "truth",
        "crazy", "never", "always", "secret", "problem", "realized", "imagine",
    },
    "news_documentary": {
        "news", "report", "according", "government", "policy", "election",
        "evidence", "documentary", "investigation", "official", "court",
    },
    "real_estate": {
        "property", "house", "home", "mortgage", "rent", "apartment",
        "bedroom", "kitchen", "realtor", "listing", "estate", "land",
    },
    "beauty_fashion": {
        "beauty", "makeup", "skin", "hair", "fashion", "outfit", "style",
        "dress", "serum", "routine", "look", "wear", "cosmetic",
    },
}


PROFILES: Dict[str, EditProfile] = {
    "podcast_interview": EditProfile(
        "podcast_interview", "reference_bold_lime", "active_speaker", "natural",
        1.55, 0.75, 4.2, "calm_documentary", 0.20, "clean_cut", .28, .22, "warm_natural",
    ),
    "education_explainer": EditProfile(
        "education_explainer", "editorial_sentence_gold", "content_safe", "clear",
        1.30, 1.25, 4.5, "clean_corporate", 0.16, "match_cut",
    ),
    "finance_business": EditProfile(
        "finance_business", "editorial_sentence_gold", "content_safe", "authoritative",
        1.35, 1.10, 4.4, "clean_corporate", 0.14, "clean_cut",
    ),
    "health_fitness": EditProfile(
        "health_fitness", "soft_karaoke_ice", "content_safe", "clear",
        1.35, 1.15, 4.2, "warm_documentary", 0.14, "match_cut",
    ),
    "gaming_sports": EditProfile(
        "gaming_sports", "duotone_pulse_rose", "screen_safe", "fast",
        1.90, 0.55, 3.2, "energetic_sport", 0.42, "energy_cut",
    ),
    "product_demo": EditProfile(
        "product_demo", "rounded_plate_ice", "screen_safe", "precise",
        1.20, 1.10, 3.8, "clean_tech", 0.18, "match_cut",
    ),
    "vlog_travel": EditProfile(
        "vlog_travel", "soft_karaoke_ice", "scene_first", "dynamic",
        1.75, 0.75, 3.8, "upbeat_lifestyle", 0.30, "match_cut",
    ),
    "music_nightlife": EditProfile(
        "music_nightlife", "double_deck_orchid", "scene_first", "beat_driven",
        9.0, 0.20, 3.0, "none", 0.48, "energy_cut",
    ),
    "story_commentary": EditProfile(
        "story_commentary", "reference_bold_lime", "face_first", "story",
        1.55, 0.90, 4.0, "warm_documentary", 0.22, "clean_cut", .34, .28, "warm_natural",
    ),
    "news_documentary": EditProfile(
        "news_documentary", "editorial_sentence_gold", "content_safe", "measured",
        1.25, 1.35, 4.6, "calm_documentary", 0.10, "match_cut", .45, .12, "neutral_documentary",
    ),
    "real_estate": EditProfile(
        "real_estate", "soft_karaoke_ice", "scene_first", "smooth",
        1.45, 1.20, 4.2, "upbeat_lifestyle", 0.16, "match_cut", .30, .24, "warm_bright",
    ),
    "beauty_fashion": EditProfile(
        "beauty_fashion", "duotone_pulse_rose", "face_first", "polished",
        1.50, 0.85, 3.8, "upbeat_lifestyle", 0.24, "match_cut", .28, .30, "clean_vibrant",
    ),
}


def _tokens(value: str) -> list[str]:
    return re.findall(r"[a-zA-Z][a-zA-Z0-9']{1,}", value.lower())


def classify(media: MediaInfo, transcript: Transcript) -> Tuple[EditProfile, Dict[str, object]]:
    tokens = _tokens(transcript.text + " " + media.path.stem.replace("_", " "))
    counts = Counter(tokens)
    scores: Dict[str, float] = {}
    for category, terms in CATEGORY_TERMS.items():
        score = sum(min(4, counts.get(term, 0)) for term in terms)
        scores[category] = float(score)
    question_count = transcript.text.count("?")
    if question_count >= 3:
        scores["podcast_interview"] += min(5.0, question_count * 0.6)
    words_per_minute = len(transcript.words) / max(1e-6, media.duration / 60.0)
    if words_per_minute > 175:
        scores["story_commentary"] += 2.0
    if not transcript.words:
        # Visual-first footage should not receive a speech-heavy profile.
        category = "vlog_travel"
    else:
        category = max(scores, key=scores.get)
        if scores[category] <= 1.0:
            category = "story_commentary"
    profile = PROFILES[category]
    return profile, {
        "category_scores": scores,
        "words_per_minute": round(words_per_minute, 2),
        "selected_category": category,
        "duration_policy": "unrestricted",
    }
