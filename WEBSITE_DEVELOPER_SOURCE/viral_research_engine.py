from __future__ import annotations

import json
import os
import re
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import yt_dlp

COMMENT_LIMIT = max(20, int(os.getenv("VIRAL_RESEARCH_COMMENT_LIMIT", "120")))
CACHE_TTL_SECONDS = max(300, int(os.getenv("VIRAL_RESEARCH_CACHE_SECONDS", "21600")))
CACHE_DIR = Path(os.getenv("VIRAL_RESEARCH_CACHE_DIR", str(Path(__file__).resolve().parent / "auth" / "research_cache")))
CACHE_DIR.mkdir(parents=True, exist_ok=True)

_TS_RE = re.compile(r"(?<!\d)(\d{1,2}:\d{2}(?::\d{2})?)(?!\d)")
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9#']{2,}")

_STOP = {
    "the","and","for","are","but","not","you","all","can","has","its","our",
    "who","why","how","with","from","this","that","they","was","were","have",
    "just","what","when","your","about","into","than","then","them","his","her",
}

def _safe_id(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip())
    return value[:120] or "unknown"

def _cache_path(video_id: str) -> Path:
    return CACHE_DIR / f"{_safe_id(video_id)}.json"

def _ts_seconds(value: str) -> Optional[float]:
    try:
        parts = [int(x) for x in value.split(":")]
        if len(parts) == 2:
            return float(parts[0] * 60 + parts[1])
        if len(parts) == 3:
            return float(parts[0] * 3600 + parts[1] * 60 + parts[2])
    except Exception:
        return None
    return None

def score_timestamps(comments: List[Dict[str, Any]]) -> Dict[float, float]:
    mentions: Counter = Counter()
    likes: Counter = Counter()

    for row in comments:
        text = str(row.get("text") or "")
        weight = max(1, int(row.get("like_count") or 0) + 1)
        for raw in _TS_RE.findall(text):
            ts = _ts_seconds(raw)
            if ts is None:
                continue
            mentions[ts] += 1
            likes[ts] += weight

    if not mentions:
        return {}

    max_mentions = max(mentions.values()) or 1
    max_likes = max(likes.values()) or 1

    return {
        float(ts): min(
            100.0,
            (mentions[ts] / max_mentions) * 60.0
            + (likes.get(ts, 0) / max_likes) * 40.0,
        )
        for ts in mentions
    }

def build_comment_profile(comments: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not comments:
        return {
            "comment_count": 0,
            "top_comments": [],
            "frequent_terms": [],
            "questions": [],
            "timestamp_mentions": [],
        }

    ranked = sorted(
        comments,
        key=lambda x: int(x.get("like_count") or 0),
        reverse=True,
    )[:25]

    words: List[str] = []
    questions: List[str] = []
    timestamp_examples: List[str] = []

    for row in comments:
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        words.extend(
            w.lower() for w in _WORD_RE.findall(text)
            if w.lower() not in _STOP and len(w) > 3
        )
        if "?" in text and len(questions) < 20:
            questions.append(text[:240])
        if _TS_RE.search(text) and len(timestamp_examples) < 30:
            timestamp_examples.append(text[:240])

    return {
        "comment_count": len(comments),
        "top_comments": [
            {
                "text": str(x.get("text") or "")[:300],
                "like_count": int(x.get("like_count") or 0),
            }
            for x in ranked[:15]
        ],
        "frequent_terms": [w for w, _ in Counter(words).most_common(20)],
        "questions": questions[:12],
        "timestamp_mentions": timestamp_examples[:15],
    }

def _fetch_comments_once(video_url: str, proxy: Optional[str]) -> List[Dict[str, Any]]:
    opts: Dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "getcomments": True,
        "writecomments": True,
        "extractor_args": {
            "youtube": {
                "comment_sort": ["top"],
                "max_comments": [str(COMMENT_LIMIT)],
            }
        },
    }
    if proxy:
        opts["proxy"] = proxy

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(video_url, download=False)

    rows: List[Dict[str, Any]] = []
    for c in (info or {}).get("comments") or []:
        text = str(c.get("text") or "").strip()
        if not text:
            continue
        rows.append({
            "text": text,
            "like_count": int(c.get("like_count") or 0),
        })
        if len(rows) >= COMMENT_LIMIT:
            break
    return rows

def fetch_comments(
    video_url: str,
    proxy_candidates: Optional[Iterable[Optional[str]]] = None,
) -> List[Dict[str, Any]]:
    """
    Direct first. Proxy candidates are only network-reliability fallbacks.
    403/429 are not rotated around.
    """
    routes: List[Optional[str]] = [None]
    for p in proxy_candidates or []:
        if p and p not in routes:
            routes.append(p)
        if len(routes) >= 4:
            break

    last_error = ""
    for proxy in routes:
        try:
            return _fetch_comments_once(video_url, proxy)
        except Exception as exc:
            last_error = str(exc)
            low = last_error.lower()
            if "429" in low or "too many requests" in low or "403" in low or "forbidden" in low:
                break
            continue
    return []

def fetch_tiktok_provider_evidence(subject: str) -> Dict[str, Any]:
    """
    Uses ViralForge's configured social-intelligence provider, if present.
    No undocumented TikTok scraping endpoint is required.
    """
    try:
        from viralforge.core import settings, provider_call
        s = settings()
        if not s.tiktok_url:
            return {}
        result = provider_call(
            s.tiktok_url,
            s.tiktok_key,
            {
                "subject": subject,
                "request": (
                    "successful short-form posts, views/engagement where available, "
                    "topics, hooks, comment themes, positive/negative reactions, "
                    "questions and controversy/debate triggers"
                ),
            },
        )
        return result if isinstance(result, dict) else {"provider_result": result}
    except Exception as exc:
        return {"provider_error": str(exc)[:300]}

def save_research(video_id: str, payload: Dict[str, Any]) -> None:
    path = _cache_path(video_id)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)

def load_research(video_id: str) -> Dict[str, Any]:
    path = _cache_path(video_id)
    try:
        if not path.is_file():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        age = time.time() - float(data.get("updated_at") or 0)
        if age > CACHE_TTL_SECONDS:
            return {}
        # JSON object keys become strings. Normalize timestamp scores.
        raw_scores = data.get("comment_scores") or {}
        data["comment_scores"] = {
            float(k): float(v)
            for k, v in raw_scores.items()
            if _is_number(k) and _is_number(v)
        }
        return data
    except Exception:
        return {}

def _is_number(value: Any) -> bool:
    try:
        float(value)
        return True
    except Exception:
        return False

def collect_research(
    video_url: str,
    video_id: str,
    title: str,
    proxy_candidates: Optional[Iterable[Optional[str]]] = None,
) -> Dict[str, Any]:
    existing = load_research(video_id)
    if existing:
        return existing

    comments = fetch_comments(video_url, proxy_candidates)
    comment_scores = score_timestamps(comments)
    comment_profile = build_comment_profile(comments)
    tiktok_profile = fetch_tiktok_provider_evidence(title)

    payload = {
        "video_id": video_id,
        "title": title,
        "comment_scores": comment_scores,
        "comment_profile": comment_profile,
        "tiktok_profile": tiktok_profile,
        "updated_at": time.time(),
    }
    save_research(video_id, payload)
    return payload

def schedule_research(
    video_url: str,
    video_id: str,
    title: str,
    proxy_candidates: Optional[Iterable[Optional[str]]] = None,
) -> None:
    def worker() -> None:
        try:
            collect_research(
                video_url,
                video_id,
                title,
                list(proxy_candidates or []),
            )
        except Exception:
            # Research is additive; it must never crash the bot or clipper.
            return

    threading.Thread(
        target=worker,
        name=f"viral-research-{_safe_id(video_id)[:24]}",
        daemon=True,
    ).start()

def boost_segments_with_research(
    segments: List[Any],
    research: Dict[str, Any],
) -> List[Any]:
    if not segments or not research:
        return segments

    scores: Dict[float, float] = research.get("comment_scores") or {}
    profile = research.get("comment_profile") or {}
    social = research.get("tiktok_profile") or {}

    top_timestamps = sorted(
        scores.items(),
        key=lambda x: x[1],
        reverse=True,
    )[:8]

    terms = set(
        str(x).lower()
        for x in (
            list(profile.get("frequent_terms") or [])[:20]
            + list(social.get("trending_terms") or [])[:20]
            + list(social.get("proven_topics") or [])[:20]
        )
        if str(x).strip()
    )

    for seg in segments:
        reasons: List[str] = []
        strongest = 0.0

        for ts, evidence_score in top_timestamps:
            if float(seg.start) <= float(ts) <= float(seg.end):
                strongest = max(strongest, float(evidence_score))

        if strongest > 0:
            # Up to 30% evidence boost; stronger comment consensus gets more weight.
            multiplier = 1.0 + 0.30 * min(1.0, strongest / 100.0)
            seg.score = min(100, int(round(float(seg.score) * multiplier)))
            reasons.append("audience timestamp evidence")

        text = (
            str(getattr(seg, "text", "") or "") + " "
            + str(getattr(seg, "hook", "") or "")
        ).lower()
        matched = [term for term in terms if len(term) >= 4 and term in text]
        if matched:
            seg.score = min(100, int(round(float(seg.score) * 1.15)))
            reasons.append("audience/topic match")

        if reasons:
            existing = str(getattr(seg, "reason", "") or "").strip()
            suffix = "; ".join(reasons)
            seg.reason = (existing + ("; " if existing else "") + suffix)[:300]

    return sorted(segments, key=lambda x: float(getattr(x, "score", 0)), reverse=True)
