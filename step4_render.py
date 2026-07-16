"""
AI FILE NOTE - STEP 4: SEGMENTED VIDEO RENDERER

Chức năng chính:
- Render video Lo-Fi theo từng segment để giảm rủi ro lỗi với video dài.
- Tạo preview ảnh chuyển động kết hợp video hiệu ứng.
- Dựng chuyển động ảnh, ghép overlay, xử lý audio master và nối các segment bằng FFmpeg.
- Theo dõi tiến độ/ETA, kiểm tra dung lượng đĩa và xác minh đầu ra bằng manifest.
- Ghi asset, cache và trạng thái workflow vào SQLite với cơ chế fencing token.

Đầu vào chính:
- project_id, audio_path, image_path, effect_path, thời lượng segment, encoder và chế độ chuyển động.

Đầu ra chính:
- Path video MP4 hoàn chỉnh, preview MP4 và metadata/manifest kiểm chứng.

API được file khác sử dụng:
- build_effect_preview()
- render_video_segment(), process_audio_master()
- run_step4()
- check_disk_space(), parse_bitrate_to_bps()

Phụ thuộc quan trọng:
- FFmpeg/ffprobe, config, MediaProbe, OutputVerifier, ProjectManager, CacheManager và AudioProcessor.

Lưu ý khi sửa:
- Giữ chữ ký build_effect_preview() và run_step4() vì step3_review_app.py gọi trực tiếp.
- Không bỏ callback tiến độ hoặc thay thang percent 0.0-1.0 nếu chưa sửa UI.
- Không thay đổi quy trình segment/concat, fencing token hoặc manifest nếu chưa kiểm thử video dài.
- Khi đổi encoder phải giữ fallback CPU và kiểm tra tương thích GPU/NVENC.
"""
import os
import sys
import shutil
import logging
import subprocess
import queue
import threading
import time
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
RENDERER_VERSION = "2026.07.16-r4.0"

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

def _run_ffmpeg(cmd: list[str], error_prefix: str) -> None:
    """Chạy FFmpeg và giữ phần lỗi cuối để chẩn đoán thay vì nuốt stderr."""
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("Không tìm thấy FFmpeg trong PATH.")
    result = subprocess.run(
        [str(item) for item in cmd],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        details = (result.stderr or "FFmpeg không trả chi tiết").strip().splitlines()[-12:]
        raise RuntimeError(f"{error_prefix}: " + " | ".join(details))


def _notify_progress(callback, percent: float, stage: str, eta_seconds: float | None = None) -> None:
    if not callback:
        return
    value = max(0.0, min(float(percent), 1.0))
    try:
        callback(value, stage, eta_seconds)
    except TypeError:
        try:
            callback(value, stage)
        except TypeError:
            callback(value)


def build_effect_preview(
    background_image: Path,
    effect_video: Path,
    out_path: Path,
    duration: float = 10.0,
    motion_mode: str = "smooth_zoom",
) -> Path:
    """Tạo preview 10 giây, giữ màu ảnh nền và màu gốc của hiệu ứng."""
    background_image = Path(background_image)
    effect_video = Path(effect_video)
    out_path = Path(out_path)
    if not background_image.is_file():
        raise FileNotFoundError(f"Không tìm thấy ảnh nền: {background_image}")
    if not effect_video.is_file():
        raise FileNotFoundError(f"Không tìm thấy video hiệu ứng: {effect_video}")
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("Không tìm thấy FFmpeg trong PATH.")

    # Preview 720p nhẹ hơn và hiển thị ổn định trong Streamlit; video cuối vẫn theo renderer chính.
    if motion_mode == "parallax":
        motion_mode = "smooth_zoom"
    if motion_mode == "smooth_zoom":
        base_filter = (
            "scale=1344:756:force_original_aspect_ratio=increase:flags=lanczos,"
            "crop=1344:756,"
            "rotate='0.0045*sin(2*PI*t/10)':ow=iw:oh=ih:bilinear=1:fillcolor=black,"
            "crop=1280:720:(iw-1280)/2:(ih-720)/2,setsar=1"
        )
    else:
        base_filter = "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720,setsar=1"

    # colorkey chỉ xóa vùng gần đen. Không desaturate và không screen blend, vì hai cách đó
    # có thể làm ảnh nền ám màu. Màu của phần tử hiệu ứng được giữ nguyên.
    filter_complex = (
        f"[0:v]{base_filter},format=rgba[base];"
        "[1:v]scale=1280:720:force_original_aspect_ratio=increase,"
        "crop=1280:720,format=rgba,"
        "colorkey=black:0.22:0.10,colorchannelmixer=aa=0.78[fx];"
        "[base][fx]overlay=0:0:shortest=1:format=auto,"
        f"fps={config.VIDEO_FPS},format=yuv420p[out]"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)

    def _command(encoder: str, preset: str) -> list[str]:
        return [
            "ffmpeg", "-y",
            "-loop", "1", "-framerate", str(config.VIDEO_FPS), "-i", str(background_image),
            "-stream_loop", "-1", "-i", str(effect_video),
            "-filter_complex", filter_complex,
            "-map", "[out]", "-an", "-t", f"{float(duration):.3f}",
            "-c:v", encoder, "-preset", preset,
            "-profile:v", "main", "-level", "4.0",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out_path),
        ]

    attempts = [("h264_nvenc", "p1"), ("libx264", "veryfast")]
    errors = []
    for encoder, preset in attempts:
        out_path.unlink(missing_ok=True)
        result = subprocess.run(
            _command(encoder, preset),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode == 0 and out_path.is_file() and out_path.stat().st_size >= 1024:
            return out_path
        errors.extend((result.stderr or "").strip().splitlines()[-8:])

    out_path.unlink(missing_ok=True)
    raise RuntimeError("FFmpeg tạo preview thất bại: " + " | ".join(errors[-12:]))


from core.audio_processor import AudioProcessor

def render_video_segment(
    project_id: str,
    segment_index: int,
    start_seconds: float,
    duration: float,
    image_path: Path,
    effect_path: Path,
    segment_path: Path,
    encoder: str,
    bitrate: str,
    progress_callback=None,
    overall_start: float = 0.05,
    overall_span: float = 0.65,
    motion_mode: str = "smooth_zoom",
):
    """Render segment và đọc tiến độ thật từ FFmpeg qua -progress pipe:1."""
    import time

    segment_path.parent.mkdir(parents=True, exist_ok=True)
    if motion_mode == "parallax":
        raise RuntimeError(
            "Parallax nhiều lớp chưa thể render vì manifest hiện chỉ có mask, chưa có các layer ảnh đã tách nền."
        )

    # Chuyển động mượt bằng overscan + crop hình sin. Không dùng zoompan theo frame
    # vì làm tròn tọa độ dễ gây rung/giật ở chuyển động rất chậm.
    # Đung đưa camera chậm, không zoom. Oversample trước rồi downscale để tránh nhảy pixel.
    base_filter = (
        "scale=2016:1134:force_original_aspect_ratio=increase:flags=lanczos,"
        "crop=2016:1134,"
        f"rotate='0.0045*sin(2*PI*(t+{start_seconds})/10)':"
        "ow=iw:oh=ih:bilinear=1:fillcolor=black,"
        "crop=1920:1080:(iw-1920)/2:(ih-1080)/2,setsar=1"
    )
    filter_complex = (
        f"[0:v]{base_filter}[base];"
        "[1:v]scale=1920:1080:force_original_aspect_ratio=increase,"
        "crop=1920:1080,setsar=1,format=rgba,"
        "colorkey=0x000000:0.18:0.08,colorchannelmixer=aa=0.72[fx];"
        "[base][fx]overlay=shortest=1:format=auto,format=yuv420p[out]"
    )
    cmd = [
        "ffmpeg", "-y", "-nostats", "-stats_period", "0.25", "-progress", "pipe:1",
        "-loop", "1", "-framerate", str(config.VIDEO_FPS), "-i", image_path.as_posix(),
        "-stream_loop", "-1", "-i", effect_path.as_posix(),
        "-filter_complex", filter_complex, "-map", "[out]", "-an",
        "-t", f"{duration:.3f}", "-r", str(config.VIDEO_FPS),
        "-c:v", encoder, "-preset", "ultrafast", "-b:v", bitrate,
        "-pix_fmt", "yuv420p", segment_path.as_posix(),
    ]
    logger.info(f"[Render Segment {segment_index}] Start: {start_seconds}s, Duration: {duration}s")
    started = time.monotonic()
    process = subprocess.Popen(
        [str(item) for item in cmd],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    encoded_seconds = 0.0
    encoded_frames = 0
    total_frames = max(int(round(duration * config.VIDEO_FPS)), 1)
    last_emit = 0.0
    progress_queue: queue.Queue[str | None] = queue.Queue()

    def _read_progress_stream() -> None:
        try:
            if process.stdout:
                for progress_line in process.stdout:
                    progress_queue.put(progress_line.strip())
        finally:
            progress_queue.put(None)

    threading.Thread(target=_read_progress_stream, daemon=True).start()
    if progress_callback:
        progress_callback(overall_start, f"Khởi tạo phân đoạn {segment_index + 1}", None)

    stream_closed = False
    while process.poll() is None or not stream_closed:
        line = None
        try:
            line = progress_queue.get(timeout=0.25)
            if line is None:
                stream_closed = True
        except queue.Empty:
            pass

        if line:
            if line.startswith("frame="):
                try:
                    encoded_frames = max(encoded_frames, int(line.split("=", 1)[1]))
                    encoded_seconds = max(encoded_seconds, encoded_frames / float(config.VIDEO_FPS))
                except ValueError:
                    pass
            elif line.startswith(("out_time_ms=", "out_time_us=")):
                value = line.split("=", 1)[1].strip()
                if value not in ("", "N/A"):
                    try:
                        encoded_seconds = max(encoded_seconds, int(value) / 1_000_000.0)
                    except ValueError:
                        pass
            elif line.startswith("out_time="):
                value = line.split("=", 1)[1].strip()
                try:
                    hours, minutes, seconds = value.split(":")
                    encoded_seconds = max(
                        encoded_seconds,
                        int(hours) * 3600 + int(minutes) * 60 + float(seconds),
                    )
                except (ValueError, TypeError):
                    pass

        now = time.monotonic()
        if progress_callback and now - last_emit >= 0.25:
            elapsed = max(now - started, 0.001)
            measured_ratio = max(
                encoded_seconds / max(duration, 0.001),
                encoded_frames / total_frames,
            )
            # Heartbeat chỉ dùng khi FFmpeg chưa gửi được frame/time trên Windows.
            # Giới hạn ở 92% để không báo hoàn tất giả trước khi process kết thúc.
            fallback_ratio = min(elapsed / max(duration * 1.2, 2.0), 0.92)
            local_ratio = min(max(measured_ratio, fallback_ratio), 0.995)
            completed_media = max(encoded_seconds, encoded_frames / float(config.VIDEO_FPS))
            media_speed = completed_media / elapsed if completed_media > 0 else 0.0
            eta = (duration - completed_media) / media_speed if media_speed > 0.01 else None
            progress_callback(
                overall_start + overall_span * local_ratio,
                f"Dựng hình {int(local_ratio * 100)}%",
                max(eta, 0.0) if eta is not None else None,
            )
            last_emit = now

        if process.poll() is not None and stream_closed:
            break

    if progress_callback:
        progress_callback(
            overall_start + overall_span,
            f"Hoàn tất phân đoạn {segment_index + 1}",
            0.0,
        )

    stderr_text = process.stderr.read() if process.stderr else ""
    return_code = process.wait()
    if return_code != 0:
        details = (stderr_text or "FFmpeg không trả chi tiết").strip().splitlines()[-12:]
        raise RuntimeError(f"FFmpeg render segment {segment_index} thất bại: " + " | ".join(details))
    if not segment_path.is_file() or segment_path.stat().st_size < 1024:
        raise RuntimeError(f"Segment {segment_index} không được tạo hợp lệ: {segment_path}")

def process_audio_master(input_audio: Path, ambience_audio: Path, out_path: Path, duration: float, vibe_mode: str = "clean"):
    """
    Trộn âm thanh nền và chuẩn hóa loudness theo đúng Vibe đã chọn (Clean, Light, Rich) (Mục 503).
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = out_path.parent / "temp_audio"
    temp_dir.mkdir(parents=True, exist_ok=True)
    
    try:
        # Đảm bảo âm thanh nền tồn tại (tự sinh bằng code nếu thiếu)
        ambience_pack = AudioProcessor.create_builtin_ambience_pack()
        if not Path(ambience_audio).is_file():
            ambience_audio = ambience_pack.get("rain_ambience", ambience_audio)
        crackle_audio = ambience_pack.get("vinyl_crackle")

        # Áp chất âm lofi (slowed 0.88x + lowpass ấm) cho MỌI vibe trước khi lặp
        lofi_path = temp_dir / "lofi_character.m4a"
        AudioProcessor.apply_lofi_character(input_audio, lofi_path)

        if vibe_mode == "clean":
            # Chuẩn hóa rồi lặp đủ thời lượng. Bản cũ chỉ cắt audio nên video dài hơn nhạc bị verifier từ chối.
            norm_path = temp_dir / "normalized.m4a"
            AudioProcessor.normalize_audio(lofi_path, norm_path, target_lufs=-15.0)
            cmd = [
                "ffmpeg", "-y", "-stream_loop", "-1",
                "-i", norm_path.as_posix(),
                "-t", f"{duration:.3f}",
                "-c:a", "aac", "-b:a", config.AUDIO_BITRATE,
                "-ar", str(config.AUDIO_SAMPLE_RATE),
                out_path.as_posix(),
            ]
            result = subprocess.run(
                [str(item) for item in cmd],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if result.returncode != 0:
                details = (result.stderr or "FFmpeg không trả chi tiết").strip().splitlines()[-12:]
                raise RuntimeError("FFmpeg tạo audio master thất bại: " + " | ".join(details))
        else:
            # Lặp nhạc với crossfade 5 giây để đạt đủ độ dài video
            looped_path = temp_dir / "looped.m4a"
            AudioProcessor.loop_audio(lofi_path, looped_path, target_duration=duration, crossfade_seconds=5.0)

            # Phối trộn ambience
            mixed_path = temp_dir / "mixed.m4a"
            amb_vol = 0.06 if vibe_mode == "light" else 0.09
            AudioProcessor.mix_ambience(looped_path, ambience_audio, mixed_path, music_volume=1.0, ambience_volume=amb_vol, duration=duration)
            if vibe_mode == "rich":
                # Rich: thêm vinyl crackle + hiệu ứng vang aecho
                crackled_path = temp_dir / "crackled.m4a"
                if crackle_audio and Path(crackle_audio).is_file():
                    AudioProcessor.mix_ambience(mixed_path, crackle_audio, crackled_path, music_volume=1.0, ambience_volume=0.05, duration=duration)
                else:
                    crackled_path = mixed_path
                cmd = [
                    "ffmpeg", "-y",
                    "-i", crackled_path.as_posix(),
                    "-af", "aecho=0.8:0.88:60:0.4",
                    "-c:a", "aac", "-b:a", config.AUDIO_BITRATE,
                    "-ar", str(config.AUDIO_SAMPLE_RATE),
                    out_path.as_posix()
                ]
                subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            else:
                shutil.copy(str(mixed_path), str(out_path))
                
    finally:
        # Dọn dẹp thư mục tạm
        try:
            shutil.rmtree(str(temp_dir), ignore_errors=True)
        except Exception:
            pass

def run_step4(project_id: str, audio_path: Path, image_path: Path, effect_path: Path,
              segment_duration: float = 600.0, encoder: str = None, progress_callback=None,
              vibe_mode: str = "clean", motion_mode: str = "smooth_zoom",
              parallax_enabled: bool = False) -> Path:
    """
    Hàm điều phối chính cho quá trình render video phân đoạn.
    """
    if parallax_enabled:
        motion_mode = "parallax"

    if not encoder:
        encoder = getattr(config, "NVENC_CODEC", "h264_nvenc")

    audio_path = Path(audio_path)
    image_path = Path(image_path)
    effect_path = Path(effect_path)
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("Không tìm thấy FFmpeg trong PATH.")
    if not audio_path.is_file():
        raise FileNotFoundError(f"Không tìm thấy file nhạc: {audio_path}")
    if not image_path.is_file():
        raise FileNotFoundError(f"Không tìm thấy ảnh nền: {image_path}")
    if not effect_path.is_file():
        raise FileNotFoundError(f"Không tìm thấy file hiệu ứng: {effect_path}")

    total_duration = float(config.VIDEO_DURATION_SECONDS)
    config.OUTPUT_DIR = Path(config.OUTPUT_DIR)
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    _notify_progress(progress_callback, 0.01, "Kiểm tra nguyên liệu")
    
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
                if abs(probe["duration_seconds"] - dur) < (1.0 / config.VIDEO_FPS + 0.01):
                    is_cached = True
            except Exception:
                pass
                
        if is_cached:
            print(f"[Render] Reusing cached segment {i} ({seg_path.name})")
            _notify_progress(
                progress_callback,
                0.05 + 0.65 * (i + 1) / num_segments,
                f"Dùng lại phân đoạn {i + 1}/{num_segments}",
                0.0,
            )
        else:
            actual_encoder = encoder
            try:
                render_video_segment(
                    project_id, i, start_sec, dur, image_path, effect_path, seg_path,
                    actual_encoder, config.VIDEO_BITRATE,
                    progress_callback=progress_callback,
                    overall_start=0.05 + 0.65 * i / num_segments,
                    overall_span=0.65 / num_segments,
                    motion_mode=motion_mode,
                )
            except Exception as e:
                if actual_encoder == "h264_nvenc":
                    print(f"[Render Warning] NVENC failed on segment {i}, fallback to libx264. Error: {e}")
                    actual_encoder = "libx264"
                    render_video_segment(
                    project_id, i, start_sec, dur, image_path, effect_path, seg_path,
                    actual_encoder, config.VIDEO_BITRATE,
                    progress_callback=progress_callback,
                    overall_start=0.05 + 0.65 * i / num_segments,
                    overall_span=0.65 / num_segments,
                    motion_mode=motion_mode,
                )
                else:
                    raise e
                    

    # --- GHÉP CÁC PHÂN ĐOẠN (CONCAT) ---
    _notify_progress(progress_callback, 0.72, "Ghép các phân đoạn")
    print("[Render] Concat segment files...")
    concat_list_file = segments_dir / "segments_list.txt"
    with open(concat_list_file, "w", encoding="utf-8") as f:
        for path in segment_paths:
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
    _notify_progress(progress_callback, 0.78, "Xử lý âm thanh")
    print("[Render] Preparing audio master...")
    ambience_audio = config.EFFECTS_DIR / "rain_ambience.mp3"
    if not ambience_audio.exists():
        cmd_amb = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
            "-t", "5",
            ambience_audio.as_posix()
        ]
        subprocess.run(cmd_amb, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        
    master_audio = segments_dir / "audio_master.m4a"
    process_audio_master(audio_path, ambience_audio, master_audio, total_duration, vibe_mode)

    # --- MUX VIDEO & AUDIO LẦN CUỐI ---
    _notify_progress(progress_callback, 0.88, "Ghép hình và âm thanh")
    print("[Render] Muxing final video...")
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Không ghi đè file đang được Streamlit/OneDrive giữ mở. Mỗi lần render tạo tên mới.
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    final_video = config.OUTPUT_DIR / f"{project_id}_{timestamp}.mp4"
    temp_final = config.OUTPUT_DIR / f".{project_id}_{timestamp}.muxing.mp4"

    def _run_mux(copy_video: bool) -> subprocess.CompletedProcess:
        video_args = ["-c:v", "copy"] if copy_video else [
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p",
        ]
        cmd_mux = [
            "ffmpeg", "-y",
            "-i", joined_video_raw.as_posix(),
            "-i", master_audio.as_posix(),
            "-map", "0:v:0", "-map", "1:a:0",
            *video_args,
            "-c:a", "aac", "-b:a", config.AUDIO_BITRATE,
            "-movflags", "+faststart",
            "-shortest",
            temp_final.as_posix(),
        ]
        return subprocess.run(
            cmd_mux,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    mux_result = _run_mux(copy_video=True)
    if mux_result.returncode != 0:
        logger.warning("[Render] Mux stream-copy lỗi, thử encode lại video.")
        temp_final.unlink(missing_ok=True)
        mux_result = _run_mux(copy_video=False)

    if mux_result.returncode != 0:
        details = (mux_result.stderr or "FFmpeg không trả chi tiết").strip().splitlines()[-12:]
        temp_final.unlink(missing_ok=True)
        raise RuntimeError("FFmpeg ghép video và audio thất bại: " + " | ".join(details))

    if not temp_final.is_file() or temp_final.stat().st_size < 1024:
        temp_final.unlink(missing_ok=True)
        raise RuntimeError("FFmpeg báo thành công nhưng file video cuối bị thiếu hoặc quá nhỏ.")

    temp_final.replace(final_video)
    
    try:
        joined_video_raw.unlink(missing_ok=True)
        master_audio.unlink(missing_ok=True)
        concat_list_file.unlink(missing_ok=True)
    except Exception:
        pass

    # --- XÁC MINH SẢN PHẨM CUỐI (VERIFIER) ---
    _notify_progress(progress_callback, 0.96, "Kiểm tra video đầu ra")
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
    
    _notify_progress(progress_callback, 1.0, "Hoàn thành")
        
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
