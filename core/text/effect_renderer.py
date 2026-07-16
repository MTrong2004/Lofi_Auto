"""
AI FILE NOTE - TEXT EFFECT RENDERER (sinh file ASS cho chữ động)

Chức năng chính:
- Sinh file .ass (libass) cho một dòng chữ tùy chọn, dùng CHUNG cho preview và render cuối.
- Hỗ trợ preset chuyển động vào/giữ/ra: fade, blur_in, scale_slow, slide_up, slide_left,
  soft_glow, dissolve — đều bằng tag ASS native (\\fad, \\blur, \\t, \\move, \\be).
- Xử lý render PHÂN ĐOẠN: nhận segment_start_seconds + segment_duration + total_duration,
  dịch mốc thời gian và chỉ chèn intro/outro ở segment tương ứng. Trả None nếu segment
  không giao với khoảng thời gian có chữ.
- Định vị theo lưới 9 ô + safe zone cho video 1920x1080 (tự co giãn theo kích thước thật).

API được file khác sử dụng:
- build_ass_file()
- POSITIONS, INTRO_EFFECTS, HOLD_EFFECTS, OUTRO_EFFECTS

Lưu ý khi sửa:
- Không nhận font path từ AI; font_family do core.text.provider quyết định.
- Mốc thời gian intro/outro tính tương đối với Dialogue Start nên an toàn khi cắt theo segment.
- Tránh ký tự { } trong nội dung vì mở/đóng block override của ASS.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

# Lưới 9 ô -> mã alignment numpad của ASS (\an).
POSITIONS = {
    "bottom_left": 1, "bottom_center": 2, "bottom_right": 3,
    "center_left": 4, "center": 5, "center_right": 6,
    "top_left": 7, "top_center": 8, "top_right": 9,
}
INTRO_EFFECTS = ("fade", "blur_in", "scale_slow", "slide_up", "slide_left")
HOLD_EFFECTS = ("none", "soft_glow", "glow_breathe")
OUTRO_EFFECTS = ("fade", "dissolve")

# Safe zone mặc định cho 1920x1080; co giãn tuyến tính theo kích thước thật.
_SAFE = {"top": 60, "bottom": 100, "left": 80, "right": 80}
_REF_W, _REF_H = 1920, 1080


def _ass_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours}:{minutes:02d}:{secs:05.2f}"


def _ass_color(hex_color: str, alpha: int = 0) -> str:
    """#RRGGBB -> &HAABBGGRR (ASS: alpha 00=đục, FF=trong; màu theo thứ tự BGR)."""
    text = str(hex_color or "").strip().lstrip("#")
    if len(text) != 6:
        text = "FFFFFF"
    r, g, b = text[0:2], text[2:4], text[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def _escape_text(content: str) -> str:
    out = str(content or "").replace("{", "(").replace("}", ")")
    # Chuẩn hóa xuống dòng thành \N của ASS.
    out = out.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\N")
    return out.strip()


def _safe_margins(width: int, height: int) -> dict[str, int]:
    sx = width / _REF_W
    sy = height / _REF_H
    return {
        "left": int(_SAFE["left"] * sx),
        "right": int(_SAFE["right"] * sx),
        "top": int(_SAFE["top"] * sy),
        "bottom": int(_SAFE["bottom"] * sy),
    }


def _anchor_xy(alignment: int, width: int, height: int, margins: dict[str, int]) -> tuple[int, int]:
    """Tọa độ điểm neo tương ứng alignment (khớp \\an + margin), dùng cho \\move/\\pos."""
    col = (alignment - 1) % 3  # 0=trái,1=giữa,2=phải
    row = (alignment - 1) // 3  # 0=dưới,1=giữa,2=trên
    if col == 0:
        x = margins["left"]
    elif col == 1:
        x = width // 2
    else:
        x = width - margins["right"]
    if row == 0:
        y = height - margins["bottom"]
    elif row == 1:
        y = height // 2
    else:
        y = margins["top"]
    return x, y


def _override_tags(
    profile: dict[str, Any],
    *,
    alignment: int,
    width: int,
    height: int,
    margins: dict[str, int],
    line_dur_ms: int,
    include_intro: bool,
    include_outro: bool,
) -> str:
    intro = str(profile.get("intro_effect") or "fade")
    hold = str(profile.get("hold_effect") or "none")
    outro = str(profile.get("outro_effect") or "fade")
    intro_ms = int(max(0.0, float(profile.get("intro_duration", 0.8))) * 1000)
    outro_ms = int(max(0.0, float(profile.get("outro_duration", 1.0))) * 1000)
    intro_ms = min(intro_ms, max(0, line_dur_ms))
    outro_ms = min(outro_ms, max(0, line_dur_ms - intro_ms))

    tags: list[str] = []

    # Fade alpha: intro và/hoặc dissolve/fade outro.
    fad_in = intro_ms if include_intro else 0
    fad_out = outro_ms if (include_outro and outro in ("fade", "dissolve")) else 0
    if fad_in or fad_out:
        tags.append(f"\\fad({fad_in},{fad_out})")

    # Glow giữ: dùng \be (làm mềm mép) cho quầng sáng nhẹ, không xung đột \blur animation.
    if hold in ("soft_glow", "glow_breathe"):
        tags.append("\\be2")

    # Intro động.
    if include_intro and intro_ms > 0:
        if intro == "blur_in":
            tags.append(f"\\blur8\\t(0,{intro_ms},\\blur0)")
        elif intro == "scale_slow":
            tags.append(f"\\fscx92\\fscy92\\t(0,{intro_ms},\\fscx100\\fscy100)")
        elif intro in ("slide_up", "slide_left"):
            x, y = _anchor_xy(alignment, width, height, margins)
            if intro == "slide_up":
                x0, y0 = x, y + int(0.04 * height)
            else:
                x0, y0 = x + int(0.05 * width), y
            tags.append(f"\\move({x0},{y0},{x},{y},0,{intro_ms})")

    # Outro dissolve: nở nhẹ + tăng blur ở cuối (alpha đã lo bởi \fad).
    if include_outro and outro == "dissolve" and outro_ms > 0:
        start = max(0, line_dur_ms - outro_ms)
        tags.append(f"\\t({start},{line_dur_ms},\\blur6\\fscx105\\fscy105)")

    return "{" + "".join(tags) + "}" if tags else ""


def build_ass_file(
    profile: dict[str, Any],
    out_path: str | Path,
    *,
    width: int,
    height: int,
    total_duration: float,
    font_family: str = "Arial",
    segment_start: float = 0.0,
    segment_duration: float | None = None,
) -> Path | None:
    """
    Ghi file .ass cho cửa sổ thời gian [segment_start, segment_start+segment_duration].
    Trả về đường dẫn file, hoặc None nếu segment không chứa chữ (không cần filter).
    """
    content = _escape_text(profile.get("content"))
    if not content:
        return None

    seg_start = max(0.0, float(segment_start))
    seg_dur = float(segment_duration) if segment_duration is not None else float(total_duration) - seg_start
    seg_end = seg_start + seg_dur

    # Khoảng thời gian chữ hiển thị (tuyệt đối). Mặc định phủ toàn video.
    text_start = max(0.0, float(profile.get("start_seconds", 0.0)))
    text_end = profile.get("end_seconds")
    text_end = float(text_end) if text_end not in (None, "") else float(total_duration)
    text_end = min(text_end, float(total_duration))

    overlap_start = max(text_start, seg_start)
    overlap_end = min(text_end, seg_end)
    if overlap_end <= overlap_start + 1e-3:
        return None

    local_start = overlap_start - seg_start
    local_end = overlap_end - seg_start
    line_dur_ms = int(round((local_end - local_start) * 1000))

    # intro nằm ở segment chứa mốc bắt đầu thật; outro ở segment chứa mốc kết thúc thật.
    include_intro = seg_start <= text_start < seg_end
    include_outro = seg_start < text_end <= seg_end + 1e-6

    alignment = POSITIONS.get(str(profile.get("position") or "center"), 5)
    margins = _safe_margins(width, height)
    override = _override_tags(
        profile,
        alignment=alignment, width=width, height=height, margins=margins,
        line_dur_ms=line_dur_ms, include_intro=include_intro, include_outro=include_outro,
    )

    font_size = int(max(16, min(int(profile.get("font_size", 72)), 200)))
    outline_width = float(max(0.0, min(float(profile.get("outline_width", 2.0)), 8.0)))
    primary = _ass_color(profile.get("text_color", "#FFFFFF"))
    outline = _ass_color(profile.get("outline_color", "#000000"))
    bold = -1 if profile.get("bold") else 0

    style = (
        f"Style: Text,{font_family},{font_size},{primary},{primary},{outline},&H64000000,"
        f"{bold},0,0,0,100,100,0,0,1,{outline_width:.1f},1,{alignment},"
        f"{margins['left']},{margins['right']},{margins['bottom']},1"
    )

    dialogue = (
        f"Dialogue: 0,{_ass_time(local_start)},{_ass_time(local_end)},Text,,0,0,0,,{override}{content}"
    )

    ass = "\n".join([
        "[Script Info]",
        "ScriptType: v4.00+",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        f"PlayResX: {int(width)}",
        f"PlayResY: {int(height)}",
        "",
        "[V4+ Styles]",
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,"
        "Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,"
        "Alignment,MarginL,MarginR,MarginV,Encoding",
        style,
        "",
        "[Events]",
        "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text",
        dialogue,
        "",
    ])
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(ass, encoding="utf-8")
    return out_path
