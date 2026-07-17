"""
AI FILE NOTE - EFFECT RECOMMENDER (hồ sơ AI + xếp hạng local-first)

Chức năng chính:
- Sinh hồ sơ hiệu ứng bằng AI (OpenAI-compatible) với fallback deterministic.
- Hồ sơ bắt buộc phân loại effect_type (screen_black/chroma_key/alpha/normal)
  kèm thông số chroma key mặc định.
- Query gửi Pixabay phải nói rõ loại asset ("rain green screen",
  "smoke black background overlay"...), không dùng từ khóa chung chung.
- Xếp hạng deterministic: rank_local_effects() cho thư viện manifest local,
  rank_candidates() cho metadata Pixabay. AI không tự chọn/tải file.

API được file khác sử dụng:
- build_effect_profile(), fallback_effect_profile()
- rank_candidates(), rank_local_effects()

Lưu ý khi sửa:
- Giữ mọi giá trị số trong vùng an toàn (_normalize_profile) vì AI có thể trả bừa.
- effect_type trả về phải thuộc EFFECT_TYPES của core/effect_compositor.py.
"""
from __future__ import annotations

import json
import re
from typing import Any

import requests

_ALLOWED_BLEND_MODES = {"screen", "lighten", "overlay", "soft-light", "normal"}
_ALLOWED_EFFECT_TYPES = {"screen_black", "chroma_key", "alpha", "normal"}

# (queries, preferred, avoid, effect_type, blend, opacity, speed)
_FALLBACK_RULES = {
    "rain": (["rain green screen", "window rain drops black background", "soft rain overlay black background"], ["rain", "night", "water"], ["people", "text", "daylight"], "screen_black", "normal", 0.48, 0.9),
    "snow": (["snow particles green screen", "snow falling black background overlay", "winter frost overlay black background"], ["snow", "winter", "slow"], ["people", "text", "camera movement"], "screen_black", "normal", 0.42, 0.8),
    "gaming": (["neon particles black background", "digital glitch overlay black background", "retro scanline overlay"], ["neon", "glitch", "digital"], ["people", "text", "daylight"], "screen_black", "normal", 0.38, 1.1),
    "anime": (["sakura petals green screen", "dreamy particles black background", "soft light leaks black background"], ["petals", "dreamy", "soft"], ["people", "logo", "text"], "screen_black", "normal", 0.4, 0.85),
    "coffee": (["steam green screen", "warm bokeh overlay black background", "soft dust particles black background"], ["warm", "bokeh", "dust"], ["people", "text", "camera movement"], "screen_black", "normal", 0.35, 0.75),
    "sleep": (["slow particles black background overlay", "moonlight clouds overlay", "soft blue bokeh black background"], ["slow", "soft", "night"], ["flash", "glitch", "fast"], "screen_black", "normal", 0.3, 0.65),
    "dark": (["smoke black background overlay", "dark dust particles overlay", "blue light leaks black background"], ["dark", "smoke", "blue"], ["daylight", "people", "text"], "screen_black", "normal", 0.4, 0.8),
}

_DEFAULT_CHROMA = {
    "key_color": "#00FF00",
    "chroma_similarity": 0.18,
    "chroma_softness": 0.08,
    "despill": 0.35,
    "edge_feather": 1.5,
}


def _words(value: Any) -> set[str]:
    if isinstance(value, (list, tuple, set)):
        value = " ".join(str(item) for item in value)
    return set(re.findall(r"[a-z0-9]+", str(value or "").lower()))


def _clamp(value: Any, low: float, high: float, fallback: float) -> float:
    try:
        return max(low, min(float(value), high))
    except (TypeError, ValueError):
        return fallback


def _normalize_profile(raw: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    queries = [str(item).strip()[:100] for item in raw.get("queries", []) if str(item).strip()][:3]
    preferred = [str(item).strip().lower() for item in raw.get("preferred_tags", []) if str(item).strip()][:10]
    avoid = [str(item).strip().lower() for item in raw.get("avoid_tags", []) if str(item).strip()][:10]
    blend = str(raw.get("blend_mode") or fallback["blend_mode"]).lower()
    effect_type = str(raw.get("effect_type") or fallback["effect_type"]).strip().lower()
    key_color = str(raw.get("key_color") or _DEFAULT_CHROMA["key_color"]).strip()
    if not re.fullmatch(r"#?[0-9a-fA-F]{6}", key_color):
        key_color = _DEFAULT_CHROMA["key_color"]
    elif not key_color.startswith("#"):
        key_color = "#" + key_color
    return {
        "queries": queries or fallback["queries"],
        "preferred_tags": preferred or fallback["preferred_tags"],
        "avoid_tags": avoid or fallback["avoid_tags"],
        "effect_type": effect_type if effect_type in _ALLOWED_EFFECT_TYPES else fallback["effect_type"],
        "blend_mode": blend if blend in _ALLOWED_BLEND_MODES else fallback["blend_mode"],
        "opacity": _clamp(raw.get("opacity"), 0.1, 0.85, fallback["opacity"]),
        "speed": _clamp(raw.get("speed"), 0.5, 1.5, fallback["speed"]),
        "key_color": key_color.upper(),
        "chroma_similarity": _clamp(raw.get("chroma_similarity"), 0.05, 0.5, _DEFAULT_CHROMA["chroma_similarity"]),
        "chroma_softness": _clamp(raw.get("chroma_softness"), 0.0, 0.4, _DEFAULT_CHROMA["chroma_softness"]),
        "despill": _clamp(raw.get("despill"), 0.0, 1.0, _DEFAULT_CHROMA["despill"]),
        "edge_feather": _clamp(raw.get("edge_feather"), 0.0, 5.0, _DEFAULT_CHROMA["edge_feather"]),
        "reason": str(raw.get("reason") or fallback["reason"])[:300],
        "source": str(raw.get("source") or "ai"),
    }


def fallback_effect_profile(track: dict | None, music_tags: list[str] | None, image_context: str = "") -> dict[str, Any]:
    text = " ".join([
        str((track or {}).get("title") or ""), str((track or {}).get("author") or ""),
        str((track or {}).get("description") or ""), " ".join(music_tags or []), image_context,
    ]).lower()
    selected = next((rule for key, rule in _FALLBACK_RULES.items() if key in text), None)
    if not selected:
        selected = (
            ["soft dust particles black background", "bokeh overlay black background", "film grain overlay"],
            ["soft", "slow", "lofi"], ["people", "text", "logo"], "screen_black", "normal", 0.35, 0.8,
        )
    queries, preferred, avoid, effect_type, blend, opacity, speed = selected
    return {
        "queries": queries, "preferred_tags": preferred, "avoid_tags": avoid,
        "effect_type": effect_type, "blend_mode": blend, "opacity": opacity, "speed": speed,
        **_DEFAULT_CHROMA,
        "reason": "Hồ sơ dự phòng tạo từ mood, tiêu đề nhạc và mô tả ảnh.", "source": "fallback",
    }


def build_effect_profile(
    track: dict | None,
    music_tags: list[str] | None,
    image_context: str,
    *,
    api_url: str,
    api_key: str = "",
    model: str = "openai",
    timeout: int = 40,
    enabled: bool = True,
) -> dict[str, Any]:
    """Ask an OpenAI-compatible endpoint for strict JSON, then validate every field."""
    fallback = fallback_effect_profile(track, music_tags, image_context)
    if not str(api_key or "").strip():
        api_url = "https://text.pollinations.ai/openai"
        model = "openai"
    if not enabled or not api_url.strip():
        return fallback
    system = (
        "You recommend subtle stock-video overlays for a lofi visual. Return JSON only with keys: "
        "effect_type (one of screen_black/chroma_key/alpha/normal - screen_black means dark-background "
        "overlay footage, chroma_key means green-screen footage), "
        "queries (max 3 short English Pixabay search phrases; each MUST state the asset type explicitly, "
        "e.g. 'rain green screen' or 'smoke black background overlay', never a bare word like 'rain'), "
        "preferred_tags, avoid_tags, blend_mode (screen/lighten/overlay/soft-light/normal), "
        "opacity (0.1-0.85), speed (0.5-1.5), key_color (hex, only for chroma_key), "
        "chroma_similarity (0.05-0.5), chroma_softness (0-0.4), despill (0-1), reason. "
        "Avoid people, text, logos and strong camera motion unless clearly required."
    )
    user = json.dumps({
        "track_title": (track or {}).get("title"), "track_author": (track or {}).get("author"),
        "music_tags": music_tags or [], "image_context": image_context[:1500],
    }, ensure_ascii=False)
    headers = {"Content-Type": "application/json"}
    if api_key.strip():
        headers["Authorization"] = f"Bearer {api_key.strip()}"

    req_json = {
        "model": model, "temperature": 0.25, "max_tokens": 600,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }
    # Pollinations doesn't support response_format json_object
    is_pollinations = "pollinations.ai" in str(api_url).lower()
    if not is_pollinations:
        req_json["response_format"] = {"type": "json_object"}

    try:
        response = requests.post(api_url, headers=headers, json=req_json, timeout=timeout)
        # If server rejects json_object response format, retry without it
        if response.status_code == 400 and "response_format" in req_json:
            req_json.pop("response_format")
            response = requests.post(api_url, headers=headers, json=req_json, timeout=timeout)

        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        if isinstance(content, dict):
            raw = content
        else:
            cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", str(content).strip(), flags=re.I)
            raw = json.loads(cleaned)
        raw["source"] = "ai"
        return _normalize_profile(raw, fallback)
    except Exception as exc:
        result = dict(fallback)
        result["error"] = str(exc)[:300]
        return result


def _score_item(words: set[str], profile: dict[str, Any], duration: float, height: int, size: int) -> int:
    preferred = _words(profile.get("preferred_tags"))
    avoided = _words(profile.get("avoid_tags"))
    query_words = _words(profile.get("queries"))
    score = 12 * len(words & preferred) + 3 * len(words & query_words) - 15 * len(words & avoided)
    if 5 <= duration <= 20:
        score += 8
    elif duration > 60:
        score -= 5
    if 540 <= height <= 1080:
        score += 5
    if size and size <= 30 * 1024 * 1024:
        score += 4
    return score


def rank_candidates(candidates: list[dict], profile: dict[str, Any]) -> list[dict]:
    """Rank API metadata deterministically so AI cannot select or download blindly."""
    preferred = _words(profile.get("preferred_tags"))
    ranked = []
    for candidate in candidates:
        words = _words(candidate.get("tags")) | _words(candidate.get("query"))
        score = _score_item(
            words, profile,
            float(candidate.get("duration") or 0),
            int(candidate.get("height") or 0),
            int(candidate.get("file_size") or 0),
        )
        item = dict(candidate)
        item["ai_score"] = max(0, min(100, 50 + score))
        matches = sorted(words & preferred)
        item["ai_reason"] = "Phù hợp tag: " + ", ".join(matches[:4]) if matches else "Phù hợp thông số video và query tìm kiếm."
        ranked.append(item)
    return sorted(ranked, key=lambda item: (-item["ai_score"], int(item.get("file_size") or 0)))


def rank_local_effects(records: list[dict], profile: dict[str, Any]) -> list[dict]:
    """
    Xếp hạng thư viện local (manifest) theo hồ sơ AI — chạy TRƯỚC khi gọi Pixabay
    để tiết kiệm quota, tránh tải trùng và hoạt động khi mất mạng.
    Thưởng điểm khi effect_type của asset khớp với effect_type hồ sơ đề xuất.
    """
    preferred = _words(profile.get("preferred_tags"))
    wanted_type = str(profile.get("effect_type") or "").strip().lower()
    ranked = []
    for record in records:
        if record.get("status") not in (None, "ready"):
            continue
        words = _words(record.get("tags")) | _words(record.get("query")) | _words(record.get("file_name"))
        score = _score_item(
            words, profile,
            float(record.get("duration_seconds") or 0),
            int(record.get("height") or 0),
            int(record.get("file_size") or 0),
        )
        record_type = str(record.get("effect_type") or "").strip().lower()
        if wanted_type and record_type == wanted_type:
            score += 10
        item = dict(record)
        item["provider"] = item.get("provider") or "local"
        item["ai_score"] = max(0, min(100, 50 + score))
        matches = sorted(words & preferred)
        item["ai_reason"] = (
            "Đã có sẵn trong thư viện · khớp tag: " + ", ".join(matches[:4])
            if matches else "Đã có sẵn trong thư viện local."
        )
        item["origin"] = "local"
        ranked.append(item)
    return sorted(ranked, key=lambda item: -item["ai_score"])
