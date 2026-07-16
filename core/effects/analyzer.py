"""
AI FILE NOTE - EFFECT ANALYZER (tự nhận diện loại nền của video hiệu ứng)

Chức năng chính:
- Lấy mẫu vài khung hình của video hiệu ứng bằng FFmpeg.
- Phân tích màu vùng BIÊN khung hình để phân loại:
  chroma_key (phông xanh/xanh dương bão hòa), screen_black (nền gần đen),
  alpha (video có kênh alpha), normal (còn lại).
- Đề xuất bộ thông số compositing ban đầu và ghi kết quả vào manifest.

API được file khác sử dụng:
- analyze_effect_background(path) -> dict
- analyze_and_register(path) -> dict (phân tích + ghi manifest)

Lưu ý khi sửa:
- Phát hiện chạy local hoàn toàn (FFmpeg + Pillow), không gọi AI.
- Kết quả chỉ là đề xuất; người dùng vẫn chỉnh tay được trong UI.
- Không xóa/tải video; chỉ đọc file và cập nhật metadata manifest.
"""
from __future__ import annotations

import colorsys
import json
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger("lofi_automation")

_SAMPLE_FRAMES = 5
_ANALYSIS_WIDTH = 320
_BORDER_RATIO = 0.12  # tỷ lệ bề dày vùng biên so với cạnh khung hình

# Các pix_fmt có kênh alpha thực sự (yuva, rgba, argb...).
_ALPHA_HINTS = ("yuva", "rgba", "argb", "abgr", "bgra", "ya8", "ya16", "gbrap")


def _probe_pix_fmt(video_path: Path) -> str:
    if shutil.which("ffprobe") is None:
        return ""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=pix_fmt,duration",
                "-of", "json", str(video_path),
            ],
            capture_output=True, text=True, timeout=20,
        )
        data = json.loads(result.stdout or "{}")
        streams = data.get("streams") or [{}]
        return str(streams[0].get("pix_fmt") or "")
    except Exception:
        return ""


def _extract_sample_frames(video_path: Path, count: int = _SAMPLE_FRAMES) -> list[Path]:
    """Lấy mẫu khung hình rải đều bằng ffmpeg; trả về danh sách PNG tạm."""
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("Không tìm thấy FFmpeg trong PATH.")
    out_dir = Path(tempfile.mkdtemp(prefix="effect_analyze_"))
    pattern = out_dir / "frame_%02d.png"
    # thumbnail=n chọn khung đại diện; select theo khoảng cách đều đơn giản và đủ tốt.
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error", "-i", str(video_path),
            "-vf", f"select='not(mod(n,24))',scale={_ANALYSIS_WIDTH}:-2",
            "-frames:v", str(count), "-vsync", "vfr", str(pattern),
        ],
        capture_output=True, text=True, timeout=60,
    )
    frames = sorted(out_dir.glob("frame_*.png"))
    if not frames:
        raise RuntimeError(
            "Không lấy được khung hình mẫu: " + (result.stderr or "không rõ nguyên nhân").strip()[-300:]
        )
    return frames


def _border_pixels(image) -> list[tuple[int, int, int]]:
    width, height = image.size
    thickness = max(2, int(min(width, height) * _BORDER_RATIO))
    pixels = image.load()
    samples = []
    step = 3  # lấy thưa cho nhanh; vùng biên vẫn đủ đại diện
    for y in range(0, height, step):
        for x in range(0, width, step):
            if x < thickness or x >= width - thickness or y < thickness or y >= height - thickness:
                samples.append(pixels[x, y][:3])
    return samples


def _classify_samples(samples: list[tuple[int, int, int]]) -> dict[str, Any]:
    """Đếm tỷ lệ pixel biên thuộc nhóm: xanh bão hòa (green/blue screen) và gần đen."""
    total = max(len(samples), 1)
    green_like = 0
    dark = 0
    green_sum = [0, 0, 0]
    for r, g, b in samples:
        h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
        if v < 0.16:
            dark += 1
            continue
        # Dải hue 60°-180° phủ green screen lẫn blue-green; blue screen thuần ~240°.
        hue_deg = h * 360.0
        if s > 0.45 and v > 0.25 and (75.0 <= hue_deg <= 195.0 or 205.0 <= hue_deg <= 255.0):
            green_like += 1
            green_sum[0] += r
            green_sum[1] += g
            green_sum[2] += b
    detected_color = None
    if green_like:
        detected_color = "#{:02X}{:02X}{:02X}".format(
            green_sum[0] // green_like, green_sum[1] // green_like, green_sum[2] // green_like
        )
    return {
        "green_ratio": green_like / total,
        "dark_ratio": dark / total,
        "detected_color": detected_color,
    }


def _recommended_composite(effect_type: str, detected_color: str | None) -> dict[str, Any]:
    if effect_type == "chroma_key":
        return {
            "blend_mode": "normal",
            "opacity": 0.85,
            "speed": 1.0,
            "key_color": detected_color or "#00FF00",
            "chroma_similarity": 0.18,
            "chroma_softness": 0.08,
            "despill": 0.35,
            "edge_feather": 1.5,
        }
    if effect_type == "screen_black":
        return {
            "blend_mode": "normal",
            "opacity": 0.72,
            "speed": 1.0,
            "key_color": None,
            "chroma_similarity": 0.18,
            "chroma_softness": 0.08,
            "despill": None,
            "edge_feather": None,
        }
    if effect_type == "alpha":
        return {
            "blend_mode": "normal", "opacity": 0.85, "speed": 1.0,
            "key_color": None, "chroma_similarity": None, "chroma_softness": None,
            "despill": None, "edge_feather": None,
        }
    return {
        "blend_mode": "normal", "opacity": 0.45, "speed": 1.0,
        "key_color": None, "chroma_similarity": None, "chroma_softness": None,
        "despill": None, "edge_feather": None,
    }


def analyze_effect_background(video_path: str | Path) -> dict[str, Any]:
    """
    Phân tích local: trả về effect_type, detected_background, detection_confidence
    và recommended_composite. Không ghi manifest.
    """
    from PIL import Image

    video_path = Path(video_path)
    if not video_path.is_file():
        raise FileNotFoundError(f"Không tìm thấy video hiệu ứng: {video_path}")

    pix_fmt = _probe_pix_fmt(video_path)
    if any(hint in pix_fmt for hint in _ALPHA_HINTS):
        result = {
            "effect_type": "alpha",
            "detected_background": None,
            "detection_confidence": 0.99,
            "detection_method": f"pix_fmt={pix_fmt}",
        }
        result["recommended_composite"] = _recommended_composite("alpha", None)
        return result

    frames = _extract_sample_frames(video_path)
    green_ratios: list[float] = []
    dark_ratios: list[float] = []
    colors: list[str] = []
    try:
        for frame in frames:
            with Image.open(frame) as img:
                stats = _classify_samples(_border_pixels(img.convert("RGB")))
            green_ratios.append(stats["green_ratio"])
            dark_ratios.append(stats["dark_ratio"])
            if stats["detected_color"]:
                colors.append(stats["detected_color"])
    finally:
        for frame in frames:
            frame.unlink(missing_ok=True)
        try:
            frames[0].parent.rmdir()
        except OSError:
            pass

    avg_green = sum(green_ratios) / len(green_ratios)
    avg_dark = sum(dark_ratios) / len(dark_ratios)
    if avg_green >= 0.55:
        effect_type = "chroma_key"
        confidence = round(min(avg_green + 0.15, 0.99), 2)
        background = colors[len(colors) // 2] if colors else "#00FF00"
    elif avg_dark >= 0.60:
        effect_type = "screen_black"
        confidence = round(min(avg_dark + 0.15, 0.99), 2)
        background = "#000000"
    else:
        effect_type = "normal"
        confidence = round(max(1.0 - max(avg_green, avg_dark), 0.5), 2)
        background = None

    return {
        "effect_type": effect_type,
        "detected_background": background,
        "detection_confidence": confidence,
        "detection_method": f"border_sampling(green={avg_green:.2f},dark={avg_dark:.2f})",
        "recommended_composite": _recommended_composite(effect_type, background),
    }


def analyze_and_register(video_path: str | Path, effects_dir: str | Path | None = None) -> dict[str, Any]:
    """Phân tích rồi ghi kết quả vào manifest của thư viện hiệu ứng."""
    import config
    from core.effects.manifest import register_effect

    video_path = Path(video_path)
    directory = Path(effects_dir) if effects_dir else Path(config.EFFECTS_DIR)
    analysis = analyze_effect_background(video_path)
    register_effect(directory, {
        "file_name": video_path.name,
        "effect_type": analysis["effect_type"],
        "detected_background": analysis["detected_background"],
        "detection_confidence": analysis["detection_confidence"],
        "recommended_composite": analysis["recommended_composite"],
    })
    logger.info(
        f"[EffectAnalyzer] {video_path.name}: {analysis['effect_type']} "
        f"(tin cậy {analysis['detection_confidence']:.0%})"
    )
    return analysis
