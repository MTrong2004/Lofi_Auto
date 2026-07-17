"""
AI FILE NOTE - STEP 4: SEGMENTED VIDEO RENDERER

Chức năng chính:
- Render video Lo-Fi theo từng segment để giảm rủi ro lỗi với video dài.
- Tạo preview ảnh chuyển động kết hợp video hiệu ứng.
- PROFILE MÀU: preview, segment và video cuối cùng dùng BT.709 TV range để gần Live View.
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
- detect_best_encoder()
- check_disk_space(), parse_bitrate_to_bps()

Phụ thuộc quan trọng:
- FFmpeg/ffprobe, config, MediaProbe, OutputVerifier, ProjectManager, CacheManager, AudioProcessor
  và core/effect_compositor (filter builder dùng chung cho preview lẫn render cuối).

Lưu ý khi sửa:
- Giữ chữ ký build_effect_preview() và run_step4() vì review_app.py gọi trực tiếp;
  effect_settings là tham số tùy chọn, mặc định giữ hành vi cũ (overlay nền đen).
- Chuỗi filter overlay phải đi qua core/effect_compositor.build_filter_complex();
  không viết chuỗi filter riêng để preview và render cuối không lệch nhau.
- Encoder: None/"auto" sẽ gọi detect_best_encoder() (test encode NVENC thật, cache theo tiến trình).
- Không bỏ callback tiến độ hoặc thay thang percent 0.0-1.0 nếu chưa sửa UI.
- Không thay đổi quy trình segment/concat, fencing token hoặc manifest nếu chưa kiểm thử video dài.
- LOOP-REUSE (r5.6): khi KHÔNG có chữ động/lyrics và segment_duration là bội số 10s,
  mọi segment đủ dài giống hệt nhau từng frame -> chỉ render segment 0 rồi lặp lại file đó
  trong concat list. Nếu thêm filter phụ thuộc thời gian tuyệt đối (theo start_seconds,
  ngoài rotate chu kỳ 10s), PHẢI tắt hoặc siết điều kiện loop_reuse trong run_step4().
- r5.7: concat + mux gộp thành 1 lệnh FFmpeg (concat demuxer làm input 0, không còn
  joined_raw.mp4 trung gian); preset thích ứng (ít segment -> medium/p5, nhiều -> veryfast/p4);
  số worker song song theo encoder (libx264: 2 để tránh oversubscribe CPU, NVENC: tối đa 3 session).
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
from core.runtime.db import get_db_connection
from core.media.probe import MediaProbe
from core.media.output_verifier import OutputVerifier
from core.runtime.project_manager import ProjectManager
from core.runtime.cache_manager import CacheManager
from core.effects.compositor import build_filter_complex, normalize_effect_settings

logger = logging.getLogger("lofi_automation")
RENDERER_VERSION = "2026.07.18-r5.7-mux-preset"


def _bt709_output_args() -> list[str]:
    """Ép metadata màu thống nhất trên preview, segment và file cuối.

    v5.5: dùng full-range (pc) thay vì tv-range để bản render KHỚP màu với live preview
    (trình duyệt hiển thị full-range sRGB). Trước đây tv-range làm render tối/xỉn hơn preview.
    """
    return [
        "-colorspace", "bt709",
        "-color_primaries", "bt709",
        "-color_trc", "bt709",
        "-color_range", "pc",
    ]

# Kết quả dò NVENC được cache theo tiến trình để không test lại mỗi segment.
_ENCODER_CACHE: dict[str, str] = {}


def detect_best_encoder(force_refresh: bool = False) -> str:
    """
    Dò GPU bằng một lần encode thử NVENC thật (không tin danh sách -encoders,
    vì FFmpeg build kèm NVENC cả trên máy không có GPU NVIDIA).
    Máy có GPU trả về h264_nvenc, không thì libx264.
    """
    if not force_refresh and "encoder" in _ENCODER_CACHE:
        return _ENCODER_CACHE["encoder"]
    encoder = "libx264"
    if shutil.which("ffmpeg") is not None:
        preferred = getattr(config, "NVENC_CODEC", "h264_nvenc")
        try:
            probe = subprocess.run(
                [
                    "ffmpeg", "-v", "error", "-f", "lavfi",
                    "-i", "color=c=black:s=256x144:d=0.3:r=24",
                    "-frames:v", "3", "-c:v", preferred, "-f", "null", "-",
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
            )
            if probe.returncode == 0:
                encoder = preferred
        except Exception:
            pass
    _ENCODER_CACHE["encoder"] = encoder
    logger.info(f"[Render] Encoder tự chọn: {encoder} ({'GPU NVENC' if encoder != 'libx264' else 'CPU libx264'})")
    return encoder

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
    logger.info(f"[Render Precheck] Free space: {free / (1024**3):.2f} GB, Estimated space needed: {estimated_bytes / (1024**3):.2f} GB")
    
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


def _prepare_lyrics_filter(
    project_id: str | None,
    work_dir: Path,
    ass_name: str,
    *,
    segment_start: float = 0.0,
    segment_duration: float = 10.0,
) -> str | None:
    """
    Tách phân đoạn lời bài hát tương ứng với segment, dịch thời gian và xuất file .ass tạm thời.
    Trả về chuỗi filter 'subtitles=...' hoặc None.
    """
    if not project_id:
        return None
    try:
        from core.text.ass_renderer import generate_ass_file, get_subtitle_manifest_path, load_subtitle_manifest
        
        manifest_path = get_subtitle_manifest_path(project_id)
        if not manifest_path.is_file():
            return None
            
        m = load_subtitle_manifest(project_id)
        if not m.get("enabled") or not m.get("lyrics"):
            return None
            
        shifted_lyrics = []
        for item in m["lyrics"]:
            s = float(item["start"]) - segment_start
            e = float(item["end"]) - segment_start
            
            # Kiểm tra xem câu này có nằm trong phân đoạn segment hiện tại không
            if e > 0.01 and s < segment_duration - 0.01:
                shifted_item = dict(item)
                # Dịch chuyển mốc thời gian
                shifted_item["start"] = max(0.0, s)
                shifted_item["end"] = min(segment_duration, e)
                
                # Dịch chuyển mốc thời gian của từng từ (nếu có)
                if item.get("words"):
                    shifted_words = []
                    for w in item["words"]:
                        w_s = float(w["start"]) - segment_start
                        w_e = float(w["end"]) - segment_start
                        shifted_words.append({
                            "word": w["word"],
                            "start": max(0.0, w_s),
                            "end": min(segment_duration, w_e)
                        })
                    shifted_item["words"] = shifted_words
                shifted_lyrics.append(shifted_item)
                
        if not shifted_lyrics:
            return None
            
        # Xuất file ASS tạm thời trong thư mục làm việc của segment
        ass_path = work_dir / ass_name
        generate_ass_file(shifted_lyrics, m["style"], ass_path)
        
        return f"subtitles={ass_name}"
    except Exception as exc:
        logger.warning(f"[SubtitleRender] Không thể chuẩn bị phụ đề karaoke: {exc}")
        return None


def _prepare_text_filter(
    text_profile: dict | None,
    work_dir: Path,
    ass_name: str,
    *,
    width: int,
    height: int,
    total_duration: float,
    segment_start: float = 0.0,
    segment_duration: float | None = None,
) -> str | None:
    """
    Sinh file .ass trong work_dir và trả về chuỗi filter 'subtitles=...' (tham chiếu basename,
    dùng chung builder với render cuối). Trả None nếu chữ tắt/không có nội dung/không giao segment.
    FFmpeg phải chạy với cwd=work_dir để basename hợp lệ, tránh escaping path trên Windows.
    """
    if not text_profile or not text_profile.get("enabled"):
        return None
    if not str(text_profile.get("content") or "").strip():
        return None
    try:
        import shutil as _shutil
        from core.text.effect_renderer import build_ass_file
        from core.text.provider import resolve_font

        font = resolve_font(text_profile.get("font_style", "sans"), text_profile.get("content", ""))
        ass_path = build_ass_file(
            text_profile,
            work_dir / ass_name,
            width=width, height=height, total_duration=total_duration,
            font_family=font["family"],
            segment_start=segment_start, segment_duration=segment_duration,
        )
        if ass_path is None:
            return None
        text_filter = f"subtitles={ass_name}"
        # Font bundle (data/fonts): copy vào work_dir và trỏ fontsdir='.' để libass thấy.
        if font.get("bundled_path"):
            try:
                _shutil.copy2(font["bundled_path"], work_dir / Path(font["bundled_path"]).name)
                text_filter += ":fontsdir=."
            except OSError:
                pass
        return text_filter
    except Exception as exc:
        logger.warning(f"[TextEffect] Bỏ qua chữ động do lỗi chuẩn bị ASS: {exc}")
        return None


def build_effect_preview(
    background_image: Path,
    effect_video: Path,
    out_path: Path,
    duration: float = 10.0,
    motion_mode: str = "smooth_zoom",
    effect_settings: dict | None = None,
    text_profile: dict | None = None,
    project_id: str | None = None,
) -> Path:
    """Tạo preview 10 giây dùng CHUNG filter builder với render cuối (effect_compositor)."""
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

    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Chữ động dùng chung builder; ASS đặt cạnh file preview, ffmpeg chạy với cwd=out_path.parent.
    text_filter = _prepare_text_filter(
        text_profile, out_path.parent, f"{out_path.stem}.ass",
        width=1280, height=720, total_duration=float(duration),
        segment_start=0.0, segment_duration=float(duration),
    )
    
    # Phụ đề Karaoke chạy chữ (nếu được bật và đã duyệt)
    lyrics_filter = _prepare_lyrics_filter(
        project_id, out_path.parent, f"{out_path.stem}_lyrics.ass",
        segment_start=0.0, segment_duration=float(duration),
    )
    
    filters = []
    if text_filter:
        filters.append(text_filter)
    if lyrics_filter:
        filters.append(lyrics_filter)
        
    combined_filter = ",".join(filters) if filters else None
    
    effect_guard = (
        f"[1:v]setpts=PTS-STARTPTS,"
        f"tpad=stop_mode=clone:stop_duration={float(duration):.3f}[effect_guard];"
    )
    filter_complex = effect_guard + build_filter_complex(
        base_filter,
        effect_settings,
        width=1280,
        height=720,
        fps=config.VIDEO_FPS,
        effect_input="effect_guard",
        text_filter=combined_filter,
    )

    def _command(encoder: str, preset: str) -> list[str]:
        return [
            "ffmpeg", "-y",
            "-loop", "1", "-framerate", str(config.VIDEO_FPS), "-i", str(background_image),
            "-stream_loop", "-1", "-i", str(effect_video),
            "-filter_complex", filter_complex,
            "-map", "[out]", "-an", "-t", f"{float(duration):.3f}",
            "-c:v", encoder, "-preset", preset,
            "-profile:v", "main", "-level", "4.0",
            "-pix_fmt", "yuv420p", *_bt709_output_args(),
            "-movflags", "+faststart", str(out_path),
        ]

    best = detect_best_encoder()
    attempts = [("h264_nvenc", "p1"), ("libx264", "veryfast")] if best == "h264_nvenc" else [("libx264", "veryfast")]
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
            cwd=str(out_path.parent),
        )
        if result.returncode == 0 and out_path.is_file() and out_path.stat().st_size >= 1024:
            return out_path
        errors.extend((result.stderr or "").strip().splitlines()[-8:])

    out_path.unlink(missing_ok=True)
    raise RuntimeError("FFmpeg tạo preview thất bại: " + " | ".join(errors[-12:]))


from core.media.audio_processor import AudioProcessor

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
    effect_settings: dict | None = None,
    text_profile: dict | None = None,
    total_duration: float | None = None,
    high_quality: bool = False,
):
    """Render segment và đọc tiến độ thật từ FFmpeg qua -progress pipe:1.

    high_quality=True dùng khi tổng số segment phải encode ít (loop-reuse):
    preset chậm hơn cho chất lượng tốt hơn cùng bitrate, tổng thời gian vẫn nhỏ.
    """
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
    # Chữ động: ASS theo từng segment (dịch mốc thời gian theo start_seconds); ffmpeg chạy cwd=segments_dir.
    text_filter = _prepare_text_filter(
        text_profile, segment_path.parent, f"{segment_path.stem}.ass",
        width=1920, height=1080,
        total_duration=float(total_duration if total_duration is not None else duration),
        segment_start=float(start_seconds), segment_duration=float(duration),
    )
    
    # Phụ đề Karaoke chạy chữ theo segment
    lyrics_filter = _prepare_lyrics_filter(
        project_id, segment_path.parent, f"{segment_path.stem}_lyrics.ass",
        segment_start=float(start_seconds), segment_duration=float(duration),
    )
    
    filters = []
    if text_filter:
        filters.append(text_filter)
    if lyrics_filter:
        filters.append(lyrics_filter)
        
    combined_filter = ",".join(filters) if filters else None
    
    effect_guard = (
        f"[1:v]setpts=PTS-STARTPTS,"
        f"tpad=stop_mode=clone:stop_duration={float(duration):.3f}[effect_guard];"
    )
    filter_complex = effect_guard + build_filter_complex(
        base_filter,
        effect_settings,
        width=1920,
        height=1080,
        fps=config.VIDEO_FPS,
        effect_input="effect_guard",
        text_filter=combined_filter,
    )
    # Preset thích ứng: ít segment phải encode (loop-reuse) -> preset chậm, nén tốt hơn
    # cùng bitrate; nhiều segment (có chữ/lyrics) -> ưu tiên tốc độ nhưng vẫn bỏ ultrafast
    # vì ultrafast phí bitrate rõ rệt ở 2800k/1080p.
    # NVENC: encode hầu như không phải nút thắt (filter chạy CPU) nên tối thiểu p4;
    # p1 chỉ nhanh hơn không đáng kể mà nén kém rõ.
    if high_quality:
        preset = "p5" if encoder == "h264_nvenc" else "medium"
    else:
        preset = "p4" if encoder == "h264_nvenc" else "veryfast"
    cmd = [
        "ffmpeg", "-y", "-nostats", "-stats_period", "0.25", "-progress", "pipe:1",
        "-loop", "1", "-framerate", str(config.VIDEO_FPS), "-i", image_path.as_posix(),
        "-stream_loop", "-1", "-i", effect_path.as_posix(),
        "-filter_complex", filter_complex, "-map", "[out]", "-an",
        "-t", f"{duration:.3f}", "-r", str(config.VIDEO_FPS),
        "-c:v", encoder, "-preset", preset, "-b:v", bitrate,
        "-pix_fmt", "yuv420p", *_bt709_output_args(), segment_path.as_posix(),
    ]
    logger.info(f"[Render Segment {segment_index}] Start: {start_seconds}s, Duration: {duration}s")
    started = time.monotonic()
    process = subprocess.Popen(
        [str(item) for item in cmd],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(segment_path.parent),
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
    stderr_lines = []

    def _read_progress_stream() -> None:
        try:
            if process.stdout:
                for progress_line in process.stdout:
                    progress_queue.put(progress_line.strip())
        finally:
            progress_queue.put(None)

    def _read_stderr_stream() -> None:
        try:
            if process.stderr:
                for err_line in process.stderr:
                    stderr_lines.append(err_line)
        except Exception:
            pass

    threading.Thread(target=_read_progress_stream, daemon=True).start()
    threading.Thread(target=_read_stderr_stream, daemon=True).start()
    if progress_callback:
        progress_callback(overall_start, f"Khởi tạo phân đoạn {segment_index + 1}", None)

    stream_closed = False
    process_done_at: float | None = None  # Thời điểm process kết thúc
    while True:
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

        # Ghi nhận thời điểm process kết thúc để có deadline thoát vòng lặp.
        if process.poll() is not None and process_done_at is None:
            process_done_at = time.monotonic()

        # Điều kiện thoát: stream đã đóng HOẶC process kết thúc rồi chờ quá 3s
        # (trường hợp stdout pipe không gửi EOF do flush encoder cuối cùng trên Windows).
        if stream_closed:
            break
        if process_done_at is not None and (time.monotonic() - process_done_at) > 3.0:
            break

    if progress_callback:
        progress_callback(
            overall_start + overall_span,
            f"Hoàn tất phân đoạn {segment_index + 1}",
            0.0,
        )

    return_code = process.wait()
    stderr_text = "".join(stderr_lines)
    if return_code != 0:
        details = (stderr_text or "FFmpeg không trả chi tiết").strip().splitlines()[-12:]
        raise RuntimeError(f"FFmpeg render segment {segment_index} thất bại: " + " | ".join(details))
    if not segment_path.is_file() or segment_path.stat().st_size < 1024:
        raise RuntimeError(f"Segment {segment_index} không được tạo hợp lệ: {segment_path}")

def process_audio_master(input_audio, ambience_audio: Path, out_path: Path, duration: float,
                         vibe_mode: str = "clean", remix_settings: dict | None = None):
    """
    Trộn âm thanh nền và chuẩn hóa loudness theo đúng Vibe đã chọn (Clean, Light, Rich) (Mục 503).

    input_audio: Path MỘT bài, hoặc LIST nhiều bài (HM5 -> nối crossfade thành audio-master nguồn).
    remix_settings: dict tham số remix (HM4). Nếu có -> dùng apply_remix thay apply_lofi_character.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = out_path.parent / "temp_audio"
    temp_dir.mkdir(parents=True, exist_ok=True)

    try:
        # HM5: nhiều bài -> nối crossfade thành 1 bài nguồn trước khi xử lý.
        if isinstance(input_audio, (list, tuple)):
            valid = [Path(p) for p in input_audio if Path(p).is_file()]
            if not valid:
                raise RuntimeError("Không có bài hợp lệ để dựng audio master.")
            if len(valid) > 1:
                combined = temp_dir / "combined_source.m4a"
                AudioProcessor.concat_tracks(valid, combined, crossfade_seconds=3.0)
                source_audio = combined
            else:
                source_audio = valid[0]
        else:
            source_audio = Path(input_audio)

        # Đảm bảo âm thanh nền tồn tại (tự sinh bằng code nếu thiếu)
        ambience_pack = AudioProcessor.create_builtin_ambience_pack()
        if not Path(ambience_audio).is_file():
            ambience_audio = ambience_pack.get("rain_ambience", ambience_audio)
        crackle_audio = ambience_pack.get("vinyl_crackle")

        # Áp chất âm: HM4 remix tham số hoá nếu có, ngược lại chất âm lofi mặc định (slowed + lowpass ấm).
        lofi_path = temp_dir / "lofi_character.m4a"
        if remix_settings:
            AudioProcessor.apply_remix(source_audio, lofi_path, remix_settings)
        else:
            AudioProcessor.apply_lofi_character(source_audio, lofi_path)

        # duration <= 0 (chế độ nhiều bài / natural length): lấy đúng độ dài bản đã xử lý,
        # để bước loop/trim bên dưới không lặp thêm mà chỉ giữ nguyên (source == target).
        if float(duration) <= 0.0:
            try:
                duration = float(MediaProbe.probe_media(lofi_path)["duration_seconds"])
            except Exception:
                duration = float(config.VIDEO_DURATION_SECONDS)

        if vibe_mode == "clean":
            # apply_lofi_character đã gộp dynaudnorm vào filter chain (normalize=True mặc định),
            # nên không cần bước normalize riêng. Dùng trực tiếp lofi_path để stream_loop.
            norm_path = lofi_path

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

            # Xác minh duration audio master thực tế — nếu lệch nhiều so với yêu cầu thì fallback
            # dùng loop_audio (crossfade) để đảm bảo đúng thời lượng trước khi mux.
            try:
                master_probe = MediaProbe.probe_media(out_path)
                master_dur = master_probe["duration_seconds"]
            except Exception:
                master_dur = 0.0
            if abs(master_dur - duration) > 2.0:
                logger.warning(
                    f"[AudioMaster] Audio master stream-loop không đạt thời lượng yêu cầu "
                    f"({master_dur:.2f}s vs {duration:.2f}s). Fallback sang loop_audio crossfade."
                )
                looped_fb = temp_dir / "looped_fallback.m4a"
                AudioProcessor.loop_audio(lofi_path, looped_fb, target_duration=duration, crossfade_seconds=5.0)
                import shutil as _shutil
                _shutil.copy(str(looped_fb), str(out_path))
        else:
            # Lặp nhạc với crossfade 5 giây để đạt đủ độ dài video
            looped_path = temp_dir / "looped.m4a"
            AudioProcessor.loop_audio(lofi_path, looped_path, target_duration=duration, crossfade_seconds=5.0)

            if vibe_mode == "rich":
                # Rich: Phối hợp nhạc + rain + vinyl crackle + reverb trong DUY NHẤT 1 lần chạy FFmpeg
                cmd = [
                    "ffmpeg", "-y",
                    "-i", looped_path.as_posix(),
                ]
                inputs_count = 1
                filter_parts = ["[0:a]aecho=0.8:0.88:60:0.4[music]"]
                amix_inputs = ["[music]"]
                
                if Path(ambience_audio).is_file():
                    cmd.extend(["-stream_loop", "-1", "-i", ambience_audio.as_posix()])
                    filter_parts.append(f"[{inputs_count}:a]volume=0.09[rain]")
                    amix_inputs.append("[rain]")
                    inputs_count += 1
                    
                if crackle_audio and Path(crackle_audio).is_file():
                    cmd.extend(["-stream_loop", "-1", "-i", crackle_audio.as_posix()])
                    filter_parts.append(f"[{inputs_count}:a]volume=0.05[crackle]")
                    amix_inputs.append("[crackle]")
                    inputs_count += 1
                    
                amix_str = "".join(amix_inputs)
                filter_parts.append(f"{amix_str}amix=inputs={inputs_count}:duration=first:dropout_transition=2[out]")
                filter_complex = ";".join(filter_parts)
                
                cmd.extend([
                    "-filter_complex", filter_complex,
                    "-map", "[out]",
                    "-t", f"{duration:.3f}",
                    "-c:a", "aac", "-b:a", config.AUDIO_BITRATE,
                    "-ar", str(config.AUDIO_SAMPLE_RATE),
                    out_path.as_posix()
                ])
                _run_ffmpeg(cmd, "FFmpeg phối âm và thêm reverb (rich) thất bại")
            else:
                # Light hoặc Clean: Chỉ cần phối trộn nhạc và rain bằng 1 lệnh
                mixed_path = temp_dir / "mixed.m4a"
                AudioProcessor.mix_ambience(looped_path, ambience_audio, mixed_path, music_volume=1.0, ambience_volume=0.06, duration=duration)
                shutil.copy(str(mixed_path), str(out_path))
                
    finally:
        # Dọn dẹp thư mục tạm
        try:
            shutil.rmtree(str(temp_dir), ignore_errors=True)
        except Exception:
            pass

def run_step4(project_id: str, audio_path: Path, image_path: Path, effect_path: Path,
              segment_duration: float = 60.0, encoder: str = None, progress_callback=None,
              vibe_mode: str = "clean", motion_mode: str = "smooth_zoom",
              parallax_enabled: bool = False, effect_settings: dict | None = None,
              text_profile: dict | None = None, remix_settings: dict | None = None,
              audio_paths: list | None = None) -> Path:
    """
    Hàm điều phối chính cho quá trình render video phân đoạn.
    encoder=None hoặc "auto": tự dò GPU NVENC, không có thì dùng CPU libx264.
    effect_settings: thông số compositing thống nhất (xem core/effect_compositor.py).
    remix_settings: dict tham số remix âm thanh (HM4); None -> chất âm lofi mặc định.
    audio_paths: LIST nhiều bài (HM5) -> nối thành audio-master dài, thời lượng video theo tổng.
                 Nếu None/1 bài -> dùng audio_path như cũ (thời lượng theo config).
    """
    if parallax_enabled:
        motion_mode = "parallax"

    if not encoder or str(encoder).lower() == "auto":
        encoder = detect_best_encoder()

    if effect_settings is None:
        # Caller không truyền thông số: dùng kết quả phân tích loại nền trong manifest
        # (main.py/app_server.py hưởng tự động; review_app luôn truyền tường minh).
        try:
            from core.effects.manifest import get_effect_metadata
            meta = get_effect_metadata(config.EFFECTS_DIR, effect_path)
            recommended = meta.get("recommended_composite") or {}
            if meta.get("effect_type"):
                effect_settings = {
                    "effect_type": meta["effect_type"],
                    **{key: value for key, value in recommended.items() if value is not None},
                }
        except Exception:
            effect_settings = None
    effect_settings = normalize_effect_settings(effect_settings)

    image_path = Path(image_path)
    effect_path = Path(effect_path)
    # HM5: gom danh sách bài. audio_paths ưu tiên; nếu không có thì dùng audio_path đơn.
    track_list = [Path(p) for p in (audio_paths or []) if Path(p).is_file()]
    is_multi_track = len(track_list) > 1
    if not track_list:
        track_list = [Path(audio_path)]
    audio_path = track_list[0]
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("Không tìm thấy FFmpeg trong PATH.")
    if not audio_path.is_file():
        raise FileNotFoundError(f"Không tìm thấy file nhạc: {audio_path}")
    if not image_path.is_file():
        raise FileNotFoundError(f"Không tìm thấy ảnh nền: {image_path}")
    if not effect_path.is_file():
        raise FileNotFoundError(f"Không tìm thấy file hiệu ứng: {effect_path}")

    config.OUTPUT_DIR = Path(config.OUTPUT_DIR)
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    project_dir = ProjectManager.get_project_dir(project_id)
    segments_dir = project_dir / "segments"
    segments_dir.mkdir(parents=True, exist_ok=True)
    master_audio = segments_dir / "audio_master.m4a"

    _notify_progress(progress_callback, 0.01, "Kiểm tra nguyên liệu")

    # Thời lượng video: mặc định theo config; NHIỀU BÀI -> dựng audio-master trước để lấy độ dài
    # THẬT (tổng các bài, đã tính chất âm/tempo), rồi cắt video khớp đúng độ dài đó.
    total_duration = float(config.VIDEO_DURATION_SECONDS)
    prebuilt_master = False
    if is_multi_track:
        _notify_progress(progress_callback, 0.02, f"Nối {len(track_list)} bài thành audio dài")
        ambience_pre = config.EFFECTS_DIR / "rain_ambience.mp3"
        process_audio_master(track_list, ambience_pre, master_audio, 0.0,
                             vibe_mode, remix_settings=remix_settings)
        try:
            total_duration = float(MediaProbe.probe_media(master_audio)["duration_seconds"])
        except Exception:
            total_duration = float(config.VIDEO_DURATION_SECONDS)
        prebuilt_master = master_audio.is_file() and total_duration > 1.0
        logger.info(f"[Render] Chế độ mix {len(track_list)} bài -> video dài {total_duration:.1f}s")

    # 1. Kiểm tra dung lượng ổ đĩa (theo thời lượng thực tế)
    if not check_disk_space(config.OUTPUT_DIR, total_duration, config.VIDEO_BITRATE, config.AUDIO_BITRATE):
        raise ValueError("Ổ đĩa không đủ dung lượng để tiến hành render (Mục 10).")

    # Chữ ký thông số compositing: đưa vào TÊN segment để đổi hiệu ứng/chữ/chuyển động thì
    # segment cache cũ (theo bộ thông số khác) không bị tái dùng nhầm.
    # Phải bao gồm ĐỦ những gì preview_key của review_app bao gồm (ảnh nền, file hiệu ứng,
    # mtime lyrics) để preview và render cuối vô hiệu cache đồng bộ với nhau — trước đây
    # thiếu 3 thành phần này nên đổi ảnh/hiệu ứng/lyrics vẫn tái dùng segment cũ.
    lyrics_mtime = 0.0
    try:
        from core.text.ass_renderer import get_subtitle_manifest_path
        _sub_path = get_subtitle_manifest_path(project_id)
        if _sub_path.is_file():
            lyrics_mtime = _sub_path.stat().st_mtime
    except Exception:
        pass
    asset_signature = (
        f"{image_path.resolve()}|{image_path.stat().st_mtime}|"
        f"{effect_path.resolve()}|{effect_path.stat().st_mtime}|"
        f"lyrics={lyrics_mtime}|{RENDERER_VERSION}"
    )
    try:
        from core.effects.compositor import effect_settings_cache_key
        from core.text.provider import text_profile_cache_key
        signature_source = (
            f"{effect_settings_cache_key(effect_settings)}|{motion_mode}|"
            f"{text_profile_cache_key(text_profile)}|{asset_signature}"
        )
    except Exception:
        signature_source = f"{motion_mode}|{asset_signature}"
    composite_sig = hashlib.sha256(signature_source.encode("utf-8")).hexdigest()[:10]
    # Dọn segment cũ khác chữ ký để không tích rác (không đụng segment đúng chữ ký).
    for stale in segments_dir.glob("segment_*.mp4"):
        if f"_{composite_sig}" not in stale.name:
            stale.unlink(missing_ok=True)

    # Chia phân đoạn
    num_segments = int(total_duration // segment_duration)
    if total_duration % segment_duration > 0:
        num_segments += 1

    # --- TỐI ƯU LOOP-REUSE: nội dung video tuần hoàn -> chỉ render 1 segment đầy đủ ---
    # Ảnh nền tĩnh, rotate có chu kỳ đúng 10s và effect video được reset (setpts=PTS-STARTPTS
    # + stream_loop) ở đầu MỖI segment, nên mọi segment đủ dài đều giống hệt nhau từng frame,
    # trừ khi có chữ động/lyrics (ASS dịch mốc thời gian theo segment_start).
    # Khi đủ điều kiện: render segment 0 một lần rồi liệt kê lặp lại trong concat list
    # (concat demuxer chấp nhận cùng một file nhiều lần với -c copy) -> video 1 giờ chỉ tốn
    # công encode ~1 segment thay vì 60, cực quan trọng trên máy render CPU.
    loop_reuse = False
    if num_segments > 1 and abs(segment_duration % 10.0) < 1e-6:
        text_on = bool(
            text_profile and text_profile.get("enabled")
            and str(text_profile.get("content") or "").strip()
        )
        lyrics_on = False
        try:
            from core.text.ass_renderer import get_subtitle_manifest_path, load_subtitle_manifest
            if get_subtitle_manifest_path(project_id).is_file():
                _lyrics_manifest = load_subtitle_manifest(project_id)
                lyrics_on = bool(_lyrics_manifest.get("enabled") and _lyrics_manifest.get("lyrics"))
        except Exception:
            lyrics_on = True  # không xác định được -> an toàn: không reuse
        loop_reuse = not text_on and not lyrics_on
    if loop_reuse:
        logger.info(
            f"[Render] Nội dung tuần hoàn (không chữ/lyrics): render 1 segment "
            f"{segment_duration:.0f}s và tái dùng cho {num_segments} phân đoạn."
        )

    # Ít segment phải encode -> dùng preset chất lượng cao (thời gian tổng vẫn nhỏ).
    high_quality_render = loop_reuse or num_segments <= 2

    segment_paths = []

    # --- RENDER TỪNG PHÂN ĐOẠN ---
    from concurrent.futures import ThreadPoolExecutor
    progress_lock = threading.Lock()
    progress_tracker = {}

    for i in range(num_segments):
        start_sec = i * segment_duration
        dur = min(segment_duration, total_duration - start_sec)
        # Segment đủ dài tái dùng file của segment 0 (nội dung từng frame giống hệt);
        # segment cuối ngắn hơn vẫn render riêng.
        if loop_reuse and i > 0 and abs(dur - segment_duration) < 1e-6:
            segment_paths.append(segment_paths[0])
            continue
        seg_path = segments_dir / f"segment_{i}_{composite_sig}.mp4"
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
            progress_tracker[i] = 1.0
            logger.info(f"[Render] Reusing cached segment {i} ({seg_path.name})")
        else:
            progress_tracker[i] = 0.0

    # Khởi chạy cập nhật tiến độ ban đầu. Chia theo số segment THỰC SỰ phải render
    # (loop-reuse khiến progress_tracker chỉ chứa các segment duy nhất).
    unique_segments = max(len(progress_tracker), 1)
    render_start_time = time.monotonic()
    with progress_lock:
        completed_cached = sum(1.0 for v in progress_tracker.values() if v == 1.0)
        _notify_progress(
            progress_callback,
            0.05 + 0.65 * (completed_cached / unique_segments),
            f"Khởi chạy phân đoạn (Đã cache: {int(completed_cached)}/{unique_segments})",
            0.0,
        )

    def render_one_segment(idx: int):
        if progress_tracker[idx] == 1.0:
            return
            
        start_sec = idx * segment_duration
        dur = min(segment_duration, total_duration - start_sec)
        seg_path = segment_paths[idx]
        
        def local_callback(local_ratio, status_text, eta):
            with progress_lock:
                progress_tracker[idx] = local_ratio
                total_progress = sum(progress_tracker.values()) / unique_segments
                
                elapsed = time.monotonic() - render_start_time
                if total_progress > 0.005:
                    overall_eta = elapsed * (1.0 - total_progress) / total_progress
                else:
                    overall_eta = None
                    
                _notify_progress(
                    progress_callback,
                    0.05 + 0.65 * total_progress,
                    f"Dựng hình {int(total_progress * 100)}%",
                    overall_eta,
                )

        actual_encoder = encoder
        try:
            render_video_segment(
                project_id, idx, start_sec, dur, image_path, effect_path, seg_path,
                actual_encoder, config.VIDEO_BITRATE,
                progress_callback=local_callback,
                overall_start=0.0,
                overall_span=1.0,
                motion_mode=motion_mode,
                effect_settings=effect_settings,
                text_profile=text_profile,
                total_duration=total_duration,
                high_quality=high_quality_render,
            )
        except Exception as e:
            if actual_encoder == "h264_nvenc":
                logger.warning(f"[Render Warning] NVENC failed on segment {idx}, fallback to libx264. Error: {e}")
                render_video_segment(
                    project_id, idx, start_sec, dur, image_path, effect_path, seg_path,
                    "libx264", config.VIDEO_BITRATE,
                    progress_callback=local_callback,
                    overall_start=0.0,
                    overall_span=1.0,
                    motion_mode=motion_mode,
                    effect_settings=effect_settings,
                    text_profile=text_profile,
                    total_duration=total_duration,
                    high_quality=high_quality_render,
                )
            else:
                raise e

    # Chạy song song các phân đoạn chưa có cache.
    # libx264 mỗi tiến trình đã dùng toàn bộ core CPU -> chạy 4 ffmpeg cùng lúc chỉ
    # oversubscribe (chậm hơn + tốn RAM); giữ 2 worker để filter/encode gối đầu nhau.
    # NVENC giới hạn ~3 session đồng thời trên GPU consumer.
    segments_to_render = [i for i, v in progress_tracker.items() if v == 0.0]
    if segments_to_render:
        if encoder == "h264_nvenc":
            max_workers = min(3, os.cpu_count() or 4)
        else:
            max_workers = 2
        max_workers = min(max_workers, len(segments_to_render))
        logger.info(f"[Render] Song song {len(segments_to_render)} phân đoạn với {max_workers} worker...")
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            list(executor.map(render_one_segment, segments_to_render))
                    

    # --- CHUẨN BỊ CONCAT LIST (ghép thật diễn ra ngay trong bước mux, 1 lệnh FFmpeg) ---
    _notify_progress(progress_callback, 0.72, "Chuẩn bị ghép các phân đoạn")
    concat_list_file = segments_dir / "segments_list.txt"
    with open(concat_list_file, "w", encoding="utf-8") as f:
        for path in segment_paths:
            f.write(f"file '{path.resolve().as_posix()}'\n")

    # --- TRỘN & CHUẨN HÓA AUDIO MASTER ---
    _notify_progress(progress_callback, 0.78, "Xử lý âm thanh")
    logger.info("[Render] Preparing audio master...")
    ambience_audio = config.EFFECTS_DIR / "rain_ambience.mp3"
    if not ambience_audio.exists():
        cmd_amb = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
            "-t", "5",
            ambience_audio.as_posix()
        ]
        _run_ffmpeg(cmd_amb, "FFmpeg tạo ambience mặc định thất bại")
        
    # master_audio đã được khai báo ở đầu; chế độ nhiều bài đã dựng sẵn (prebuilt_master) -> tái dùng.
    if prebuilt_master and master_audio.is_file():
        logger.info("[Render] Tái dùng audio-master đã dựng từ nhiều bài (bỏ qua dựng lại).")
    else:
        process_audio_master(audio_path, ambience_audio, master_audio, total_duration,
                             vibe_mode, remix_settings=remix_settings)

    # --- CONCAT + MUX VIDEO & AUDIO TRONG MỘT LỆNH ---
    # Gộp concat demuxer và mux audio vào 1 lần chạy FFmpeg: tránh ghi file trung gian
    # joined_raw.mp4 (~toàn bộ video ghi ra đĩa thêm 1 lần) trước khi mux.
    _notify_progress(progress_callback, 0.88, "Ghép hình và âm thanh")
    logger.info("[Render] Concat segments + muxing final video...")
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Không ghi đè file đang được Streamlit/OneDrive giữ mở. Mỗi lần render tạo tên mới.
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    final_video = config.OUTPUT_DIR / f"{project_id}_{timestamp}.mp4"
    temp_final = config.OUTPUT_DIR / f".{project_id}_{timestamp}.muxing.mp4"

    def _run_mux(copy_video: bool) -> subprocess.CompletedProcess:
        # Cờ màu BT.709 chỉ có tác dụng khi re-encode; với -c:v copy bitstream giữ
        # nguyên cờ màu đã encode đúng ở từng segment.
        video_args = ["-c:v", "copy"] if copy_video else [
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p", *_bt709_output_args(),
        ]
        cmd_mux = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0", "-i", concat_list_file.as_posix(),
            "-i", master_audio.as_posix(),
            "-map", "0:v:0", "-map", "1:a:0",
            *video_args,
            "-c:a", "aac", "-b:a", config.AUDIO_BITRATE,
            "-movflags", "+faststart",
            # Dùng -t thay -shortest để tránh video bị cắt ngắn khi audio master
            # bị lỗi duration (ví dụ loudnorm output ngắn hơn kỳ vọng).
            "-t", f"{total_duration:.3f}",
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
        master_audio.unlink(missing_ok=True)
        concat_list_file.unlink(missing_ok=True)
    except Exception:
        pass

    # --- XÁC MINH SẢN PHẨM CUỐI (VERIFIER) ---
    _notify_progress(progress_callback, 0.96, "Kiểm tra video đầu ra")
    logger.info("[Render] Verifying output...")
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
    
    import core.runtime.db
    core.runtime.db.init_db()
    
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
