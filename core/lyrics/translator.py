"""
AI FILE NOTE - LYRICS TRANSLATOR
Chức năng chính:
- Tạo phiên âm Pinyin có dấu từ chữ Hán (dùng pypinyin nếu có).
- Dịch lời bài hát gốc sang tiếng Việt theo phong cách lofi bằng LLM (qua utils.helpers.call_llm_chat:
  provider chính theo config.PROMPT_API_*, tự fallback Gemini -> Pollinations; KHÔNG hardcode provider).
- Căn chỉnh để số dòng dịch khớp đúng số dòng gốc (cắt bớt hoặc đệm dòng rỗng).
Đầu vào chính:
- lines (list[str] câu gốc), song_info (dict tùy chọn: title, author).
Đầu ra chính:
- list[str] Pinyin hoặc list[str] câu tiếng Việt; fallback trả list rỗng khi lỗi.
API được file khác sử dụng:
- generate_pinyin(), translate_lyrics_to_vietnamese().
Phụ thuộc quan trọng:
- requests (gọi https://text.pollinations.ai), pypinyin (tùy chọn), logging.
Lưu ý khi sửa:
- Phải giữ nguyên số dòng đầu ra bằng số dòng đầu vào; nhiều module hạ nguồn dựa vào ánh xạ 1-1 theo dòng.
- pypinyin là phụ thuộc mềm; thiếu thì bỏ qua chứ không được crash.
"""
from __future__ import annotations

import json
import logging
import requests
from typing import Any

logger = logging.getLogger("lofi.lyrics_translator")

def generate_pinyin(lines: list[str]) -> list[str]:
    """Tạo Pinyin có dấu từ chữ Hán sử dụng thư viện pypinyin (nếu có)."""
    try:
        from pypinyin import pinyin, Style
        result = []
        for line in lines:
            if not line.strip():
                result.append("")
                continue
            p_list = pinyin(line, style=Style.TONE)
            # Gộp danh sách pinyin lại thành chuỗi, bỏ khoảng trắng dư thừa
            p_words = [item[0] for item in p_list if item]
            result.append(" ".join(p_words))
        return result
    except ImportError:
        logger.warning("[LyricsTranslator] Chưa cài đặt thư viện 'pypinyin'. Bỏ qua tự sinh Pinyin.")
        return [""] * len(lines)

def translate_lyrics_to_vietnamese(lines: list[str], song_info: dict | None = None) -> list[str]:
    """
    Dịch danh sách câu hát gốc sang tiếng Việt.
    Sử dụng Pollinations LLM (phục vụ miễn phí, không cần key) để dịch ngữ cảnh tốt nhất.
    """
    if not lines:
        return []
        
    info_str = ""
    if song_info:
        info_str = f"Bài hát: {song_info.get('title', 'Unknown')} - Ca sĩ: {song_info.get('author', 'Unknown')}\n"
        
    prompt = (
        "Bạn là một dịch giả chuyên nghiệp, chuyên dịch lời bài hát nước ngoài sang lời thơ/lời nhạc tiếng Việt mềm mại, lofi, bay bổng.\n"
        f"Hãy dịch danh sách các câu hát dưới đây sang tiếng Việt. {info_str}\n"
        "Yêu cầu:\n"
        "1. Dịch đúng nghĩa thơ, hợp phong cách nhạc Lo-Fi chill.\n"
        "2. Giữ nguyên số dòng. Mỗi dòng gốc tương ứng đúng 1 dòng dịch.\n"
        "3. Không thêm bất kỳ số thứ tự, dấu gạch đầu dòng, dấu ngoặc hay văn bản giải thích nào khác.\n"
        "4. Output chỉ chứa đúng các câu tiếng Việt đã dịch, phân tách bằng dấu xuống dòng.\n\n"
        "Danh sách các câu cần dịch:\n" + "\n".join(lines)
    )
    
    try:
        # Dùng hàm LLM chung (config.PROMPT_API_* + fallback Gemini->Pollinations).
        # KHÔNG hardcode provider nữa để chức năng dịch cũng theo cấu hình chung.
        from utils.helpers import call_llm_chat
        content = call_llm_chat(
            [
                {"role": "system", "content": "You are a professional lyrics translator translating to Vietnamese. Answer only with the translation, line by line, maintaining the exact same line count."},
                {"role": "user", "content": prompt},
            ],
            json_mode=False, max_tokens=900, temperature=0.6, timeout=25,
        )
        if content:
            content = content.strip()
            translated_lines = [line.strip() for line in content.splitlines() if line.strip() or line == ""]

            # Đảm bảo số lượng dòng khớp hoàn toàn
            if len(translated_lines) == len(lines):
                return translated_lines
            logger.warning(
                f"[LyricsTranslator] Số dòng dịch ({len(translated_lines)}) lệch với dòng gốc ({len(lines)}). "
                "Đang căn chỉnh dòng..."
            )
            if len(translated_lines) > len(lines):
                return translated_lines[:len(lines)]
            return translated_lines + [""] * (len(lines) - len(translated_lines))
    except Exception as exc:
        logger.error(f"[LyricsTranslator] Lỗi khi gọi API dịch LLM: {exc}")

    # Fallback: Trả về danh sách trống hoặc rỗng
    return [""] * len(lines)
