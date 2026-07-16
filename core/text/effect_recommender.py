"""
AI FILE NOTE - TEXT EFFECT RECOMMENDER (AI gợi ý profile chữ + fallback local)

Chức năng chính:
- Gọi endpoint OpenAI-compatible xin JSON profile chữ (vị trí, font_style, màu, cỡ, animation).
- Validate mọi trường: vị trí chỉ trong lưới 9 ô, không nhận tọa độ tùy ý, không nhận path font.
- Fallback local theo mood khi AI lỗi hoặc bị tắt, để luồng không bao giờ kẹt.

API được file khác sử dụng:
- build_text_profile(), fallback_text_profile()

Lưu ý khi sửa:
- AI chỉ ĐỀ XUẤT; không tự render, không tự đổi file, không tự quyết định phát hành.
- Giữ nội dung chữ do người dùng nhập; AI không được đặt lại content.
"""
from __future__ import annotations

import json
import re
from typing import Any

import requests

_POSITIONS = {
    "top_left", "top_center", "top_right",
    "center_left", "center", "center_right",
    "bottom_left", "bottom_center", "bottom_right",
}
_FONT_STYLES = {"sans", "serif", "display"}
_INTRO = {"fade", "blur_in", "scale_slow", "slide_up", "slide_left"}
_HOLD = {"none", "soft_glow", "glow_breathe"}
_OUTRO = {"fade", "dissolve"}

# Fallback theo mood: (preset, font_style, position, text_color, outline_color, intro, hold, outro)
_FALLBACK_RULES = {
    "rain": ("rainy_night", "serif", "center_right", "#EAF2F8", "#0B1B2B", "blur_in", "soft_glow", "dissolve"),
    "night": ("cinematic", "serif", "center_right", "#F3F0E8", "#101820", "blur_in", "soft_glow", "dissolve"),
    "sleep": ("dreamy", "serif", "center", "#F5F0FF", "#1A1533", "fade", "soft_glow", "dissolve"),
    "coffee": ("retro", "sans", "bottom_left", "#F6ECD9", "#2A1E10", "slide_up", "none", "fade"),
    "anime": ("anime", "sans", "top_center", "#FFFFFF", "#20304A", "scale_slow", "soft_glow", "fade"),
    "gaming": ("neon", "display", "top_center", "#E8FBFF", "#0A2A3A", "slide_left", "soft_glow", "fade"),
    "chinese": ("ancient_mist", "serif", "center_right", "#F3F0E8", "#152536", "blur_in", "soft_glow", "dissolve"),
    "guzheng": ("ancient_mist", "serif", "center_right", "#F3F0E8", "#152536", "blur_in", "soft_glow", "dissolve"),
}


def fallback_text_profile(track: dict | None, music_tags: list[str] | None,
                          image_context: str, content: str, video_duration: float) -> dict[str, Any]:
    text = " ".join([
        str((track or {}).get("title") or ""), str((track or {}).get("author") or ""),
        " ".join(music_tags or []), image_context,
    ]).lower()
    rule = next((value for key, value in _FALLBACK_RULES.items() if key in text), None)
    if not rule:
        rule = ("minimal", "sans", "bottom_center", "#FFFFFF", "#101820", "fade", "soft_glow", "fade")
    preset, font_style, position, text_color, outline_color, intro, hold, outro = rule
    return {
        "enabled": True,
        "content": content,
        "preset": preset,
        "font_style": font_style,
        "position": position,
        "text_color": text_color,
        "outline_color": outline_color,
        "outline_width": 2.0,
        "font_size": 76,
        "bold": True,
        "intro_effect": intro,
        "hold_effect": hold,
        "outro_effect": outro,
        "intro_duration": 0.8,
        "outro_duration": 1.2,
        "reason": "Hồ sơ chữ dự phòng theo mood nhạc và ảnh nền.",
        "source": "fallback",
    }


def _validate(raw: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    def pick(key, allowed):
        value = str(raw.get(key) or "").strip().lower()
        return value if value in allowed else fallback[key]
    result = dict(fallback)
    result["preset"] = str(raw.get("preset") or fallback["preset"]).lower()
    result["font_style"] = pick("font_style", _FONT_STYLES)
    result["position"] = pick("position", _POSITIONS)
    result["intro_effect"] = pick("intro_effect", _INTRO)
    result["hold_effect"] = pick("hold_effect", _HOLD)
    result["outro_effect"] = pick("outro_effect", _OUTRO)
    for color_key in ("text_color", "outline_color"):
        val = str(raw.get(color_key) or "").strip()
        if not val.startswith("#"):
            val = "#" + val
        result[color_key] = val if re.fullmatch(r"#[0-9a-fA-F]{6}", val) else fallback[color_key]
    for num_key, lo, hi in (("font_size", 16, 200), ("intro_duration", 0.0, 5.0), ("outro_duration", 0.0, 5.0)):
        try:
            result[num_key] = max(lo, min(float(raw.get(num_key, fallback[num_key])), hi))
        except (TypeError, ValueError):
            result[num_key] = fallback[num_key]
    result["font_size"] = int(result["font_size"])
    result["reason"] = str(raw.get("reason") or fallback["reason"])[:300]
    result["source"] = "ai"
    return result


def build_text_profile(
    track: dict | None,
    music_tags: list[str] | None,
    image_context: str,
    content: str,
    video_duration: float,
    *,
    api_url: str,
    api_key: str = "",
    model: str = "openai",
    timeout: int = 40,
    enabled: bool = True,
) -> dict[str, Any]:
    fallback = fallback_text_profile(track, music_tags, image_context, content, video_duration)
    if not enabled or not str(api_url or "").strip():
        return fallback
    system = (
        "You design a single tasteful on-screen text overlay for a lofi music video (NOT karaoke). "
        "Return JSON only with keys: preset (minimal/cinematic/ancient_mist/anime/rainy_night/neon/retro/dreamy), "
        "font_style (sans/serif/display), position (one of: top_left,top_center,top_right,center_left,"
        "center,center_right,bottom_left,bottom_center,bottom_right ONLY - never coordinates), "
        "text_color (hex), outline_color (hex), font_size (16-200), intro_effect "
        "(fade/blur_in/scale_slow/slide_up/slide_left), hold_effect (none/soft_glow/glow_breathe), "
        "outro_effect (fade/dissolve), intro_duration (0-5 sec), outro_duration (0-5 sec), reason. "
        "Avoid faces and the main subject; keep text inside safe margins; pick colors readable over the image."
    )
    user = json.dumps({
        "track_title": (track or {}).get("title"), "track_author": (track or {}).get("author"),
        "music_tags": music_tags or [], "image_context": image_context[:1500],
        "text_content": content[:200], "video_duration_seconds": video_duration,
    }, ensure_ascii=False)
    headers = {"Content-Type": "application/json"}
    if str(api_key or "").strip():
        headers["Authorization"] = f"Bearer {str(api_key).strip()}"
    try:
        response = requests.post(str(api_url).strip(), headers=headers, json={
            "model": model, "temperature": 0.3, "max_tokens": 500,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_object"},
        }, timeout=timeout)
        response.raise_for_status()
        payload = response.json()["choices"][0]["message"]["content"]
        if isinstance(payload, dict):
            raw = payload
        else:
            cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", str(payload).strip(), flags=re.I)
            raw = json.loads(cleaned)
        return _validate(raw, fallback)
    except Exception as exc:
        result = dict(fallback)
        result["error"] = str(exc)[:300]
        return result
