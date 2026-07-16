"""
Media Probe Module.
Uses ffprobe to analyze media streams, retrieve durations, verify codecs, and measure loudness (LUFS) of audio.
"""
import subprocess
import json
import re
import os
import sys
from pathlib import Path

# Đảm bảo import được config.py từ thư mục cha
sys.path.append(str(Path(__file__).parent.parent))
import config

class MediaProbeError(Exception):
    pass

class MediaProbe:
    @staticmethod
    def probe_media(file_path: Path) -> dict:
        """
        Chạy ffprobe để trích xuất metadata chi tiết của file media.
        """
        if not file_path.exists():
            raise FileNotFoundError(f"Không tìm thấy file: {file_path}")

        cmd = [
            "ffprobe",
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            str(file_path)
        ]
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True, encoding="utf-8", errors="ignore")
            data = json.loads(result.stdout)
            
            # Trích xuất thông tin cần thiết
            fmt = data.get("format", {})
            streams = data.get("streams", [])
            
            video_streams = []
            audio_streams = []
            
            for s in streams:
                codec_type = s.get("codec_type")
                if codec_type == "video":
                    # Phân tích frame rate
                    avg_fps_str = s.get("avg_frame_rate", "0/0")
                    r_fps_str = s.get("r_frame_rate", "0/0")
                    
                    video_streams.append({
                        "codec": s.get("codec_name"),
                        "width": int(s.get("width") or 0),
                        "height": int(s.get("height") or 0),
                        "avg_frame_rate": avg_fps_str,
                        "r_frame_rate": r_fps_str,
                        "time_base": s.get("time_base"),
                        "duration": float(s.get("duration") or fmt.get("duration") or 0.0),
                        "nb_frames": int(s.get("nb_frames") or 0)
                    })
                elif codec_type == "audio":
                    audio_streams.append({
                        "codec": s.get("codec_name"),
                        "sample_rate": int(s.get("sample_rate") or 0),
                        "channels": int(s.get("channels") or 0),
                        "channel_layout": s.get("channel_layout"),
                        "duration": float(s.get("duration") or fmt.get("duration") or 0.0),
                    })
            
            # Tính thời lượng thực
            duration = float(fmt.get("duration") or 0.0)
            if not duration and video_streams:
                duration = video_streams[0]["duration"]
            if not duration and audio_streams:
                duration = audio_streams[0]["duration"]

            return {
                "format_name": fmt.get("format_name"),
                "duration_seconds": duration,
                "size_bytes": int(fmt.get("size") or file_path.stat().st_size),
                "bit_rate": int(fmt.get("bit_rate") or 0),
                "video_streams": video_streams,
                "audio_streams": audio_streams
            }
            
        except subprocess.CalledProcessError as e:
            raise MediaProbeError(f"ffprobe failed for {file_path.name}: {e.stderr}")
        except Exception as e:
            raise MediaProbeError(f"Error probing {file_path.name}: {str(e)}")

    @staticmethod
    def get_loudness_and_peak(file_path: Path) -> dict:
        """
        Chạy ffmpeg với bộ lọc loudnorm để đo integrated loudness (LUFS) và true peak (dBTP).
        """
        if not file_path.exists():
            raise FileNotFoundError(f"Không tìm thấy file: {file_path}")

        # Chạy pass đầu tiên của loudnorm ở định dạng in ra JSON
        cmd = [
            "ffmpeg",
            "-i", str(file_path),
            "-af", "loudnorm=print_format=json",
            "-f", "null",
            "-"
        ]
        
        try:
            # loudnorm log xuất ra stderr
            result = subprocess.run(cmd, capture_output=True, text=True, check=True, encoding="utf-8", errors="ignore")
            stderr = result.stderr
            
            # Trích xuất JSON từ stderr (tìm khối giữa cặp dấu ngoặc nhọn {})
            json_match = re.search(r"\{\s*\"input_i\".*?\}", stderr, re.DOTALL)
            if not json_match:
                raise MediaProbeError("Không tìm thấy kết quả đo loudness trong logs FFmpeg.")
                
            loudness_data = json.loads(json_match.group(0))
            
            return {
                "integrated_loudness": float(loudness_data["input_i"]),
                "true_peak": float(loudness_data["input_tp"]),
                "loudness_range": float(loudness_data["input_lra"]),
                "threshold": float(loudness_data["input_thresh"])
            }
        except subprocess.CalledProcessError as e:
            # FFmpeg trả về exit code khác 0 khi dùng filter output null nhưng đôi khi vẫn in ra kết quả
            # Thử parse lại từ stderr trước khi crash
            try:
                stderr = e.stderr
                json_match = re.search(r"\{\s*\"input_i\".*?\}", stderr, re.DOTALL)
                if json_match:
                    loudness_data = json.loads(json_match.group(0))
                    return {
                        "integrated_loudness": float(loudness_data["input_i"]),
                        "true_peak": float(loudness_data["input_tp"]),
                        "loudness_range": float(loudness_data["input_lra"]),
                        "threshold": float(loudness_data["input_thresh"])
                    }
            except Exception:
                pass
            raise MediaProbeError(f"ffmpeg loudness check failed for {file_path.name}: {e.stderr}")
        except Exception as e:
            raise MediaProbeError(f"Error measuring loudness for {file_path.name}: {str(e)}")

    @staticmethod
    def detect_silence(file_path: Path, noise_db: float = -50.0, duration: float = 2.0) -> list[dict]:
        """
        Chạy ffmpeg với bộ lọc silencedetect để phát hiện các đoạn im lặng bất thường.
        """
        if not file_path.exists():
            raise FileNotFoundError(f"Không tìm thấy file: {file_path}")

        cmd = [
            "ffmpeg",
            "-i", str(file_path),
            "-af", f"silencedetect=noise={noise_db}dB:duration={duration}",
            "-f", "null",
            "-"
        ]
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
            stderr = result.stderr
            
            # Parse silence_start và silence_end từ stderr
            silence_ranges = []
            starts = re.findall(r"silence_start:\s*([\d\.]+)", stderr)
            ends = re.findall(r"silence_end:\s*([\d\.]+)\s*\|\s*silence_duration:\s*([\d\.]+)", stderr)
            
            # Ghép các khoảng im lặng
            for i in range(min(len(starts), len(ends))):
                silence_ranges.append({
                    "start": float(starts[i]),
                    "end": float(ends[i][0]),
                    "duration": float(ends[i][1])
                })
                
            return silence_ranges
        except Exception as e:
            raise MediaProbeError(f"Error detecting silence for {file_path.name}: {str(e)}")

if __name__ == "__main__":
    # Test nhanh bằng 1 file mp4 hoặc mp3 có sẵn
    # Nếu không có file nào, tạo 1 file sinh tự động
    import tempfile
    temp_file = Path(tempfile.gettempdir()) / "test_probe.mp3"
    
    cmd_gen = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "sine=frequency=1000:duration=5",
        "-ac", "2", "-ar", "48000",
        str(temp_file)
    ]
    try:
        subprocess.run(cmd_gen, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        print("Sinh file audio test thanh cong!")
        
        info = MediaProbe.probe_media(temp_file)
        print("Probe info duration:", info["duration_seconds"])
        print("Audio streams info:", info["audio_streams"])
        
        loud = MediaProbe.get_loudness_and_peak(temp_file)
        print("Loudness (LUFS):", loud["integrated_loudness"])
        print("True Peak (dBTP):", loud["true_peak"])
        
        silence = MediaProbe.detect_silence(temp_file)
        print("Silence segments detected:", len(silence))
        
        # Cleanup
        if temp_file.exists():
            temp_file.unlink()
            
    except Exception as e:
        print("Test failed:", str(e))
        if temp_file.exists():
            temp_file.unlink()
