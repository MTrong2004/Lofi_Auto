"""
AI FILE NOTE - TYPO PRESETS (bố cục chữ nghệ thuật theo THỂ LOẠI nhạc)

Chức năng chính:
- Định nghĩa registry preset layout tiêu đề theo thể loại: nhạc Trung/Cổ phong (dọc + con dấu đỏ
  như bìa nhạc Hoa ngữ), nhạc Việt/V-Pop (ngang, hiện đại), Lofi (tối giản, mềm).
- Ánh xạ nhãn thể loại (style_tags tiếng Việt từ step1, hoặc chữ trong nội dung/tag) -> preset.
- Trả về bộ trường layout để merge vào text profile (chỉ điền chỗ AI/người dùng chưa quyết).

API được file khác sử dụng:
- resolve_typo_preset(style_tags, content, hint_text) -> tên preset | None
- get_preset_layout(name) -> dict trường layout
- TYPO_PRESETS, GENRE_TO_PRESET

Lưu ý khi sửa:
- KHÔNG đặt lại 'content' của người dùng; preset chỉ quyết định bố cục/màu/hướng/con dấu.
- Trường con dấu (seal_*) chỉ có tác dụng khi core.text.effect_renderer bật vẽ seal.
- Tên trường phải khớp với normalize_text_profile() trong core/text/provider.py.
"""
from __future__ import annotations

from typing import Any

# Mỗi preset là tập trường layout sẽ merge vào text profile.
# writing_direction: vertical|horizontal · position: lưới 9 ô · font_style: sans|serif|display
# letter_spacing: ASS Spacing (px) · seal_*: con dấu đỏ (chỉ nhạc Trung mặc định bật).
TYPO_PRESETS: dict[str, dict[str, Any]] = {
    # Nhạc Trung / Cổ phong — bố cục như ảnh mẫu: tiêu đề xếp DỌC bên phải + con dấu đỏ.
    "chinese_ancient": {
        "preset": "ancient_mist",
        "writing_direction": "vertical",
        "visual_anchor": "negative_space",
        "position": "center_right",
        "font_style": "serif",
        "text_color": "#F5F0E6",
        "outline_color": "#16202E",
        "outline_width": 1.5,
        "bold": True,
        "letter_spacing": 6.0,
        "seal_enabled": True,
        "seal_color": "#B23A2E",
        "intro_effect": "blur_in",
        "hold_effect": "soft_glow",
        "outro_effect": "dissolve",
    },
    # Nhạc Việt / V-Pop — hiện đại, chữ NGANG, đặt góc dưới, font display khỏe.
    "vpop": {
        "preset": "cinematic",
        "writing_direction": "horizontal",
        "visual_anchor": "negative_space",
        "position": "bottom_left",
        "font_style": "display",
        "text_color": "#FFFFFF",
        "outline_color": "#1C1206",
        "outline_width": 2.5,
        "bold": True,
        "letter_spacing": 0.0,
        "seal_enabled": False,
        "seal_color": "#B23A2E",
        "intro_effect": "slide_up",
        "hold_effect": "soft_glow",
        "outro_effect": "fade",
    },
    # Lofi / chill — tối giản, mềm, chữ ngang giữa dưới, không con dấu.
    "lofi": {
        "preset": "minimal",
        "writing_direction": "horizontal",
        "visual_anchor": "negative_space",
        "position": "bottom_center",
        "font_style": "sans",
        "text_color": "#F2ECE1",
        "outline_color": "#241C15",
        "outline_width": 1.8,
        "bold": False,
        "letter_spacing": 1.0,
        "seal_enabled": False,
        "seal_color": "#B23A2E",
        "intro_effect": "fade",
        "hold_effect": "soft_glow",
        "outro_effect": "fade",
    },
}

# Ánh xạ thể loại -> preset. Khớp không phân biệt hoa/thường, có dấu, và cả nhãn tiếng Việt
# do step1_music_hunter.classify_music_styles() sinh ra ("Cổ phong", "C-Pop", "V-Pop", "Lofi"...).
# Thứ tự QUAN TRỌNG: kiểm tra cổ phong/Hoa ngữ trước, rồi Việt, rồi lofi.
GENRE_TO_PRESET: list[tuple[tuple[str, ...], str]] = [
    (("cổ phong", "co phong", "c-pop", "cpop", "hoa ngữ", "hoa ngu", "trung", "chinese",
      "guzheng", "cổ trang", "co trang", "古", "pinyin"), "chinese_ancient"),
    (("v-pop", "vpop", "việt", "viet", "vietnam", "nhạc việt", "nhac viet"), "vpop"),
    (("lofi", "lo-fi", "lo fi", "chill", "chillhop"), "lofi"),
]


def _norm(text: str) -> str:
    return str(text or "").strip().lower()


def get_preset_layout(name: str | None) -> dict[str, Any]:
    """Trả về BẢN SAO trường layout của preset (rỗng nếu tên không hợp lệ)."""
    return dict(TYPO_PRESETS.get(str(name or ""), {}))


def resolve_typo_preset(
    style_tags: list[str] | None = None,
    content: str = "",
    hint_text: str = "",
) -> str | None:
    """Chọn preset typo theo tín hiệu thể loại.

    Ưu tiên: (1) nhãn style_tags -> (2) chữ Hán trong nội dung -> (3) hint_text (title/author/tag).
    Trả về None nếu không đủ tín hiệu (nơi gọi tự quyết mặc định).
    """
    tags_joined = " ".join(_norm(t) for t in (style_tags or []))
    hint = _norm(hint_text)
    haystack = f"{tags_joined} {hint}"

    for keywords, preset in GENRE_TO_PRESET:
        if any(kw in haystack for kw in keywords):
            return preset

    # Nội dung có chữ Hán -> mặc định cổ phong (kể cả khi thiếu nhãn thể loại).
    if any("㐀" <= ch <= "鿿" for ch in str(content or "")):
        return "chinese_ancient"
    return None
