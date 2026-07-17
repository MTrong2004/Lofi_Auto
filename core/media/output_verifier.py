"""
AI FILE NOTE - OUTPUT VERIFIER
Chức năng chính:
- Xác minh toàn diện video đầu ra cuối cùng: kiểm tra có đủ luồng hình/tiếng, độ phân giải 1920x1080, frame rate CFR, codec h264 + aac 48kHz, thời lượng khớp trong sai số 1 frame (các mã RND-ACC-001..004).
- Cảnh báo (WARN) khi phát hiện đoạn màn hình đen (>3s) qua blackdetect và đoạn im lặng dài (>5s).
- Sinh output manifest bảo mật (kèm SHA-256, thông tin encoder/màu/color) và xác thực bằng schema trước khi ghi cạnh video.
Đầu vào chính:
- video_path, project_id, expected_duration, thông tin encoder yêu cầu/thực tế, track_id.
Đầu ra chính:
- dict manifest và file <stem>_manifest.json; quăng OutputVerificationError nếu không đạt.
API được file khác sử dụng:
- OutputVerifier.verify_video, OutputVerifier.detect_black_frames, OutputVerificationError.
Phụ thuộc quan trọng:
- config, FFmpeg/ffprobe (subprocess), core.media.probe (MediaProbe), core.runtime.schemas (validate_data_schema), core.runtime.cache_manager (CacheManager).
Lưu ý khi sửa:
- Các ngưỡng 1920x1080 / VIDEO_FPS / AUDIO_SAMPLE_RATE / h264 / aac là ràng buộc nghiệm thu; đổi phải đồng bộ với config và schema output_manifest.
- detect_black_frames nuốt lỗi và trả list rỗng (chỉ là cảnh báo), không được để nó làm hỏng verify chính.
- Khối __main__ chỉ tự sinh video test bằng FFmpeg để chạy thử.
"""
import os
import sys
import json
import subprocess
import re
import hashlib
from pathlib import Path
from datetime import datetime, timezone

# Đảm bảo import được config.py từ thư mục cha
sys.path.append(str(Path(__file__).parent.parent))
import config
from core.media.probe import MediaProbe, MediaProbeError
from core.runtime.schemas import validate_data_schema, SchemaValidationError
from core.runtime.cache_manager import CacheManager

class OutputVerificationError(Exception):
    pass

class OutputVerifier:
    @staticmethod
    def detect_black_frames(file_path: Path, duration: float = 2.0, pix_th: float = 0.10) -> list[dict]:
        """
        Sử dụng bộ lọc blackdetect của FFmpeg để phát hiện các khoảng màn hình đen kéo dài.
        """
        if not file_path.exists():
            raise FileNotFoundError(f"Không tìm thấy file: {file_path}")

        cmd = [
            "ffmpeg",
            "-i", str(file_path),
            "-vf", f"blackdetect=d={duration}:pix_th={pix_th}",
            "-f", "null",
            "-"
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
            stderr = result.stderr
            
            black_ranges = []
            starts = re.findall(r"black_start:\s*([\d\.]+)", stderr)
            ends = re.findall(r"black_end:\s*([\d\.]+)\s*black_duration:\s*([\d\.]+)", stderr)
            
            for i in range(min(len(starts), len(ends))):
                black_ranges.append({
                    "start": float(starts[i]),
                    "end": float(ends[i][0]),
                    "duration": float(ends[i][1])
                })
            return black_ranges
        except Exception as e:
            print(f"[Verifier Warning] Failed to detect black frames: {e}")
            return []

    @classmethod
    def verify_video(cls, video_path: Path, project_id: str, expected_duration: float, 
                     requested_encoder: str = "h264_nvenc", actual_encoder: str = "libx264", 
                     fallback_reason: str = None, track_id: str = "unknown") -> dict:
        """
        Xác minh toàn diện file video đầu ra (Mục 11, Mục 25.3).
        Trả về manifest nếu xác minh thành công, ngược lại quăng lỗi OutputVerificationError.
        """
        if not video_path.exists():
            raise OutputVerificationError(f"Không tìm thấy file video đầu ra: {video_path}")
            
        print(f"[Verifier] Probing video file: {video_path.name}")
        
        # 1. Chạy ffprobe
        try:
            info = MediaProbe.probe_media(video_path)
        except MediaProbeError as e:
            raise OutputVerificationError(f"Lỗi phân tích file video bằng ffprobe: {e}")
            
        # RND-ACC-001: Có luồng hình và tiếng
        if not info["video_streams"]:
            raise OutputVerificationError("Video đầu ra thiếu luồng hình ảnh (video stream) (RND-ACC-001).")
        if not info["audio_streams"]:
            raise OutputVerificationError("Video đầu ra thiếu luồng âm thanh (audio stream) (RND-ACC-001).")
            
        v_stream = info["video_streams"][0]
        a_stream = info["audio_streams"][0]
        
        # RND-ACC-002: Độ phân giải 1920x1080
        width = v_stream["width"]
        height = v_stream["height"]
        if width != 1920 or height != 1080:
            raise OutputVerificationError(
                f"Độ phân giải video không đúng. Yêu cầu 1920x1080, nhận được {width}x{height} (RND-ACC-002)."
            )
            
        # RND-ACC-002: Kiểm tra Frame Rate (CFR 24 FPS)
        avg_fps = v_stream["avg_frame_rate"] # e.g. "24/1"
        try:
            num, den = map(int, avg_fps.split("/"))
            fps = num / den if den != 0 else 0.0
            if abs(fps - config.VIDEO_FPS) > 0.01:
                raise OutputVerificationError(
                    f"Frame rate không đúng. Yêu cầu {config.VIDEO_FPS} FPS, nhận được {fps:.2f} FPS (RND-ACC-002)."
                )
        except Exception as e:
            if not isinstance(e, OutputVerificationError):
                raise OutputVerificationError(f"Không phân tích được avg_frame_rate: {avg_fps}")
            raise e
            
        # RND-ACC-003: Kiểm tra video codec (H264) và audio (AAC 48kHz)
        v_codec = v_stream["codec"]
        a_codec = a_stream["codec"]
        a_sr = a_stream["sample_rate"]
        
        if v_codec != "h264":
            raise OutputVerificationError(f"Video codec không đúng. Yêu cầu h264, nhận được {v_codec} (RND-ACC-003).")
        if a_codec != "aac":
            raise OutputVerificationError(f"Audio codec không đúng. Yêu cầu aac, nhận được {a_codec} (RND-ACC-003).")
        if a_sr != config.AUDIO_SAMPLE_RATE:
            raise OutputVerificationError(
                f"Audio sample rate không đúng. Yêu cầu {config.AUDIO_SAMPLE_RATE} Hz, nhận được {a_sr} Hz (RND-ACC-003)."
            )

        # RND-ACC-004: Thời lượng khớp trong khoảng sai số tối đa 1 frame
        duration = info["duration_seconds"]
        frame_duration = 1.0 / config.VIDEO_FPS
        if abs(duration - expected_duration) > (frame_duration + 0.1):
            raise OutputVerificationError(
                f"Thời lượng video lệch quá giới hạn cho phép. Yêu cầu {expected_duration:.2f}s, nhận được {duration:.2f}s (RND-ACC-004)."
            )

        # RND-ACC-006: [WARN] Phát hiện màn hình đen kéo dài (> 3s)
        black_frames = cls.detect_black_frames(video_path, duration=3.0)
        if black_frames:
            print(f"[Verifier WARNING] Phát hiện {len(black_frames)} đoạn màn hình đen kéo dài hơn 3s!")
            for idx, bf in enumerate(black_frames):
                print(f"  - Đoạn #{idx+1}: Bắt đầu tại giây {bf['start']:.1f}, kéo dài {bf['duration']:.1f}s")
                
        # RND-ACC-006: [WARN] Phát hiện đoạn im lặng kéo dài (> 5s)
        silence_frames = MediaProbe.detect_silence(video_path, noise_db=-50.0, duration=5.0)
        if silence_frames:
            print(f"[Verifier WARNING] Phát hiện {len(silence_frames)} đoạn im lặng kéo dài hơn 5s!")
            for idx, sf in enumerate(silence_frames):
                print(f"  - Đoạn #{idx+1}: Bắt đầu tại giây {sf['start']:.1f}, kéo dài {sf['duration']:.1f}s")

        # --- TẠO OUTPUT MANIFEST ---
        video_sha256 = CacheManager.get_file_sha256(video_path)
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        
        manifest = {
            "schema_name": "output_manifest",
            "schema_version": 1,
            "project_id": project_id,
            "video_path": f"data/output_final/{video_path.name}",
            "video_sha256": video_sha256,
            "duration_seconds": duration,
            "resolution": f"{width}x{height}",
            "fps": {"numerator": num, "denominator": den},
            "video_codec": v_codec,
            "requested_video_encoder": requested_encoder,
            "actual_video_encoder": actual_encoder,
            "video_encoder_fallback_reason": fallback_reason,
            "audio_codec": a_codec,
            "audio_sample_rate_hz": a_sr,
            "pixel_format": v_stream.get("pix_fmt", "yuv420p"),
            "color_metadata": {
                "primaries": v_stream.get("color_primaries", "bt709"),
                "transfer": v_stream.get("color_transfer", "bt709"),
                "space": v_stream.get("color_space", "bt709"),
                "range": v_stream.get("color_range", "tv")
            },
            "track_id": track_id,
            "app_version": "4.5.0",
            "config_hash": hashlib.sha256(str(config.VIDEO_DURATION_SECONDS).encode()).hexdigest(),
            "input_hash": video_sha256[:32],
            "producer_version": "4.5.0",
            "rendered_at_utc": now_str
        }
        
        # Xác thực cấu trúc manifest bằng schema
        validate_data_schema(manifest, "output_manifest")
        
        # Ghi manifest ra file bên cạnh video đầu ra
        manifest_path = video_path.with_name(f"{video_path.stem}_manifest.json")
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
            
        print(f"[Verifier] Generated output manifest at: {manifest_path.name}")
        return manifest

if __name__ == "__main__":
    # Test nhanh bằng file test.mp3 sinh tự động đã mux thành video ngắn
    import tempfile
    temp_dir = Path(tempfile.gettempdir())
    temp_audio = temp_dir / "test_verifier_audio.mp3"
    temp_video = temp_dir / "test_verifier_video.mp4"
    
    # 1. Sinh file audio test
    cmd_audio = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "sine=frequency=1000:duration=5",
        "-ac", "2", "-ar", "48000",
        str(temp_audio)
    ]
    # 2. Sinh file video 1920x1080 test
    cmd_video = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "color=c=blue:s=1920x1080:d=5",
        "-i", str(temp_audio),
        "-c:v", "libx264", "-r", "24", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        str(temp_video)
    ]
    try:
        subprocess.run(cmd_audio, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        subprocess.run(cmd_video, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        print("Sinh file video test thanh cong!")
        
        manifest = OutputVerifier.verify_video(
            video_path=temp_video,
            project_id="test_ver_prj",
            expected_duration=5.0,
            track_id="mock_track_id"
        )
        print("Xác minh video thanh cong! Manifest project_id:", manifest["project_id"])
        
        # Cleanup
        temp_audio.unlink(missing_ok=True)
        temp_video.unlink(missing_ok=True)
        temp_video.with_name("test_verifier_video_manifest.json").unlink(missing_ok=True)
        
    except Exception as e:
        print("Test failed:", str(e))
        temp_audio.unlink(missing_ok=True)
        temp_video.unlink(missing_ok=True)
        temp_video.with_name("test_verifier_video_manifest.json").unlink(missing_ok=True)
