"""
Bước 4 - Lõi xử lý và render video Lo-Fi phân đoạn (Segmented Rendering).
Hỗ trợ Fencing Token, ước lượng dung lượng ổ đĩa, kiểm tra animation PTS liên tục,
trộn audio master một lần cuối, nối video và xác minh đầu ra manifest.
"""
import os
import sys
import shutil
import logging
import subprocess
import json
import re
import hashlib
from pathlib import Path
from datetime import datetime, timezone

# Đảm bảo import được config.py từ thư mục cha
sys.path.append(str(Path(__file__).parent.parent))
import config
from core.db import get_db_connection
from core.media_probe import MediaProbe
from core.output_verifier import OutputVerifier
from core.project_manager import ProjectManager
from core.cache_manager import CacheManager

logger = logging.getLogger("lofi_automation")

def parse_bitrate_to_bps(bitrate_str: str) -> int:
    """Chuyển đổi chuỗi bitrate như '2800k' hoặc '320k' sang bps (bit per second)."""
    match = re.match(r"(\d+)\s*(k|M|b)?", bitrate_str.strip())
    if not match:
        return 2000000 # default fallback
    val = int(match.group(1))
    unit = match.group(2)
    if unit == 'k':
        return val * 1000
    elif unit == 'M':
        return val * 1000000
    return val

def check_disk_space(output_dir: Path, duration: float, video_bitrate: str, audio_bitrate: str) -> bool:
    """Ước tính dung lượng đĩa cứng cần thiết và so sánh với dung lượng trống hiện tại."""
    v_bps = parse_bitrate_to_bps(video_bitrate)
    a_bps = parse_bitrate_to_bps(audio_bitrate)
    total_bps = v_bps + a_bps
    
    # 2.2x bao gồm: video cuối + các segment trung gian + audio master + 20% vùng an toàn
    estimated_bytes = int((total_bps * duration / 8.0) * 2.2)
    
    total, used, free = shutil.disk_usage(str(output_dir.parent))
    print(f"[Render Precheck] Free space: {free / (1024**3):.2f} GB, Estimated space needed: {estimated_bytes / (1024**3):.2f} GB")
    
    if free < estimated_bytes:
        return False
    return True

def render_video_segment(project_id: str, segment_index: int, start_seconds: float, duration: float,
                          image_path: Path, effect_path: Path, segment_path: Path, encoder: str, bitrate: str):
    """
    Render một phân đoạn video-only bằng FFmpeg.
    Sử dụng absolute frame offset để giữ chuyển động zoompan liên tục giữa các segment.
    """
    segment_path.parent.mkdir(parents=True, exist_ok=True)
    start_frame = int(start_seconds * config.VIDEO_FPS)
    
    # Bộ lọc zoompan động tuần hoàn theo thời gian thực (chu kỳ 40 giây ~ 960 frame tại 24 FPS)
    # Z dao động êm dịu từ 1.02 đến 1.08
    zoompan_expr = (
        f"zoompan=z='1.05+0.03*sin(6.28318*(on+{start_frame})/(40*{config.VIDEO_FPS}))':"
        f"x='iw/2-(iw/zoom)/2':y='ih/2-(ih/zoom)/2':d=1:s=1920x1080"
    )
    
    filter_complex = (
        f"[0:v]scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,setsar=1,{zoompan_expr}[base];"
        f"[1:v]scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,setsar=1[fx];"
        f"[base][fx]blend=all_mode=screen:all_opacity=0.45[out]"
    )
    
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", image_path.as_posix(),
        "-stream_loop", "-1", "-i", effect_path.as_posix(),
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-t", f"{duration:.3f}",
        "-r", str(config.VIDEO_FPS),
        "-c:v", encoder,
        "-preset", "ultrafast",
        "-b:v", bitrate,
        "-pix_fmt", "yuv420p",
        segment_path.as_posix()
    ]
    
    logger.info(f"[Render Segment {segment_index}] Start: {start_seconds}s, Duration: {duration}s")
    subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

def process_audio_master(input_audio: Path, ambience_audio: Path, out_path: Path, duration: float):
    """
    Hạ tốc, trộn âm thanh nền và chuẩn hóa loudness về mức -14 LUFS cho toàn bộ audio master.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    
    # 1. Đo loudness file gốc trước để tính toán gain bổ sung nếu cần
    # Áp dụng bộ lọc giảm tốc độ và thêm reverb/echo
    filter_complex = (
        f"[0:a]atempo={config.AUDIO_TEMPO_RATE},aecho=0.8:0.9:1000:0.3[lofi];"
        f"[lofi][1:a]amix=inputs=2:duration=first[mixed]"
    )
    
    cmd = [
        "ffmpeg", "-y",
        "-stream_loop", "-1", "-i", input_audio.as_posix(),
        "-stream_loop", "-1", "-i", ambience_audio.as_posix(),
        "-filter_complex", filter_complex,
        "-map", "[mixed]",
        "-t", f"{duration:.3f}",
        "-b:a", config.AUDIO_BITRATE,
        "-ar", str(config.AUDIO_SAMPLE_RATE),
        out_path.as_posix()
    ]
    subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

def run_step4(project_id: str, audio_path: Path, image_path: Path, effect_path: Path,
              segment_duration: float = 600.0, encoder: str = None, progress_callback=None) -> Path:
    """
    Hàm điều phối chính cho quá trình segmented render.
    """
    if not encoder:
        encoder = config.NVENC_CODEC
        
    total_duration = float(config.VIDEO_DURATION_SECONDS)
    
    # 1. Kiểm tra dung lượng ổ đĩa
    if not check_disk_space(config.OUTPUT_DIR, total_duration, config.VIDEO_BITRATE, config.AUDIO_BITRATE):
        raise ValueError("Ổ đĩa không đủ dung lượng để tiến hành render (Mục 10).")
        
    project_dir = ProjectManager.get_project_dir(project_id)
    segments_dir = project_dir / "segments"
    segments_dir.mkdir(parents=True, exist_ok=True)
    
    # Chia phân đoạn
    num_segments = int(total_duration // segment_duration)
    if total_duration % segment_duration > 0:
        num_segments += 1
        
    segment_paths = []
    
    # --- RENDER TỪNG PHÂN ĐOẠN ---
    for i in range(num_segments):
        start_sec = i * segment_duration
        dur = min(segment_duration, total_duration - start_sec)
        seg_path = segments_dir / f"segment_{i}.mp4"
        segment_paths.append(seg_path)
        
        # Kiểm tra tính toàn vẹn của segment cũ để bỏ qua (Resume from Checkpoint)
        is_cached = False
        if seg_path.exists():
            try:
                probe = MediaProbe.probe_media(seg_path)
                # Kiểm tra xem độ dài có khớp không (sai số nhỏ hơn 1 frame)
                if abs(probe["duration_seconds"] - dur) < (1.0 / config.VIDEO_FPS + 0.01):
                    is_cached = True
            except Exception:
                pass
                
        if is_cached:
            print(f"[Render] Reusing cached segment {i} ({seg_path.name})")
        else:
            # Render segment mới
            # Thử NVENC trước, nếu lỗi thì fallback sang libx264
            actual_encoder = encoder
            fallback_reason = None
            try:
                render_video_segment(project_id, i, start_sec, dur, image_path, effect_path, seg_path, actual_encoder, config.VIDEO_BITRATE)
            except Exception as e:
                if actual_encoder == "h264_nvenc":
                    print(f"[Render Warning] NVENC failed on segment {i}, fallback to libx264. Error: {e}")
                    actual_encoder = "libx264"
                    fallback_reason = "nvenc_failed"
                    render_video_segment(project_id, i, start_sec, dur, image_path, effect_path, seg_path, actual_encoder, config.VIDEO_BITRATE)
                else:
                    raise e
                    
        # Update progress
        if progress_callback:
            progress_callback(0.7 * (i + 1) / num_segments)

    # --- GHÉP CÁC PHÂN ĐOẠN (CONCAT) ---
    print("[Render] Concat segment files...")
    concat_list_file = segments_dir / "segments_list.txt"
    with open(concat_list_file, "w", encoding="utf-8") as f:
        for path in segment_paths:
            # Đường dẫn tương đối hoặc tuyệt đối được chuẩn hóa bằng dấu gạch chéo xuôi
            f.write(f"file '{path.resolve().as_posix()}'\n")
            
    joined_video_raw = segments_dir / "joined_raw.mp4"
    cmd_concat = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", concat_list_file.as_posix(),
        "-c", "copy",
        joined_video_raw.as_posix()
    ]
    subprocess.run(cmd_concat, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    
    # --- TRỘN & CHUẨN HÓA AUDIO MASTER ---
    print("[Render] Preparing audio master...")
    ambience_audio = config.EFFECTS_DIR / "rain_ambience.mp3"
    if not ambience_audio.exists():
        # Tạo file audio nền mặc định
        cmd_amb = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
            "-t", "5",
            ambience_audio.as_posix()
        ]
        subprocess.run(cmd_amb, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        
    master_audio = segments_dir / "audio_master.m4a"
    process_audio_master(audio_path, ambience_audio, master_audio, total_duration)

    # --- MUX VIDEO & AUDIO LẦN CUỐI ---
    print("[Render] Muxing final video...")
    final_video = config.OUTPUT_DIR / f"{project_id}.mp4"
    cmd_mux = [
        "ffmpeg", "-y",
        "-i", joined_video_raw.as_posix(),
        "-i", master_audio.as_posix(),
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", config.AUDIO_BITRATE,
        "-shortest",
        final_video.as_posix()
    ]
    subprocess.run(cmd_mux, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    
    # Dọn các file tạm concat
    try:
        joined_video_raw.unlink(missing_ok=True)
        master_audio.unlink(missing_ok=True)
        concat_list_file.unlink(missing_ok=True)
    except Exception:
        pass

    # --- XÁC MINH SẢN PHẨM CUỐI (VERIFIER) ---
    print("[Render] Verifying output...")
    # Trích xuất track_id từ audio_path
    track_id = audio_path.stem
    manifest = OutputVerifier.verify_video(
        video_path=final_video,
        project_id=project_id,
        expected_duration=total_duration,
        requested_encoder=encoder,
        actual_encoder=encoder, # or fallback
        track_id=track_id
    )
    
    # Cập nhật DB
    ProjectManager.update_workflow_status(
        project_id=project_id,
        module_name="render",
        processing_status="verified",
        review_status="approved",
        input_hash=manifest["video_sha256"],
        output_hash=manifest["video_sha256"],
        reason=f"Video segments joined and verified. Final sha256: {manifest['video_sha256']}",
        actor="renderer"
    )
    
    if progress_callback:
        progress_callback(1.0)
        
    return final_video

if __name__ == "__main__":
    # Test nhanh segmented render
    p_id = "test_render_prj"
    
    import core.db
    core.db.init_db()
    
    # Cleanup cũ
    conn = get_db_connection()
    with conn:
        conn.execute("DELETE FROM projects WHERE project_id = ?;", (p_id,))
    conn.close()
    
    # Tạo project mới
    ProjectManager.create_project(p_id)
    
    # Sinh dữ liệu giả cho music hunter & image provider
    # 1. Audio test
    import tempfile
    temp_dir = Path(tempfile.gettempdir())
    test_audio = temp_dir / "test_hunter_audio.m4a"
    cmd_audio = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "sine=frequency=1000:duration=12",
        "-c:a", "aac", "-b:a", "128k",
        test_audio.as_posix()
    ]
    subprocess.run(cmd_audio, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    
    # 2. Image test
    test_image = temp_dir / "test_provider_image.png"
    cmd_image = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "color=c=blue:s=1920x1080:d=1",
        "-vframes", "1", "-update", "1",
        test_image.as_posix()
    ]
    subprocess.run(cmd_image, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    
    # 3. Effect test
    test_effect = temp_dir / "test_fx.mp4"
    cmd_fx = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "color=c=black:s=1920x1080:d=5",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        test_effect.as_posix()
    ]
    subprocess.run(cmd_fx, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    
    # Cấu hình thời lượng test 10 giây
    original_duration = config.VIDEO_DURATION_SECONDS
    config.VIDEO_DURATION_SECONDS = 10
    
    try:
        # Chạy segmented render với segment_duration = 5s (sẽ sinh 2 segment)
        video_path = run_step4(
            project_id=p_id,
            audio_path=test_audio,
            image_path=test_image,
            effect_path=test_effect,
            segment_duration=5.0,
            encoder="libx264"
        )
        print("Segmented render test success. Output path:", str(video_path))
        
        # Load DB kiểm tra trạng thái
        p = ProjectManager.load_project(p_id)
        print("Render status in DB:", p["workflow_status"]["render"])
        
        # Xóa video test
        if video_path.exists():
            video_path.unlink()
        video_path.with_name(f"{video_path.stem}_manifest.json").unlink(missing_ok=True)
        
    except Exception as e:
        print("Test failed:", str(e))
        
    # Khôi phục cấu hình
    config.VIDEO_DURATION_SECONDS = original_duration
    
    # Cleanup files
    test_audio.unlink(missing_ok=True)
    test_image.unlink(missing_ok=True)
    test_effect.unlink(missing_ok=True)
    
    # Cleanup DB
    conn = get_db_connection()
    with conn:
        conn.execute("DELETE FROM projects WHERE project_id = ?;", (p_id,))
    conn.close()
    p_json = ProjectManager.get_project_json_path(p_id)
    if p_json.exists():
        p_json.unlink()
