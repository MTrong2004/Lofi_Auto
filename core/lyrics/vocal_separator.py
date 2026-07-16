"""
CORE MODULE: VOCAL SEPARATOR
Tách giọng hát (vocal) và nhạc nền (instrumental) từ file nhạc gốc sử dụng Demucs.
"""
from __future__ import annotations

import os
import sys
import shutil
import subprocess
import logging
from pathlib import Path

logger = logging.getLogger("lofi.vocal_separator")

def is_demucs_installed() -> bool:
    """Kiểm tra xem thư viện demucs đã được cài đặt trong môi trường chưa."""
    try:
        # Chạy thử import trong một sub-process để tránh load torch vào tiến trình chính quá sớm
        res = subprocess.run(
            [sys.executable, "-c", "import demucs"],
            capture_output=True, text=True, timeout=10
        )
        return res.returncode == 0
    except Exception:
        return False

def separate_vocals(
    input_audio: Path,
    output_dir: Path,
    model: str = "htdemucs",
    progress_callback = None
) -> tuple[Path, Path]:
    """
    Tách vocal và instrumental từ file input_audio.
    Trả về: (vocal_path, instrumental_path)
    
    Nếu demucs chưa cài, ném ngoại lệ ImportError kèm hướng dẫn cài đặt.
    """
    input_audio = Path(input_audio).resolve()
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    
    if not input_audio.is_file():
        raise FileNotFoundError(f"Không tìm thấy file nhạc đầu vào: {input_audio}")
        
    # Định nghĩa đường dẫn file kết quả dự kiến (dựa trên hash hoặc tên file)
    stem = input_audio.stem
    vocal_final = output_dir / f"{stem}_vocals.wav"
    inst_final = output_dir / f"{stem}_instrumental.wav"
    
    # Nếu đã tồn tại file kết quả và kích thước hợp lệ -> dùng lại cache
    if vocal_final.exists() and inst_final.exists() and vocal_final.stat().st_size > 1024 * 1024:
        logger.info(f"[VocalSeparator] Dùng lại cache vocal và beat cho {stem}")
        return vocal_final, inst_final
        
    if not is_demucs_installed():
        raise ImportError(
            "Chưa cài đặt thư viện 'demucs' để tách vocal.\n"
            "Hãy chạy lệnh sau trong terminal của bạn để cài đặt:\n"
            "pip install demucs"
        )
        
    logger.info(f"[VocalSeparator] Đang chạy tách vocal cho {stem} sử dụng model {model}...")
    if progress_callback:
        progress_callback(0.1, "Đang khởi tạo Demucs (quá trình này có thể mất 1-2 phút ở lần đầu tiên)...")
        
    # Tạo thư mục tạm để demucs xuất file thô
    temp_demucs_dir = output_dir / "temp_demucs"
    temp_demucs_dir.mkdir(parents=True, exist_ok=True)
    
    # Lệnh chạy demucs: dùng hai stems để tách nhanh (chỉ tách vocal và no_vocal)
    cmd = [
        sys.executable, "-m", "demucs.separate",
        "-n", model,
        "--two-stems", "vocal",
        str(input_audio),
        "-o", str(temp_demucs_dir)
    ]
    
    try:
        # Chạy command
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace"
        )
        
        # Đọc log đầu ra để bắt tiến độ (nếu có)
        while True:
            line = process.stdout.readline()
            if not line:
                break
            line_str = line.strip()
            if line_str:
                logger.info(f"[Demucs] {line_str}")
                if progress_callback:
                    # Parse cơ bản để hiển thị tiến độ
                    if "Separating track" in line_str:
                        progress_callback(0.3, "Đang tách các track âm thanh...")
                    elif "Selected model" in line_str:
                        progress_callback(0.15, f"Đã chọn model: {model}")
                        
        process.wait()
        
        if process.returncode != 0:
            raise RuntimeError(f"Lỗi khi chạy Demucs (exit code {process.returncode})")
            
        # Tìm file kết quả trong thư mục đầu ra của demucs
        # Cấu trúc mặc định của demucs: <temp_demucs_dir>/<model>/<track_name>/vocal.wav và no_vocals.wav
        track_folder = temp_demucs_dir / model / stem
        vocal_src = track_folder / "vocal.wav"
        inst_src = track_folder / "no_vocals.wav"
        
        if not vocal_src.exists() or not inst_src.exists():
            raise FileNotFoundError("Demucs chạy thành công nhưng không tìm thấy file đầu ra vocal/no_vocals.")
            
        # Di chuyển về thư mục đích
        shutil.move(str(vocal_src), str(vocal_final))
        shutil.move(str(inst_src), str(inst_final))
        
        logger.info(f"[VocalSeparator] Đã tách vocal thành công: {vocal_final.name}")
        return vocal_final, inst_final
        
    finally:
        # Dọn dẹp thư mục tạm
        if temp_demucs_dir.exists():
            shutil.rmtree(temp_demucs_dir, ignore_errors=True)
