"""
AI FILE NOTE - TEXT EFFECT PROVIDER (điều phối chữ động tùy chọn)

Chức năng chính:
- Chuẩn hóa/kẹp profile chữ (nội dung, vị trí lưới 9 ô, màu, cỡ, preset vào/giữ/ra, mốc thời gian).
- Font registry: map font_style (serif/sans/display) -> font family theo NGÔN NGỮ nội dung
  (tiếng Việt/CJK), ưu tiên font bundle trong data/fonts/, fallback font hệ thống có cảnh báo.
- Tạo cache key ổn định cho profile chữ (để preview cũ tự vô hiệu khi đổi thông số).
- Gọi AI recommender (có fallback local) và chuẩn bị dữ liệu cho preview + render.

API được file khác sử dụng:
- normalize_text_profile(), default_text_profile()
- text_profile_cache_key()
- resolve_font()
- build_ai_text_profile()
- CONTENT_TYPES, STYLE_PRESETS, list_fonts()

Lưu ý khi sửa:
- AI KHÔNG bao giờ trả đường dẫn/tên file font; chỉ trả font_style, provider map sang family.
- Không lưu API key vào profile hay manifest.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import config
from core.text.effect_renderer import (
    POSITIONS, INTRO_EFFECTS, HOLD_EFFECTS, OUTRO_EFFECTS,
)

CONTENT_TYPES = {
    "track_title": "Tiêu đề bài nhạc",
    "artist": "Tên nghệ sĩ",
    "topic": "Chủ đề video",
    "short_desc": "Câu mô tả ngắn",
    "intro_message": "Thông điệp mở đầu",
    "series": "Tên series / kênh",
    "custom": "Tự nhập",
}

STYLE_PRESETS = ("minimal", "cinematic", "ancient_mist", "anime", "rainy_night", "neon", "retro", "dreamy")

FONT_STYLES = ("sans", "serif", "display")

_FONTS_DIR = Path(config.BASE_DIR) / "data" / "fonts"

# Ứng viên font theo (style, script). script: "latin" (gồm tiếng Việt) hoặc "cjk".
# Mỗi ứng viên: (family_name, tên_file_hệ_thống_để_kiểm_tra_tồn_tại).
# Family đầu tiên có file khớp (trong data/fonts hoặc C:/Windows/Fonts) sẽ được chọn.
_SYSTEM_FONTS_DIR = Path("C:/Windows/Fonts")
# Ứng viên bundle (OFL, trong data/fonts) đặt TRƯỚC font hệ thống để chữ đẹp/nhất quán.
# Be Vietnam Pro: sans hiện đại phủ đủ dấu tiếng Việt. Noto Serif SC: serif cổ phong phủ Hán.
_FONT_CANDIDATES: dict[tuple[str, str], list[tuple[str, str]]] = {
    ("sans", "latin"): [("Be Vietnam Pro", "BeVietnamPro-Regular.ttf"),
                        ("Segoe UI", "segoeui.ttf"), ("Arial", "arial.ttf"), ("Tahoma", "tahoma.ttf")],
    ("serif", "latin"): [("Times New Roman", "times.ttf"), ("Georgia", "georgia.ttf"), ("Arial", "arial.ttf")],
    ("display", "latin"): [("Be Vietnam Pro SemiBold", "BeVietnamPro-SemiBold.ttf"),
                          ("Arial Black", "ariblk.ttf"), ("Segoe UI", "segoeui.ttf"), ("Arial", "arial.ttf")],
    # CJK: Noto Serif SC (bundle) cho cổ phong; YaHei/SimSun/MingLiU là fallback hệ thống.
    ("sans", "cjk"): [("Microsoft YaHei", "msyh.ttc"), ("SimSun", "simsun.ttc"), ("MingLiU", "mingliub.ttc")],
    ("serif", "cjk"): [("Noto Serif SC", "NotoSerifSC.ttf"),
                      ("SimSun", "simsun.ttc"), ("Microsoft YaHei", "msyh.ttc"), ("MingLiU", "mingliub.ttc")],
    ("display", "cjk"): [("Noto Serif SC", "NotoSerifSC.ttf"), ("Microsoft YaHei", "msyh.ttc"), ("SimSun", "simsun.ttc")],
}
_FALLBACK_FAMILY = "Arial"


def _detect_script(text: str) -> str:
    """Phát hiện chữ Hán/Nhật (CJK) trong nội dung; còn lại coi là latin (gồm tiếng Việt)."""
    for ch in str(text or ""):
        code = ord(ch)
        if (0x4E00 <= code <= 0x9FFF) or (0x3040 <= code <= 0x30FF) or (0x3400 <= code <= 0x4DBF):
            return "cjk"
    return "latin"


def list_fonts() -> dict[str, Any]:
    """Thông tin font đang khả dụng để UI hiển thị (không bắt buộc)."""
    bundled = sorted(p.name for p in _FONTS_DIR.glob("*.tt*")) if _FONTS_DIR.is_dir() else []
    return {"bundled_dir": str(_FONTS_DIR), "bundled": bundled}


def resolve_font(font_style: str, content: str) -> dict[str, Any]:
    """
    Trả về {family, fontsdir, bundled_path, warning} cho nội dung + style.
    Ưu tiên font bundle trong data/fonts/; nếu không có, dùng font hệ thống theo family name
    (libass phân giải qua DirectWrite trên Windows).
    """
    style = str(font_style or "sans").lower()
    if style not in FONT_STYLES:
        style = "sans"
    script = _detect_script(content)
    candidates = _FONT_CANDIDATES.get((style, script)) or _FONT_CANDIDATES[("sans", script)]

    # 1) Ưu tiên font bundle: nếu data/fonts có đúng file ứng viên.
    if _FONTS_DIR.is_dir():
        for family, file_name in candidates:
            bundled = _FONTS_DIR / file_name
            if bundled.is_file():
                return {"family": family, "fontsdir": str(_FONTS_DIR), "bundled_path": str(bundled), "warning": ""}

    # 2) Font hệ thống theo family name (không cần fontsdir).
    for family, file_name in candidates:
        if (_SYSTEM_FONTS_DIR / file_name).is_file():
            return {"family": family, "fontsdir": "", "bundled_path": "", "warning": ""}

    # 3) Fallback: dùng family đầu tiên và cảnh báo có thể thiếu glyph.
    fallback_family = candidates[0][0] if candidates else _FALLBACK_FAMILY
    warning = (
        f"Không tìm thấy font phù hợp cho nội dung ({'chữ Hán' if script == 'cjk' else 'tiếng Việt/Latin'}). "
        f"Đang dùng '{fallback_family}', có thể thiếu ký tự. "
        f"Hãy đặt font phủ đủ vào {_FONTS_DIR}."
    )
    return {"family": fallback_family, "fontsdir": "", "bundled_path": "", "warning": warning}


def default_text_profile() -> dict[str, Any]:
    return {
        "enabled": True,
        "content": "",
        "secondary_text": "",
        "content_type": "custom",
        "design_goal": "decorative_video_typography",
        "writing_direction": "vertical",
        "visual_anchor": "beside_character",
        "avoid_subject": True,
        "preset": "minimal",
        "font_style": "sans",
        "position": "bottom_center",
        "text_color": "#FFFFFF",
        "outline_color": "#101820",
        "outline_width": 2.0,
        "font_size": 72,
        "bold": True,
        "letter_spacing": 0.0,
        # Con dấu đỏ (kiểu bìa nhạc Hoa ngữ) — chỉ vẽ khi bật + có nội dung.
        "seal_enabled": False,
        "seal_text": "",
        "seal_color": "#B23A2E",
        "intro_effect": "fade",
        "hold_effect": "soft_glow",
        "outro_effect": "fade",
        "intro_duration": 0.8,
        "outro_duration": 1.0,
        "start_seconds": 0.0,
        "end_seconds": None,
        "reason": "",
        "source": "default",
    }


def _clamp(value: Any, low: float, high: float, fallback: float) -> float:
    try:
        return max(low, min(float(value), high))
    except (TypeError, ValueError):
        return fallback


def _hex(value: Any, fallback: str) -> str:
    text = str(value or "").strip().lstrip("#")
    if re.fullmatch(r"[0-9a-fA-F]{6}", text):
        return "#" + text.upper()
    return fallback


def normalize_text_profile(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Chuẩn hóa và kẹp mọi trường; giữ nội dung người dùng, không cho AI vượt giới hạn."""
    base = default_text_profile()
    data = dict(raw or {})
    content_type = str(data.get("content_type") or base["content_type"])
    if content_type not in CONTENT_TYPES:
        content_type = base["content_type"]
    preset = str(data.get("preset") or base["preset"]).lower()
    if preset not in STYLE_PRESETS:
        preset = base["preset"]
    font_style = str(data.get("font_style") or base["font_style"]).lower()
    if font_style not in FONT_STYLES:
        font_style = base["font_style"]
    position = str(data.get("position") or base["position"]).lower()
    if position not in POSITIONS:
        position = base["position"]
    writing_direction = str(data.get("writing_direction") or base["writing_direction"]).lower()
    if writing_direction not in ("vertical", "horizontal"):
        writing_direction = base["writing_direction"]
    visual_anchor = str(data.get("visual_anchor") or base["visual_anchor"]).lower()
    if visual_anchor not in ("beside_character", "negative_space", "free"):
        visual_anchor = base["visual_anchor"]
    intro = str(data.get("intro_effect") or base["intro_effect"]).lower()
    if intro not in INTRO_EFFECTS:
        intro = base["intro_effect"]
    hold = str(data.get("hold_effect") or base["hold_effect"]).lower()
    if hold not in HOLD_EFFECTS:
        hold = base["hold_effect"]
    outro = str(data.get("outro_effect") or base["outro_effect"]).lower()
    if outro not in OUTRO_EFFECTS:
        outro = base["outro_effect"]

    end_seconds = data.get("end_seconds")
    try:
        end_seconds = float(end_seconds) if end_seconds not in (None, "") else None
    except (TypeError, ValueError):
        end_seconds = None

    return {
        "enabled": bool(data.get("enabled", base["enabled"])),
        "content": str(data.get("content") or "")[:80],
        "secondary_text": str(data.get("secondary_text") or "")[:120],
        "content_type": content_type,
        "design_goal": "decorative_video_typography",
        "writing_direction": writing_direction,
        "visual_anchor": visual_anchor,
        "avoid_subject": bool(data.get("avoid_subject", base["avoid_subject"])),
        "preset": preset,
        "font_style": font_style,
        "position": position,
        "text_color": _hex(data.get("text_color"), base["text_color"]),
        "outline_color": _hex(data.get("outline_color"), base["outline_color"]),
        "outline_width": _clamp(data.get("outline_width"), 0.0, 8.0, base["outline_width"]),
        "font_size": int(_clamp(data.get("font_size"), 16, 200, base["font_size"])),
        "bold": bool(data.get("bold", base["bold"])),
        "letter_spacing": _clamp(data.get("letter_spacing"), 0.0, 40.0, base["letter_spacing"]),
        "seal_enabled": bool(data.get("seal_enabled", base["seal_enabled"])),
        "seal_text": str(data.get("seal_text") or "")[:12],
        "seal_color": _hex(data.get("seal_color"), base["seal_color"]),
        "intro_effect": intro,
        "hold_effect": hold,
        "outro_effect": outro,
        "intro_duration": _clamp(data.get("intro_duration"), 0.0, 5.0, base["intro_duration"]),
        "outro_duration": _clamp(data.get("outro_duration"), 0.0, 5.0, base["outro_duration"]),
        "start_seconds": _clamp(data.get("start_seconds"), 0.0, 100000.0, base["start_seconds"]),
        "end_seconds": end_seconds,
        "reason": str(data.get("reason") or "")[:300],
        "source": str(data.get("source") or base["source"]),
    }


def text_profile_cache_key(profile: dict[str, Any] | None) -> str:
    """Chuỗi ổn định đại diện profile chữ; đưa vào preview_key để vô hiệu preview cũ."""
    if not profile or not profile.get("enabled") or not str(profile.get("content") or "").strip():
        return "text=off"
    p = normalize_text_profile(profile)
    return (
        f"text=on|c={p['content']}|sub={p['secondary_text']}|dir={p['writing_direction']}|"
        f"anchor={p['visual_anchor']}|avoid={int(p['avoid_subject'])}|pos={p['position']}|fs={p['font_size']}|"
        f"fst={p['font_style']}|col={p['text_color']}|out={p['outline_color']}:{p['outline_width']:.1f}|"
        f"spc={p['letter_spacing']:.1f}|seal={int(p['seal_enabled'])}:{p['seal_text']}:{p['seal_color']}|"
        f"in={p['intro_effect']}:{p['intro_duration']:.2f}|hold={p['hold_effect']}|"
        f"out2={p['outro_effect']}:{p['outro_duration']:.2f}|s={p['start_seconds']:.2f}|e={p['end_seconds']}"
    )


def build_ai_text_profile(track: dict, music_tags: list[str], image_context: str, content: str,
                           video_duration: float = 3600.0, current: dict | None = None) -> dict:
    """Tạo profile chữ bằng AI; lỗi/tắt thì fallback local. Giữ nội dung người dùng đã nhập."""
    from core.text.effect_recommender import build_text_profile
    from core.text.typo_presets import resolve_typo_preset, get_preset_layout
    current_content = str((current or {}).get("content") or "").strip()
    api_content = content or current_content
    profile = build_text_profile(
        track, music_tags, image_context, api_content, video_duration,
        api_url=getattr(config, "PROMPT_API_URL", ""),
        api_key=getattr(config, "PROMPT_API_KEY", ""),
        model=getattr(config, "PROMPT_API_MODEL", "openai"),
        timeout=int(getattr(config, "PROMPT_API_TIMEOUT", 40)),
        enabled=bool(getattr(config, "TEXT_EFFECT_AI_ENABLED", True)),
    )
    # Cho phép AI đề xuất cụm chữ khi ô chính đang trống. Nếu người dùng đã nhập, giữ nguyên.
    merged = dict(current or {})
    merged.update(profile or {})

    # Áp PRESET TYPO theo thể loại (nhạc Trung -> dọc + con dấu; Việt/Lofi -> layout khác).
    # Preset quyết định bố cục/màu/hướng để chữ hợp thể loại; nội dung người dùng giữ nguyên ở dưới.
    style_tags = (track or {}).get("style_tags") or music_tags or []
    hint_text = " ".join([
        str((track or {}).get("title") or ""), str((track or {}).get("author") or ""),
        " ".join(str(t) for t in (music_tags or [])),
    ])
    preset_key = resolve_typo_preset(
        style_tags=style_tags,
        content=str(merged.get("content") or api_content),
        hint_text=hint_text,
    )
    if preset_key:
        merged.update(get_preset_layout(preset_key))
        merged["reason"] = f"[typo:{preset_key}] " + str(merged.get("reason") or "")
        # Con dấu bật mà chưa có chữ -> mặc định lấy tên nghệ sĩ cho ra dấu có nghĩa.
        if merged.get("seal_enabled") and not str(merged.get("seal_text") or "").strip():
            author = str((track or {}).get("author") or "").strip()
            if author:
                merged["seal_text"] = author[:12]

    normalized = normalize_text_profile(merged)
    if current_content:
        normalized["content"] = current_content[:80]
        if "secondary_text" in (current or {}):
            normalized["secondary_text"] = str(current["secondary_text"])[:120]
    elif content:
        normalized["content"] = content[:80]
    normalized["design_goal"] = "decorative_video_typography"
    return normalized
