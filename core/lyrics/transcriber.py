"""
CORE MODULE: LYRICS TRANSCRIBER
Nhận dạng giọng nói/lời hát từ file Vocal sử dụng OpenAI Whisper.
"""
from __future__ import annotations

import sys
import subprocess
import logging
from pathlib import Path

logger = logging.getLogger("lofi.lyrics_transcriber")

def is_whisper_installed() -> bool:
    """Kiểm tra xem thư viện openai-whisper đã được cài đặt chưa."""
    try:
        res = subprocess.run(
            [sys.executable, "-c", "import whisper"],
            capture_output=True, text=True, timeout=10
        )
        return res.returncode == 0
    except Exception:
        return False

def transcribe_vocals(
    vocal_path: Path,
    model_name: str = "base",
    language: str | None = None,
    progress_callback = None
) -> list[dict]:
    """
    Nhận diện giọng hát từ file vocal_path.
    Trả về danh sách các câu kèm timestamp và word-level timing:
    [
        {
            "start": float,
            "end": float,
            "text": str,
            "words": [
                {"word": str, "start": float, "end": float},
                ...
            ]
        }
    ]
    """
    vocal_path = Path(vocal_path).resolve()
    if not vocal_path.is_file():
        raise FileNotFoundError(f"Không tìm thấy file vocal: {vocal_path}")
        
    if not is_whisper_installed():
        raise ImportError(
            "Chưa cài đặt thư viện 'openai-whisper' để tự động nhận dạng lời hát.\n"
            "Hãy chạy lệnh sau trong terminal của bạn để cài đặt:\n"
            "pip install openai-whisper"
        )
        
    logger.info(f"[LyricsTranscriber] Đang nhận diện {vocal_path.name} bằng Whisper ({model_name})...")
    if progress_callback:
        progress_callback(0.2, "Đang tải mô hình Whisper...")
        
    import whisper
    
    # Load model
    model = whisper.load_model(model_name)
    
    if progress_callback:
        progress_callback(0.4, "Đang xử lý âm thanh và trích xuất lời nhạc...")
        
    # Chạy transcribe với word timestamps
    transcribe_options = {
        "word_timestamps": True,
        "task": "transcribe"
    }
    if language:
        transcribe_options["language"] = language
        
    result = model.transcribe(str(vocal_path), **transcribe_options)
    
    if progress_callback:
        progress_callback(0.9, "Đang hoàn tất nhận dạng lời...")
        
    segments = []
    raw_segments = result.get("segments") or []
    
    for seg in raw_segments:
        words_list = []
        raw_words = seg.get("words") or []
        for rw in raw_words:
            words_list.append({
                "word": rw.get("word", "").strip(),
                "start": float(rw.get("start", 0.0)),
                "end": float(rw.get("end", 0.0))
            })
            
        # Nếu Whisper không trả về word timestamps cho phân đoạn này, phân bổ đều theo số từ
        text = seg.get("text", "").strip()
        if not words_list and text:
            words = text.split()
            seg_start = float(seg.get("start", 0.0))
            seg_end = float(seg.get("end", 0.0))
            duration = seg_end - seg_start
            word_dur = duration / max(1, len(words))
            for idx, w in enumerate(words):
                words_list.append({
                    "word": w,
                    "start": seg_start + idx * word_dur,
                    "end": seg_start + (idx + 1) * word_dur
                })
                
        segments.append({
            "start": float(seg.get("start", 0.0)),
            "end": float(seg.get("end", 0.0)),
            "text": text,
            "words": words_list
        })
        
    logger.info(f"[LyricsTranscriber] Hoàn tất nhận dạng. Tìm thấy {len(segments)} phân đoạn câu.")
    return segments
