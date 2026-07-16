"""
STEP 3: SUBTITLE PROVIDER AND TIMING COORDINATOR
Điều phối quá trình tách vocal, nhận dạng lời Whisper, dịch nghĩa và xuất phụ đề .ass/.srt.
"""
from __future__ import annotations

import logging
import re
import importlib
from pathlib import Path
from typing import Any

import requests

# Import các module cốt lõi từ core/
import core.lyrics.vocal_separator as separator
import core.lyrics.transcriber as transcriber
import core.lyrics.translator as translator
import core.text.ass_renderer as renderer

# Streamlit giữ module trong cache giữa các lần rerun. Reload để bản renderer
# vừa gộp manifest được dùng ngay, thay vì bản cũ thiếu các hàm manifest.
renderer = importlib.reload(renderer)

logger = logging.getLogger("lofi.step3_subtitle_provider")
_LRCLIB_SEARCH_URL = "https://lrclib.net/api/search"
_TIMED_LINE = re.compile(r"^\[(?P<minutes>\d{1,3}):(?P<seconds>\d{2}(?:\.\d{1,3})?)\](?P<text>.*)$")

def get_vocals_dir() -> Path:
    """Trả về thư mục lưu trữ file vocal tách và phụ đề."""
    path = Path("data/subtitles")
    path.mkdir(parents=True, exist_ok=True)
    return path

def run_vocal_separation(
    audio_path: Path,
    model: str = "htdemucs",
    progress_callback = None
) -> tuple[Path, Path]:
    """Tách vocal và instrumental."""
    return separator.separate_vocals(
        input_audio=audio_path,
        output_dir=get_vocals_dir(),
        model=model,
        progress_callback=progress_callback
    )

def run_transcription(
    vocal_path: Path,
    model_name: str = "base",
    language: str | None = None,
    progress_callback = None
) -> list[dict[str, Any]]:
    """Nhận diện lời từ vocal."""
    return transcriber.transcribe_vocals(
        vocal_path=vocal_path,
        model_name=model_name,
        language=language,
        progress_callback=progress_callback
    )

def find_online_lyrics(title: str, artist: str, duration: float = 180.0) -> dict[str, Any]:
    """Ưu tiên lấy lời đã xuất bản online, kèm timestamp nếu nguồn có."""
    try:
        response = requests.get(
            _LRCLIB_SEARCH_URL,
            params={"track_name": title, "artist_name": artist},
            timeout=12,
        )
        response.raise_for_status()
        results = response.json()
    except (requests.RequestException, ValueError) as exc:
        return {"found": False, "reason": f"Không thể tra lời online: {exc}"}
    if not results:
        return {"found": False, "reason": "Không tìm thấy lời bài hát đã xuất bản."}
    best = min(results, key=lambda item: abs(float(item.get("duration") or duration) - duration))
    synced, plain = best.get("syncedLyrics"), best.get("plainLyrics")
    segments = _segments_from_synced(synced, duration) if synced else _segments_from_plain(plain or "", duration)
    if not segments:
        return {"found": False, "reason": "Nguồn online không có nội dung lời dùng được."}
    return {"found": True, "segments": segments, "source": "LRCLIB", "timing": "synced" if synced else "estimated"}

def search_online_lyrics(title: str, artist: str, duration: float = 180.0, limit: int = 5) -> dict[str, Any]:
    """Tìm nhiều phiên bản lyric để tránh tự chọn nhầm bản remix/live."""
    try:
        response = requests.get(
            _LRCLIB_SEARCH_URL,
            params={"track_name": title, "artist_name": artist},
            timeout=12,
        )
        response.raise_for_status()
        results = response.json()
        if not results:
            response = requests.get(_LRCLIB_SEARCH_URL, params={"q": f"{title} {artist}".strip()}, timeout=12)
            response.raise_for_status()
            results = response.json()
    except (requests.RequestException, ValueError) as exc:
        return {"found": False, "reason": f"Không thể tra lời online: {exc}"}
    if not results:
        return {"found": False, "reason": "Không tìm thấy lời bài hát đã xuất bản."}

    def rank(item: dict[str, Any]) -> float:
        try:
            duration_gap = abs(float(item.get("duration") or duration) - duration)
        except (TypeError, ValueError):
            duration_gap = duration
        return duration_gap + (0 if item.get("syncedLyrics") else 15)

    candidates = []
    for item in sorted(results, key=rank)[:limit]:
        synced, plain = item.get("syncedLyrics"), item.get("plainLyrics")
        segments = _segments_from_synced(synced, duration) if synced else _segments_from_plain(plain or "", duration)
        if segments:
            candidates.append({
                "source": "LRCLIB", "track_name": item.get("trackName") or title,
                "artist_name": item.get("artistName") or artist, "album_name": item.get("albumName") or "",
                "duration": item.get("duration"), "segments": segments,
                "timing": "synced" if synced else "estimated",
            })
    if not candidates:
        return {"found": False, "reason": "Nguồn online không có nội dung lời dùng được."}
    return {"found": True, "candidates": candidates}

def _word_timing(text: str, start: float, end: float) -> list[dict[str, Any]]:
    words = text.split()
    chunk = max(0.05, end - start) / max(1, len(words))
    return [{"word": word, "start": start + index * chunk, "end": start + (index + 1) * chunk} for index, word in enumerate(words)]

def _segments_from_synced(text: str, duration: float) -> list[dict[str, Any]]:
    rows = []
    for raw_line in text.splitlines():
        match = _TIMED_LINE.match(raw_line.strip())
        if match and match.group("text").strip():
            rows.append((int(match.group("minutes")) * 60 + float(match.group("seconds")), match.group("text").strip()))
    segments = []
    for index, (start, line) in enumerate(rows):
        end = max(start + 0.5, rows[index + 1][0] if index + 1 < len(rows) else duration)
        segments.append({"start": start, "end": end, "text": line, "words": _word_timing(line, start, end), "pinyin": "", "vietnamese": ""})
    return segments

def _segments_from_plain(text: str, duration: float) -> list[dict[str, Any]]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    chunk = max(1.5, duration / max(1, len(lines)))
    return [
        {"start": index * chunk, "end": min(duration, (index + 1) * chunk), "text": line,
         "words": _word_timing(line, index * chunk, min(duration, (index + 1) * chunk)), "pinyin": "", "vietnamese": ""}
        for index, line in enumerate(lines)
    ]

def auto_translate_and_pinyin(
    segments: list[dict[str, Any]],
    source_language: str,
    song_info: dict | None = None
) -> list[dict[str, Any]]:
    """
    Tự động dịch nghĩa tiếng Việt cho toàn bộ phân đoạn và tạo phiên âm Pinyin (nếu là tiếng Trung).
    """
    if not segments:
        return []
        
    original_texts = [seg.get("text", "").strip() for seg in segments]
    
    # 1. Sinh Pinyin nếu là tiếng Trung
    pinyin_lines = []
    if source_language == "zh":
        pinyin_lines = translator.generate_pinyin(original_texts)
    else:
        pinyin_lines = [""] * len(original_texts)
        
    # 2. Dịch nghĩa tiếng Việt bằng LLM
    vietnamese_lines = translator.translate_lyrics_to_vietnamese(original_texts, song_info)
    
    # 3. Gộp ngược lại vào segments
    for idx, seg in enumerate(segments):
        if idx < len(pinyin_lines):
            seg["pinyin"] = pinyin_lines[idx]
        if idx < len(vietnamese_lines):
            seg["vietnamese"] = vietnamese_lines[idx]
            
    return segments

def generate_subtitles_file(
    project_id: str,
    segments: list[dict[str, Any]],
    style_config: dict[str, Any]
) -> Path:
    """Tạo file phụ đề .ass karaoke hoàn chỉnh cho dự án."""
    output_path = get_vocals_dir() / f"{project_id}_subtitles.ass"
    return renderer.generate_ass_file(segments, style_config, output_path)

def load_project_subtitles(project_id: str) -> dict[str, Any]:
    """Tải cấu hình phụ đề dự án từ manifest."""
    return renderer.load_subtitle_manifest(project_id)

def save_project_subtitles(project_id: str, data: dict[str, Any]) -> None:
    """Lưu cấu hình phụ đề dự án vào manifest."""
    renderer.save_subtitle_manifest(project_id, data)
