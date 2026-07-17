"""
AI FILE NOTE - EFFECT ANALYZER (tự nhận diện loại nền của video hiệu ứng)

Chức năng chính:
- Lấy mẫu vài khung hình của video hiệu ứng bằng FFmpeg.
- Phân tích màu vùng BIÊN + lưới thưa toàn khung để phân loại:
  chroma_key (phông xanh/xanh dương bão hòa), screen_black (nền gần đen),
  alpha (video có kênh alpha), normal (còn lại).
- Xác định MÀU KEY bằng màu trội (histogram/mode), tự chọn chroma_similarity theo độ tán màu,
  và có FALLBACK khi tín hiệu xanh yếu nhưng vẫn có cụm xanh rõ (tránh ám xanh toàn khung).
- Đề xuất bộ thông số compositing ban đầu và ghi kết quả vào manifest.

API được file khác sử dụng:
- analyze_effect_background(path) -> dict (có thể kèm khóa 'detection_warning')
- analyze_and_register(path) -> dict (phân tích + ghi manifest)

Lưu ý khi sửa:
- Phát hiện chạy local hoàn toàn (FFmpeg + Pillow), không gọi AI.
- Màu key lấy theo MODE (đỉnh histogram), KHÔNG dùng trung bình — trung bình dạt màu khi phông
  sáng không đều, gây còn ám xanh và buộc chỉnh tay.
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


def _is_green_like(r: int, g: int, b: int) -> tuple[bool, float, float]:
    """True nếu pixel thuộc nhóm phông xanh lá / xanh dương bão hòa.

    Hạ ngưỡng bão hòa xuống 0.35 (trước 0.45) để không bỏ sót phông xanh NHẠT/ám sáng
    — đây là nguyên nhân trước đây phân loại nhầm sang 'normal' gây ám xanh toàn khung.
    Trả về (green_like, hue_deg, sat) để nơi gọi tính độ tán màu.
    """
    h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
    hue_deg = h * 360.0
    # Dải hue 70°-200° phủ green screen lẫn blue-green; blue screen thuần ~240°.
    green_like = s > 0.35 and v > 0.20 and (70.0 <= hue_deg <= 200.0 or 205.0 <= hue_deg <= 260.0)
    return green_like, hue_deg, s


def _sample_pixels(image) -> tuple[list[tuple[int, int, int]], int]:
    """Lấy mẫu pixel: dày ở vùng BIÊN + lưới thưa toàn khung.

    Lấy cả vùng trong để không bỏ sót màu key khi chủ thể chạm biên hoặc phông không đều.
    Trả về (danh sách pixel, số pixel biên) — pixel biên dùng để ước lượng tỷ lệ phông nền.
    """
    width, height = image.size
    thickness = max(2, int(min(width, height) * _BORDER_RATIO))
    pixels = image.load()
    samples: list[tuple[int, int, int]] = []
    border_count = 0
    step = 3
    for y in range(0, height, step):
        for x in range(0, width, step):
            on_border = x < thickness or x >= width - thickness or y < thickness or y >= height - thickness
            if on_border:
                samples.append(pixels[x, y][:3])
                border_count += 1
            elif (x % 9 == 0) and (y % 9 == 0):  # lưới thưa cho vùng trong
                samples.append(pixels[x, y][:3])
    return samples, border_count


def _classify_samples(samples: list[tuple[int, int, int]], border_count: int) -> dict[str, Any]:
    """Đếm tỷ lệ pixel xanh (green/blue screen) và gần đen; gom pixel xanh để phân tích cụm màu."""
    total = max(len(samples), 1)
    border_total = max(border_count, 1)
    green_like = 0
    green_border = 0
    dark = 0
    green_pixels: list[tuple[int, int, int]] = []
    for idx, (r, g, b) in enumerate(samples):
        _, _, v_hsv = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
        if v_hsv < 0.16:
            dark += 1
            continue
        is_green, _, _ = _is_green_like(r, g, b)
        if is_green:
            green_like += 1
            green_pixels.append((r, g, b))
            if idx < border_count:  # phần đầu danh sách là pixel biên (xem _sample_pixels)
                green_border += 1
    return {
        "green_ratio": green_like / total,
        "green_border_ratio": green_border / border_total,
        "dark_ratio": dark / total,
        "green_pixels": green_pixels,
    }


def _dominant_key_color(green_pixels: list[tuple[int, int, int]]) -> str | None:
    """Màu key = MÀU TRỘI theo histogram (mode), KHÔNG phải trung bình.

    Trung bình bị lệch khi phông sáng không đều; lấy đỉnh histogram (gom theo lưới màu ~12)
    rồi tính trung bình trong ô trội cho ra màu phông thực tế, bền hơn nhiều.
    """
    if not green_pixels:
        return None
    from collections import Counter
    buckets: Counter = Counter()
    for r, g, b in green_pixels:
        buckets[(r // 12, g // 12, b // 12)] += 1
    (br, bg, bb), _ = buckets.most_common(1)[0]
    sel = [(r, g, b) for r, g, b in green_pixels if (r // 12, g // 12, b // 12) == (br, bg, bb)]
    n = len(sel)
    return "#{:02X}{:02X}{:02X}".format(
        sum(p[0] for p in sel) // n, sum(p[1] for p in sel) // n, sum(p[2] for p in sel) // n
    )


def _auto_similarity(green_pixels: list[tuple[int, int, int]], key_hex: str) -> float:
    """Tự chọn chroma_similarity theo độ TÁN màu của phông quanh màu key.

    Phông đều → cụm chặt → similarity nhỏ (giữ chi tiết chủ thể).
    Phông loang/ám sáng → cụm rộng → similarity lớn hơn (phủ hết sắc xanh, đỡ ám xanh).
    """
    import math
    if not green_pixels:
        return 0.18
    dr, dg, db = int(key_hex[1:3], 16), int(key_hex[3:5], 16), int(key_hex[5:7], 16)
    norm = math.sqrt(3 * 255.0 ** 2)
    dists = [math.sqrt((r - dr) ** 2 + (g - dg) ** 2 + (b - db) ** 2) / norm for r, g, b in green_pixels]
    mean_d = sum(dists) / len(dists)
    sim = 0.12 + min(mean_d, 0.30) / 0.30 * 0.20  # map 0..0.30 -> 0.12..0.32
    return round(min(max(sim, 0.10), 0.34), 3)


def _recommended_composite(
    effect_type: str,
    detected_color: str | None,
    chroma_similarity: float = 0.18,
) -> dict[str, Any]:
    if effect_type == "chroma_key":
        return {
            "blend_mode": "normal",
            "opacity": 0.85,
            "speed": 1.0,
            "key_color": detected_color or "#00FF00",
            "chroma_similarity": chroma_similarity,
            "chroma_softness": 0.10,
            "despill": 0.5,  # tăng despill mặc định để khử viền xanh còn sót
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
    green_border_ratios: list[float] = []
    dark_ratios: list[float] = []
    all_green_pixels: list[tuple[int, int, int]] = []
    try:
        for frame in frames:
            with Image.open(frame) as img:
                samples, border_count = _sample_pixels(img.convert("RGB"))
                stats = _classify_samples(samples, border_count)
            green_ratios.append(stats["green_ratio"])
            green_border_ratios.append(stats["green_border_ratio"])
            dark_ratios.append(stats["dark_ratio"])
            all_green_pixels.extend(stats["green_pixels"])
    finally:
        for frame in frames:
            frame.unlink(missing_ok=True)
        try:
            frames[0].parent.rmdir()
        except OSError:
            pass

    avg_green = sum(green_ratios) / len(green_ratios)
    avg_green_border = sum(green_border_ratios) / len(green_border_ratios)
    avg_dark = sum(dark_ratios) / len(dark_ratios)

    # Màu key + similarity tự động từ toàn bộ pixel xanh gom được (bền hơn trung bình từng frame).
    key_color = _dominant_key_color(all_green_pixels) or "#00FF00"
    auto_sim = _auto_similarity(all_green_pixels, key_color)
    warning = None

    # Ưu tiên tỷ lệ xanh ở VIỀN (nơi thường là phông nền) để quyết định.
    if avg_green_border >= 0.45 or avg_green >= 0.45:
        # Tín hiệu mạnh: chắc chắn là phông xanh.
        effect_type = "chroma_key"
        confidence = round(min(max(avg_green_border, avg_green) + 0.15, 0.99), 2)
        background = key_color
    elif (avg_green_border >= 0.20 or avg_green >= 0.20) and len(all_green_pixels) >= 30:
        # FALLBACK: tín hiệu yếu (phông nhạt / chủ thể to) nhưng vẫn có cụm xanh rõ ở biên
        # -> vẫn đề xuất chroma_key kèm cảnh báo, thay vì rơi về 'normal' (tránh ám xanh toàn khung).
        effect_type = "chroma_key"
        confidence = round(min(max(avg_green_border, avg_green) + 0.10, 0.75), 2)
        background = key_color
        warning = (
            "Tín hiệu phông xanh yếu (phông nhạt hoặc chủ thể lớn) — đã tự chọn chroma_key. "
            "Nếu còn ám xanh hãy tăng nhẹ similarity/despill, hoặc đổi sang 'normal' nếu đây không phải phông xanh."
        )
    elif avg_dark >= 0.60:
        effect_type = "screen_black"
        confidence = round(min(avg_dark + 0.15, 0.99), 2)
        background = "#000000"
    else:
        effect_type = "normal"
        confidence = round(max(1.0 - max(avg_green, avg_dark), 0.5), 2)
        background = None

    sim_for_reco = auto_sim if effect_type == "chroma_key" else 0.18
    result = {
        "effect_type": effect_type,
        "detected_background": background,
        "detection_confidence": confidence,
        "detection_method": (
            f"sampling(green={avg_green:.2f},green_border={avg_green_border:.2f},"
            f"dark={avg_dark:.2f},sim={sim_for_reco:.2f})"
        ),
        "recommended_composite": _recommended_composite(effect_type, background, sim_for_reco),
    }
    if warning:
        result["detection_warning"] = warning
    return result


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
        "detection_warning": analysis.get("detection_warning"),
        "recommended_composite": analysis["recommended_composite"],
    })
    logger.info(
        f"[EffectAnalyzer] {video_path.name}: {analysis['effect_type']} "
        f"(tin cậy {analysis['detection_confidence']:.0%})"
    )
    return analysis
