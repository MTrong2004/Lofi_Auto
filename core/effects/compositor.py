"""
AI FILE NOTE - EFFECT COMPOSITOR (FFmpeg filter builder dùng chung)

Chức năng chính:
- Chuẩn hóa bộ thông số compositing (effect_type, opacity, speed, blend, chroma key).
- Sinh chuỗi filter_complex FFmpeg THỐNG NHẤT cho cả preview 10 giây lẫn render cuối,
  để hai đường render không bao giờ lệch nhau về tham số.
- Tạo cache key ổn định từ toàn bộ thông số compositing (dùng cho preview_key).

Các loại hiệu ứng (effect_type):
- screen_black: overlay nền đen, xóa vùng gần đen bằng colorkey.
- chroma_key:   video phông xanh, tách nền bằng chromakey + despill + feather.
- alpha:        video đã có kênh alpha, chỉ áp opacity.
- normal:       video thường, ghép đè với opacity (hoặc blend mode nếu chọn).

API được file khác sử dụng:
- normalize_effect_settings()
- build_filter_complex()
- effect_settings_cache_key()
- DEFAULT_EFFECT_SETTINGS, EFFECT_TYPES, BLEND_MODES

Lưu ý khi sửa:
- build_effect_preview() và render_video_segment() trong step4_render.py phải luôn
  gọi qua build_filter_complex(); không viết chuỗi filter overlay riêng.
- Đổi tên/thêm khóa settings phải cập nhật effect_settings_cache_key() để preview cũ
  tự bị vô hiệu.
"""
from __future__ import annotations

import re
from typing import Any

EFFECT_TYPES = ("screen_black", "chroma_key", "alpha", "normal")

# Blend mode "normal" = overlay alpha thông thường; các mode còn lại dùng filter blend.
BLEND_MODES = ("normal", "screen", "lighten", "overlay", "soft-light")
_FFMPEG_BLEND_NAMES = {
    "screen": "screen",
    "lighten": "lighten",
    "overlay": "overlay",
    "soft-light": "softlight",
}

DEFAULT_EFFECT_SETTINGS: dict[str, Any] = {
    "effect_type": "screen_black",
    "blend_mode": "normal",
    "opacity": 0.72,
    "speed": 1.0,
    "key_color": "#00FF00",
    "chroma_similarity": 0.18,
    "chroma_softness": 0.08,
    "despill": 0.35,
    "edge_feather": 1.5,
}


def _clamp(value: Any, low: float, high: float, fallback: float) -> float:
    try:
        return max(low, min(float(value), high))
    except (TypeError, ValueError):
        return fallback


def _normalize_hex_color(value: Any, fallback: str = "#00FF00") -> str:
    text = str(value or "").strip().lstrip("#")
    if re.fullmatch(r"[0-9a-fA-F]{6}", text):
        return "#" + text.upper()
    return fallback


def normalize_effect_settings(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Chuẩn hóa và kẹp giá trị mọi thông số compositing về vùng an toàn."""
    data = dict(raw or {})
    effect_type = str(data.get("effect_type") or "").strip().lower()
    if effect_type not in EFFECT_TYPES:
        effect_type = DEFAULT_EFFECT_SETTINGS["effect_type"]
    blend_mode = str(data.get("blend_mode") or "").strip().lower()
    if blend_mode not in BLEND_MODES:
        blend_mode = "normal"
    # Chroma key và alpha luôn ghép bằng overlay alpha; blend mode chỉ có nghĩa
    # với overlay nền đen (screen cổ điển) hoặc video thường.
    if effect_type in ("chroma_key", "alpha"):
        blend_mode = "normal"
    return {
        "effect_type": effect_type,
        "blend_mode": blend_mode,
        "opacity": _clamp(data.get("opacity"), 0.0, 1.0, DEFAULT_EFFECT_SETTINGS["opacity"]),
        "speed": _clamp(data.get("speed"), 0.25, 2.0, DEFAULT_EFFECT_SETTINGS["speed"]),
        "key_color": _normalize_hex_color(data.get("key_color")),
        "chroma_similarity": _clamp(data.get("chroma_similarity"), 0.01, 0.6, DEFAULT_EFFECT_SETTINGS["chroma_similarity"]),
        "chroma_softness": _clamp(data.get("chroma_softness"), 0.0, 0.5, DEFAULT_EFFECT_SETTINGS["chroma_softness"]),
        "despill": _clamp(data.get("despill"), 0.0, 1.0, DEFAULT_EFFECT_SETTINGS["despill"]),
        "edge_feather": _clamp(data.get("edge_feather"), 0.0, 6.0, DEFAULT_EFFECT_SETTINGS["edge_feather"]),
    }


def effect_settings_cache_key(settings: dict[str, Any] | None) -> str:
    """Chuỗi ổn định đại diện toàn bộ thông số compositing, dùng trong preview_key."""
    s = normalize_effect_settings(settings)
    return (
        f"type={s['effect_type']}|blend={s['blend_mode']}|op={s['opacity']:.3f}|"
        f"spd={s['speed']:.3f}|key={s['key_color']}|sim={s['chroma_similarity']:.3f}|"
        f"soft={s['chroma_softness']:.3f}|despill={s['despill']:.3f}|feather={s['edge_feather']:.2f}"
    )


def _despill_type_for(key_color: str) -> str | None:
    """despill của FFmpeg chỉ hỗ trợ green/blue; màu key khác thì bỏ qua despill."""
    red = int(key_color[1:3], 16)
    green = int(key_color[3:5], 16)
    blue = int(key_color[5:7], 16)
    if green >= red and green >= blue:
        return "green"
    if blue > green and blue >= red:
        return "blue"
    return None


def _effect_chain(settings: dict[str, Any], width: int, height: int, *, for_blend: bool = False) -> str:
    """Chuỗi filter xử lý stream hiệu ứng: scale, tốc độ, tách nền và opacity.

    for_blend=True: opacity do blend=all_opacity đảm nhiệm, không áp colorchannelmixer
    để tránh áp opacity hai lần.
    """
    parts = [
        f"scale={width}:{height}:force_original_aspect_ratio=increase",
        f"crop={width}:{height}",
        "setsar=1",
    ]
    if abs(settings["speed"] - 1.0) > 1e-3:
        parts.append(f"setpts=PTS/{settings['speed']:.4f}")

    effect_type = settings["effect_type"]
    opacity = 1.0 if for_blend else settings["opacity"]
    if effect_type == "chroma_key":
        color = "0x" + settings["key_color"].lstrip("#")
        parts.append(f"chromakey={color}:{settings['chroma_similarity']:.4f}:{settings['chroma_softness']:.4f}")
        parts.append("format=rgba")
        despill_type = _despill_type_for(settings["key_color"])
        if despill_type and settings["despill"] > 0.001:
            parts.append(f"despill=type={despill_type}:mix={settings['despill']:.4f}:expand=0")
        if settings["edge_feather"] > 0.05:
            # Feather không có sẵn trong chromakey: tách alpha, làm mờ rồi ghép lại.
            feather = (
                f"split=2[fx_c][fx_a];[fx_a]alphaextract,gblur=sigma={settings['edge_feather']:.3f}[fx_m];"
                "[fx_c][fx_m]alphamerge"
            )
            parts.append(feather)
        if opacity < 0.9995:
            parts.append(f"colorchannelmixer=aa={opacity:.4f}")
    elif effect_type == "screen_black":
        parts.append("format=rgba")
        if not for_blend:
            # Với blend screen/lighten, vùng đen tự triệt tiêu nên không cần colorkey.
            parts.append(f"colorkey=black:{settings['chroma_similarity']:.4f}:{settings['chroma_softness']:.4f}")
        if opacity < 0.9995:
            parts.append(f"colorchannelmixer=aa={opacity:.4f}")
    elif effect_type == "alpha":
        parts.append("format=rgba")
        if opacity < 0.9995:
            parts.append(f"colorchannelmixer=aa={opacity:.4f}")
    else:  # normal
        parts.append("format=rgba")
        if not for_blend and opacity < 0.9995:
            parts.append(f"colorchannelmixer=aa={opacity:.4f}")
    return ",".join(parts)


def build_filter_complex(
    base_filter: str,
    settings: dict[str, Any] | None,
    *,
    width: int,
    height: int,
    fps: int,
    base_input: str = "0:v",
    effect_input: str = "1:v",
    text_filter: str | None = None,
) -> str:
    """
    Sinh filter_complex hoàn chỉnh: [base_input] qua base_filter (chuyển động ảnh),
    [effect_input] qua chuỗi tách nền/opacity rồi ghép.

    text_filter: chuỗi filter chữ (ví dụ "subtitles=text.ass") được nối SAU khi ghép hiệu ứng,
    ngay trước nhãn [out]. Nhờ đi qua cùng builder này, chữ khớp giữa preview và render cuối.
    """
    s = normalize_effect_settings(settings)
    use_blend = s["effect_type"] in ("screen_black", "normal") and s["blend_mode"] != "normal"
    fx_chain = _effect_chain(s, width, height, for_blend=use_blend)

    # Đuôi chung: (fps + text tùy chọn + yuv420p) -> [out].
    text_part = f"{text_filter}," if text_filter else ""
    tail = f"fps={fps},{text_part}format=yuv420p[out]"

    if use_blend:
        # Blend theo không gian RGB để screen/lighten/overlay/softlight ra màu đúng.
        mode = _FFMPEG_BLEND_NAMES[s["blend_mode"]]
        return (
            f"[{base_input}]{base_filter},format=gbrp[base];"
            f"[{effect_input}]{fx_chain},format=gbrp[fx];"
            f"[base][fx]blend=all_mode={mode}:all_opacity={s['opacity']:.4f}:shortest=1,"
            f"{tail}"
        )
    return (
        f"[{base_input}]{base_filter},format=rgba[base];"
        f"[{effect_input}]{fx_chain}[fx];"
        f"[base][fx]overlay=0:0:shortest=1:format=auto,"
        f"{tail}"
    )
