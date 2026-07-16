"""
CORE MODULE: ASS RENDERER
Biên dịch danh sách câu phụ đề kèm cấu hình style thành file phụ đề .ass chuẩn Karaoke.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("lofi.ass_renderer")

# Subtitle project metadata lives with its ASS compiler so the feature has one
# core module instead of separate renderer and manifest files.
SUBTITLES_DIR = Path("data/subtitles")
DEFAULT_SUBTITLE_STYLE = {
    "font_name": "Arial", "font_size_original": 32, "font_size_translation": 20,
    "primary_color": "#FFFFFF", "secondary_color": "#FFC0CB", "outline_color": "#000000",
    "outline_width": 2.5, "shadow_depth": 1.0, "alignment": 2, "margin_v": 60,
}

def get_subtitle_manifest_path(project_id: str) -> Path:
    SUBTITLES_DIR.mkdir(parents=True, exist_ok=True)
    return SUBTITLES_DIR / f"{project_id}_subtitle_manifest.json"

def load_subtitle_manifest(project_id: str) -> dict[str, Any]:
    path = get_subtitle_manifest_path(project_id)
    if path.is_file():
        try:
            with open(path, "r", encoding="utf-8") as file:
                data = json.load(file)
            style = data.setdefault("style", {})
            for key, value in DEFAULT_SUBTITLE_STYLE.items():
                style.setdefault(key, value)
            return data
        except Exception as exc:
            logger.error("Could not load subtitle manifest %s: %s", project_id, exc)
    return {"enabled": True, "reviewed": False, "language": "zh", "style": dict(DEFAULT_SUBTITLE_STYLE), "lyrics": []}

def save_subtitle_manifest(project_id: str, data: dict[str, Any]) -> None:
    with open(get_subtitle_manifest_path(project_id), "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)

def hex_to_ass_color(hex_str: str, alpha_hex: str = "00") -> str:
    """Chuyển đổi màu Hex (#RRGGBB) sang định dạng màu ASS (&HAABBGGRR) của FFmpeg libass."""
    hex_str = hex_str.strip().lstrip("#")
    if len(hex_str) != 6:
        hex_str = "FFFFFF"
    r = hex_str[0:2]
    g = hex_str[2:4]
    b = hex_str[4:6]
    # Thứ tự byte trong ASS là Alpha-Blue-Green-Red
    return f"&H{alpha_hex}{b}{g}{r}"

def format_ass_time(seconds: float) -> str:
    """Chuyển đổi giây thành định dạng thời gian ASS: H:MM:SS.cs (cs = centiseconds)."""
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int(round((seconds % 1) * 100))
    
    # Xử lý làm tròn tràn số
    if cs == 100:
        s += 1
        cs = 0
        if s == 60:
            m += 1
            s = 0
            if m == 60:
                h += 1
                m = 0
                
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

def generate_ass_file(
    lyrics: list[dict[str, Any]],
    style_config: dict[str, Any],
    output_path: Path
) -> Path:
    """
    Tạo file .ass phụ đề từ danh sách lyrics và cấu hình style.
    
    Mỗi mục trong danh sách lyrics có cấu trúc:
    {
        "start": float,
        "end": float,
        "text": str,           # Câu gốc (ví dụ tiếng Trung/Anh)
        "pinyin": str,         # Phiên âm (nếu có)
        "vietnamese": str,     # Dịch tiếng Việt (nếu có)
        "words": [             # Danh sách từ để chạy karaoke
            {"word": str, "start": float, "end": float},
            ...
        ]
    }
    """
    output_path = Path(output_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    font_name = style_config.get("font_name", "Arial")
    font_size_orig = style_config.get("font_size_original", 32)
    font_size_trans = style_config.get("font_size_translation", 20)
    
    # Màu sắc
    primary = hex_to_ass_color(style_config.get("secondary_color", "#FFC0CB")) # Màu chữ khi đã được tô (Active)
    secondary = hex_to_ass_color(style_config.get("primary_color", "#FFFFFF")) # Màu chữ khi chưa được tô (Inactive)
    outline = hex_to_ass_color(style_config.get("outline_color", "#000000"))
    shadow = "&H90000000"  # Bóng đổ bán trong suốt
    
    outline_width = float(style_config.get("outline_width", 2.5))
    shadow_depth = float(style_config.get("shadow_depth", 1.0))
    margin_v = int(style_config.get("margin_v", 60))
    alignment = int(style_config.get("alignment", 2)) # 2 = bottom center
    
    # Cấu trúc tiêu đề file .ass
    header = (
        "[Script Info]\n"
        "Title: Lofi Automation Karaoke Subtitles\n"
        "ScriptType: v4.00+\n"
        "WrapStyle: 0\n"
        "PlayResX: 1920\n"
        "PlayResY: 1080\n"
        "ScaledBorderAndShadow: yes\n\n"
        
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
    )
    
    # Style cho câu gốc (Original/Pinyin) - Hỗ trợ Karaoke chạy chữ
    # PrimaryColour là màu đã tô (Active), SecondaryColour là màu chờ tô (Inactive)
    style_original = (
        f"Style: Original,{font_name},{font_size_orig},{primary},{secondary},{outline},{shadow},"
        f"1,0,0,0,100,100,0,0,1,{outline_width},{shadow_depth},{alignment},10,10,{margin_v},1\n"
    )
    
    # Style cho câu dịch (Tiếng Việt) - Xuất hiện nguyên câu dạng Fade
    # Không chạy karaoke chữ dịch để tránh rối mắt, dùng primary làm màu chính
    style_translation = (
        f"Style: Translation,{font_name},{font_size_trans},{secondary},{secondary},{outline},{shadow},"
        f"0,0,0,0,100,100,0,0,1,{outline_width * 0.8},{shadow_depth * 0.8},{alignment},10,10,{margin_v - font_size_orig - 12},1\n"
    )
    
    lines = [header, style_original, style_translation, "\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"]
    
    for idx, item in enumerate(lyrics):
        start_sec = float(item["start"])
        end_sec = float(item["end"])
        
        # Bỏ qua câu có khoảng thời gian không hợp lý
        if start_sec >= end_sec:
            continue
            
        start_str = format_ass_time(start_sec)
        end_str = format_ass_time(end_sec)
        
        # 1. Tạo câu Karaoke cho dòng gốc (hoặc Pinyin nếu là tiếng Trung)
        text_orig = item.get("text", "").strip()
        pinyin_text = item.get("pinyin", "").strip()
        
        # Ưu tiên hiển thị Pinyin nếu có để hát theo dễ hơn, câu gốc chữ Hán đưa xuống dòng dịch hoặc gộp
        display_orig = pinyin_text if pinyin_text else text_orig
        
        # Dựng karaoke tags {\kf...} dựa trên word timings
        words = item.get("words") or []
        if words:
            karaoke_text = ""
            current_time = start_sec
            
            for w in words:
                w_start = float(w["start"])
                w_end = float(w["end"])
                w_text = w["word"]
                
                # Khoảng lặng giữa các từ
                if w_start > current_time:
                    silence_cs = int(round((w_start - current_time) * 100))
                    if silence_cs > 0:
                        karaoke_text += f"{{\\kf{silence_cs}}}"
                        
                word_cs = int(round((w_end - w_start) * 100))
                word_cs = max(1, word_cs) # tối thiểu 1 cs
                
                # Thêm dấu cách tự động cho tiếng Anh, tiếng Trung Pinyin đã phân tách sẵn
                karaoke_text += f"{{\\kf{word_cs}}}{w_text} "
                current_time = w_end
                
            karaoke_text = karaoke_text.strip()
        else:
            # Nếu không có từ chi tiết, dùng hiệu ứng karaoke chuyển đều toàn bộ câu
            total_cs = int(round((end_sec - start_sec) * 100))
            karaoke_text = f"{{\\kf{total_cs}}}{display_orig}"
            
        # Thêm hiệu ứng xuất hiện nhẹ nhàng (Fade-in 150ms, Fade-out 250ms)
        final_orig_text = f"{{\\fad(150,250)}}{karaoke_text}"
        
        # Ghi sự kiện dòng gốc
        lines.append(
            f"Dialogue: 0,{start_str},{end_str},Original,,0,0,0,,{final_orig_text}\n"
        )
        
        # 2. Tạo câu dịch nghĩa (Tiếng Việt)
        vietnamese_text = item.get("vietnamese", "").strip()
        # Nếu đang hiển thị Pinyin ở dòng trên, có thể gộp chữ Hán gốc vào dòng dịch dạng: "Dịch nghĩa (Chữ Hán)"
        if pinyin_text and text_orig:
            display_trans = f"{vietnamese_text} ({text_orig})" if vietnamese_text else text_orig
        else:
            display_trans = vietnamese_text
            
        if display_trans:
            # Dòng dịch chỉ cần fade nhẹ không chạy karaoke
            final_trans_text = f"{{\\fad(200,300)}}{display_trans}"
            lines.append(
                f"Dialogue: 0,{start_str},{end_str},Translation,,0,0,0,,{final_trans_text}\n"
            )
            
    # Lưu file
    with open(output_path, "w", encoding="utf-8") as f:
        f.writelines(lines)
        
    logger.info(f"[ASSRenderer] Đã xuất file phụ đề ASS: {output_path.name}")
    return output_path
