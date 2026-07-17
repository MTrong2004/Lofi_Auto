"""
AI FILE NOTE - CAPTION WRITER (sinh title/description/hashtag cho upload YouTube)

Chức năng chính:
- Gọi endpoint OpenAI-compatible (PROMPT_API_URL, mặc định text.pollinations.ai miễn phí)
  để viết title, description và hashtag theo bài nhạc/mood.
- Luôn có fallback deterministic khi AI lỗi hoặc bị tắt, nên upload không bao giờ kẹt.
- Tự chèn credit nhạc (từ MetadataStore) vào description và chuẩn hóa hashtag/tags
  theo giới hạn của YouTube (title ≤ 100 ký tự, tags ≤ 500 ký tự tổng).

API được file khác sử dụng:
- generate_caption()

Lưu ý khi sửa:
- Không ghi API key vào kết quả trả về hoặc log.
- Kết quả AI phải qua _normalize_caption() vì model có thể trả thiếu/bừa trường.
"""
from __future__ import annotations

import json
import re
from typing import Any

import requests

_MAX_TITLE = 100
_MAX_TAGS_TOTAL = 480  # YouTube giới hạn ~500 ký tự cho toàn bộ tags
_MAX_HASHTAGS = 15     # YouTube chỉ đọc tối đa 15 hashtag đầu


def _audience_intent(music_tags: list[str] | None) -> list[str]:
    """Infer listening intent from tags to guide caption tone."""
    tags = " ".join(str(tag).lower() for tag in (music_tags or []))
    intents: list[str] = []
    for words, label in (
        (("study", "focus", "work", "deep focus"), "study and focus"),
        (("sleep", "night", "ambient", "calm"), "rest and unwind"),
        (("rain", "cozy", "coffee", "chill"), "a calm background atmosphere"),
    ):
        if any(word in tags for word in words) and label not in intents:
            intents.append(label)
    return intents[:2] or ["study, work, and relaxation"]


def _caption_facts(track: dict | None, music_tags: list[str] | None) -> dict[str, Any]:
    """Limit model context to verifiable track facts."""
    source = track or {}
    fields = ("title", "author", "album", "genre", "language", "license", "rights_status")
    facts = {key: str(source.get(key)).strip()[:160] for key in fields if source.get(key)}
    facts["music_tags"] = [str(tag).strip()[:40] for tag in (music_tags or []) if str(tag).strip()][:8]
    return facts


def _duration_label(duration_seconds: float, language: str) -> str:
    minutes = int(round(float(duration_seconds or 0) / 60))
    if minutes >= 55:
        hours = max(1, int(round(minutes / 60)))
        return f"{hours} tiếng" if language == "vi" else f"{hours} Hour"
    if minutes >= 1:
        return f"{minutes} phút" if language == "vi" else f"{minutes} Min"
    return "Short" if language != "vi" else "Ngắn"


def _clean_hashtag(value: str) -> str:
    text = re.sub(r"[^0-9A-Za-zÀ-ỹ]+", "", str(value or "").strip().lstrip("#"))
    return f"#{text}" if text else ""


def _normalize_caption(raw: dict[str, Any], fallback: dict[str, Any], credit_text: str) -> dict[str, Any]:
    title = str(raw.get("title") or fallback["title"]).strip()[:_MAX_TITLE]
    description = str(raw.get("description") or fallback["description"]).strip()[:4500]
    hashtags = []
    for item in raw.get("hashtags") or []:
        tag = _clean_hashtag(str(item))
        if tag and tag.lower() not in {t.lower() for t in hashtags}:
            hashtags.append(tag)
    hashtags = hashtags[:_MAX_HASHTAGS] or fallback["hashtags"]

    tags: list[str] = []
    total = 0
    for item in raw.get("tags") or fallback["tags"]:
        tag = str(item).strip()[:60]
        if not tag or tag.lower() in {t.lower() for t in tags}:
            continue
        if total + len(tag) > _MAX_TAGS_TOTAL:
            break
        tags.append(tag)
        total += len(tag)

    if credit_text and credit_text not in description:
        description = f"{description}\n\n{credit_text}"
    hashtag_line = " ".join(hashtags)
    if hashtag_line and hashtag_line not in description:
        description = f"{description}\n\n{hashtag_line}"
    return {
        "title": title,
        "description": description[:4900],
        "hashtags": hashtags,
        "tags": tags,
        "source": str(raw.get("source") or "ai"),
    }


def fallback_caption(
    track: dict | None,
    music_tags: list[str] | None,
    duration_seconds: float,
    language: str = "vi",
    video_index: int = 1,
) -> dict[str, Any]:
    """Caption deterministic khi AI lỗi/tắt; vẫn đủ title, description, hashtag, tags."""
    title_track = str((track or {}).get("title") or "Lofi Chill Beats").strip()
    mood = ", ".join((music_tags or [])[:3])
    duration_text = _duration_label(duration_seconds, language)
    if language == "vi":
        title = f"{title_track} - Lofi Chill {duration_text} | Nhạc học bài, thư giãn #{video_index}"
        description = (
            f"{duration_text} nhạc lofi chill để học bài, làm việc và thư giãn.\n"
            f"Mood: {mood or 'chill, thư giãn'}.\n"
            "Đeo tai nghe và tận hưởng nhé!"
        )
    else:
        title = f"{title_track} - Lofi Chill Beats {duration_text} | Study & Relax #{video_index}"
        description = (
            f"{duration_text} of chill lofi beats for studying, working and relaxing.\n"
            f"Mood: {mood or 'chill, calm'}.\n"
            "Put on your headphones and enjoy!"
        )
    hashtags = ["#lofi", "#lofichill", "#studymusic", "#relaxmusic", "#chillbeats"]
    tags = [
        "lofi", "lofi chill", "study music", "relax music", "chill beats",
        "lofi hip hop", "nhạc lofi", "nhạc học bài", "lofi study", "sleep music",
    ]
    return {
        "title": title[:_MAX_TITLE],
        "description": description,
        "hashtags": hashtags,
        "tags": tags,
        "source": "fallback",
    }


def generate_caption(
    track: dict | None,
    music_tags: list[str] | None = None,
    *,
    duration_seconds: float = 3600,
    language: str = "vi",
    video_index: int = 1,
    credit_text: str = "",
    api_url: str = "",
    api_key: str = "",
    model: str = "openai",
    timeout: int = 40,
    enabled: bool = True,
    channel_profile: str = "",
) -> dict[str, Any]:
    """Sinh caption bằng AI; mọi lỗi đều rơi về fallback deterministic."""
    fallback = fallback_caption(track, music_tags, duration_seconds, language, video_index)
    if not enabled or not str(api_url or "").strip():
        return _normalize_caption({}, fallback, credit_text)

    lang_name = "Vietnamese" if language == "vi" else "English"
    profile = str(channel_profile or "").strip()[:500] or (
        "Warm, calm lofi channel. Helpful and natural, never clickbait or keyword-stuffed."
    )
    system = (
        "You are the senior YouTube metadata editor for a lofi music channel. "
        "Write useful metadata for a real listener, not generic SEO filler. Return JSON only with keys: "
        f"title (natural {lang_name}, max 95 characters; include the length and one specific mood or use case), "
        f"description (natural {lang_name}, 3-6 short lines: opening atmosphere, listener use case, "
        "one concrete mood/detail from the supplied facts, then a gentle call-to-action; no hashtags, links, or emoji spam), "
        "hashtags (8-12 distinct lowercase items without '#'; broad plus specific, never duplicated), "
        "tags (10-15 distinct plain YouTube search phrases; prioritize phrases people would actually search). "
        "Use only supplied facts. Do not invent artist credits, licensing/copyright claims, chart claims, collaborations, "
        "or the words 'official', 'copyright free', or 'original' unless explicitly supplied. "
        "Avoid title templates, repeated keywords, all caps, and unsupported promises such as sleep cures."
    )
    user = json.dumps({
        "channel_voice": profile,
        "verified_track_facts": _caption_facts(track, music_tags),
        "video_length": _duration_label(duration_seconds, language),
        "video_index": video_index,
        "listener_intent": _audience_intent(music_tags),
        "quality_check": [
            "Title sounds human and is not a keyword list.",
            "Description contains no invented facts or hashtags.",
            "Tags and hashtags are unique and relevant to the facts.",
        ],
    }, ensure_ascii=False)
    try:
        # Dùng hàm LLM chung (fallback model/provider); primary = tham số truyền vào (config.PROMPT_API_*).
        from utils.helpers import call_llm_chat
        content = call_llm_chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            json_mode=True, max_tokens=700, temperature=0.45, timeout=timeout,
            primary=(api_url, api_key, model),
        )
        if not content:
            return _normalize_caption({}, fallback, credit_text)
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", str(content).strip(), flags=re.I)
        raw = json.loads(cleaned)
        raw["source"] = "ai"
        return _normalize_caption(raw, fallback, credit_text)
    except Exception as exc:
        result = _normalize_caption({}, fallback, credit_text)
        result["error"] = str(exc)[:300]
        return result
