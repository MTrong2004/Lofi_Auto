"""
AI FILE NOTE - STEP 3: EFFECT PROVIDER AND OVERLAYS

Chức năng chính:
- Liệt kê, tải và quản lý các video hiệu ứng overlay (mưa, bụi, tuyết, đĩa than...).
- Hỗ trợ tải hiệu ứng online từ Pexels API.
- Tự động sinh ra các hiệu ứng video lặp khít (seamless loop) bằng FFmpeg (lavfi) nếu thiếu.
- Chọn ngẫu nhiên hoặc chỉ định hiệu ứng cho video thành phẩm.

Đầu vào chính:
- Từ khóa tìm kiếm hiệu ứng (query), API key Pexels, hoặc yêu cầu sinh hiệu ứng mặc định.

Đầu ra chính:
- Path tới video hiệu ứng MP4 làm overlay cho bước render.

API được file khác sử dụng:
- list_effect_videos()
- download_pexels_effect()
- create_builtin_effect_pack()
- pick_effect_video()

Phụ thuộc quan trọng:
- config, requests, FFmpeg, Path.
"""
import os
import re
import random
import logging
import requests
import subprocess
from pathlib import Path

# Đảm bảo import được config.py từ thư mục cha
import sys
sys.path.append(str(Path(__file__).parent.parent))
import config

logger = logging.getLogger("lofi_automation")

def _safe_slug(text: str, max_len: int = 60) -> str:
    """Tạo tên file an toàn từ từ khóa tìm kiếm."""
    text = (text or "effect").lower().strip()
    text = re.sub(r"[^a-z0-9_-]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return (text or "effect")[:max_len]


def list_effect_videos() -> list[Path]:
    """Lấy danh sách file hiệu ứng trong data/effects."""
    config.EFFECTS_DIR.mkdir(parents=True, exist_ok=True)
    return sorted(config.EFFECTS_DIR.glob("*.mp4"))


def download_pexels_effect(query: str, api_key: str = "", max_results: int = 8) -> Path:
    """
    Tải 1 video hiệu ứng từ Pexels về data/effects.
    Cần Pexels API key. Có thể nhập trong UI hoặc đặt biến môi trường PEXELS_API_KEY.
    """
    api_key = (api_key or os.getenv("PEXELS_API_KEY", "")).strip()
    query = (query or "rain overlay").strip()
    if not api_key:
        raise ValueError("Chưa có Pexels API key. Hãy nhập API key Pexels trong giao diện hoặc đặt biến môi trường PEXELS_API_KEY.")

    headers = {"Authorization": api_key}
    params = {
        "query": query,
        "orientation": "landscape",
        "per_page": max(1, min(int(max_results), 20)),
    }
    response = requests.get("https://api.pexels.com/videos/search", headers=headers, params=params, timeout=30)
    response.raise_for_status()
    data = response.json()
    videos = data.get("videos") or []
    if not videos:
        raise ValueError(f"Không tìm thấy hiệu ứng online cho từ khóa: {query}")

    best_video_file = None
    best_video = None
    for video in videos:
        video_files = video.get("video_files") or []
        candidates = [
            f for f in video_files
            if f.get("file_type") == "video/mp4" and f.get("link")
        ]
        if not candidates:
            continue
        candidates.sort(key=lambda f: (
            abs((f.get("height") or 720) - 720),
            abs((f.get("width") or 1280) - 1280),
        ))
        best_video_file = candidates[0]
        best_video = video
        break

    if not best_video_file:
        raise ValueError("Pexels có kết quả nhưng không có file mp4 phù hợp.")

    slug = _safe_slug(query)
    video_id = best_video.get("id", random.randint(1000, 9999)) if best_video else random.randint(1000, 9999)
    out_path = config.EFFECTS_DIR / f"pexels_{slug}_{video_id}.mp4"

    download = requests.get(best_video_file["link"], stream=True, timeout=120)
    download.raise_for_status()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("wb") as f:
        for chunk in download.iter_content(chunk_size=1024 * 512):
            if chunk:
                f.write(chunk)

    if not out_path.exists() or out_path.stat().st_size < 1024:
        raise ValueError("File hiệu ứng tải về bị lỗi hoặc quá nhỏ.")

    credit_path = config.EFFECTS_DIR / "online_effects_credits.txt"
    photographer = (best_video or {}).get("user", {}).get("name", "Pexels")
    source_url = (best_video or {}).get("url", "https://www.pexels.com")
    with credit_path.open("a", encoding="utf-8") as f:
        f.write(f"{out_path.name} | Video by {photographer} on Pexels | {source_url}\n")

    logger.info(f"[Pexels] Đã tải hiệu ứng online: {out_path.name}")
    return out_path


def create_builtin_effect_pack() -> list[Path]:
    """
    Tạo bộ hiệu ứng code local (nền đen, dùng với blend screen).
    Kỹ thuật hạt rơi: sinh 1 khung noise tĩnh (select frame 0 + loop) rồi cuộn dọc
    bằng filter scroll -> hạt có quỹ đạo rơi thật thay vì nhiễu nhấp nháy.
    Tốc độ cuộn chọn sao cho sau 8s (192 frame) trôi tròn số lần chiều cao khung
    -> video lặp khít (seamless loop) khi render dùng -stream_loop.
    """
    config.EFFECTS_DIR.mkdir(parents=True, exist_ok=True)

    # Khung noise tĩnh: giữ frame 0, lặp đủ 192 frame, đặt lại timestamp 24fps
    _static_noise = (
        "select='eq(n,0)',loop=loop=191:size=1:start=0,setpts=N/(24*TB)"
    )
    # 3/192 vòng/frame: mưa rơi ~270px/s; 1/192: tuyết ~90px/s (đều tròn vòng sau 8s)
    effect_specs = {
        # Mưa: hạt thưa kéo dọc thành vệt (avgblur dọc) + tăng sáng lại, cuộn nhanh
        "effect_rain_fall.mp4": (
            "nullsrc=s=1280x720:d=8:r=24,"
            "geq=lum='if(gt(random(1),0.9975),255,0)':cb=128:cr=128,"
            f"{_static_noise},"
            "avgblur=sizeX=1:sizeY=7,lutyuv=y='min(val*10,190)',"
            "scroll=vertical=0.015625,"
            "format=yuv420p"
        ),
        # Tuyết: bông mềm (blur đều), rơi chậm
        "effect_snow_fall.mp4": (
            "nullsrc=s=1280x720:d=8:r=24,"
            "geq=lum='if(gt(random(1),0.996),255,0)':cb=128:cr=128,"
            f"{_static_noise},"
            "avgblur=sizeX=2:sizeY=2,lutyuv=y='min(val*7,210)',"
            "scroll=vertical=0.00520833,"
            "format=yuv420p"
        ),
        # Bụi: hạt rất thưa, mờ và tối, trôi lơ lửng lên trên
        "effect_dust_soft.mp4": (
            "nullsrc=s=1280x720:d=8:r=24,"
            "geq=lum='if(gt(random(1),0.998),255,0)':cb=128:cr=128,"
            f"{_static_noise},"
            "avgblur=sizeX=3:sizeY=3,lutyuv=y='min(val*5,140)',"
            "scroll=vertical=-0.00520833,"
            "format=yuv420p"
        ),
        # Scanline retro: tĩnh, vốn là hiệu ứng nhân tạo nên giữ nguyên
        "effect_retro_scanline.mp4": "nullsrc=s=1280x720:d=8:r=24,geq=lum='if(eq(mod(Y,6),0),70,0)':cb=128:cr=128,format=yuv420p",
        # Film grain: random mỗi frame là ĐÚNG bản chất grain, giữ nguyên
        "effect_light_film_grain.mp4": "nullsrc=s=1280x720:d=8:r=24,geq=lum='random(1)*45':cb=128:cr=128,format=yuv420p",
    }
    created = []
    for file_name, lavfi in effect_specs.items():
        out_path = config.EFFECTS_DIR / file_name
        if out_path.exists() and out_path.stat().st_size > 1024:
            created.append(out_path)
            continue
        cmd = [
            "ffmpeg", "-y", "-f", "lavfi", "-i", lavfi,
            "-t", "8", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(out_path),
        ]
        subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        created.append(out_path)
    return created


def pick_effect_video() -> Path:
    """Chọn ngẫu nhiên 1 video hiệu ứng (mưa/bụi/đĩa than) từ thư mục asset tĩnh."""
    effects = list_effect_videos()
    if not effects:
        default_path = config.EFFECTS_DIR / "default_effect.mp4"
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "color=c=black:s=1280x720:d=5",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            str(default_path)
        ]
        logger.info(f"Tạo file hiệu ứng mặc định: {' '.join(cmd)}")
        subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        effects = [default_path]
    return random.choice(effects)

# --- Pixabay Video API ---
PIXABAY_VIDEO_API_URL = "https://pixabay.com/api/videos/"
PIXABAY_LICENSE_NAME = "Pixabay Content License"
PIXABAY_LICENSE_URL = "https://pixabay.com/service/license-summary/"


def _pick_pixabay_file(files: dict) -> dict | None:
    items = [dict(files.get(q) or {}, quality=q) for q in ("medium", "small", "large", "tiny") if (files.get(q) or {}).get("url")]
    items.sort(key=lambda x: (abs(int(x.get("height") or 720) - 720), int(x.get("size") or 0)))
    return items[0] if items else None


def search_pixabay_effects(query: str, api_key: str = "", max_results: int = 12) -> list[dict]:
    """Tìm metadata video; không tải tự động."""
    key = (api_key or getattr(config, "PIXABAY_API_KEY", "") or os.getenv("PIXABAY_API_KEY", "")).strip()
    if not key:
        raise ValueError("Chưa có Pixabay API key.")
    query = (query or "rain overlay").strip()[:100]
    response = requests.get(PIXABAY_VIDEO_API_URL, params={
        "key": key, "q": query, "video_type": "film", "safesearch": "true",
        "per_page": max(3, min(int(max_results), 20)),
    }, headers={"User-Agent": "LoFi-Studio/1.0"}, timeout=int(getattr(config, "EFFECT_API_TIMEOUT", 20)))
    response.raise_for_status()
    results = []
    for hit in response.json().get("hits") or []:
        chosen = _pick_pixabay_file(hit.get("videos") or {})
        if not chosen:
            continue
        results.append({
            "provider": "pixabay", "id": int(hit.get("id") or 0),
            "page_url": str(hit.get("pageURL") or ""), "download_url": str(chosen["url"]),
            # Thumbnail chỉ dùng hiển thị kết quả; video vẫn chỉ tải khi người dùng chọn.
            "thumbnail_url": str(chosen.get("thumbnail") or ""),
            "tags": [x.strip() for x in str(hit.get("tags") or "").split(",") if x.strip()],
            "duration": float(hit.get("duration") or 0), "width": int(chosen.get("width") or 0),
            "height": int(chosen.get("height") or 0), "file_size": int(chosen.get("size") or 0),
            "quality": chosen.get("quality"), "query": query,
            "license_name": PIXABAY_LICENSE_NAME, "license_url": PIXABAY_LICENSE_URL,
        })
    return results


def _allowed_pixabay_url(url: str) -> bool:
    from urllib.parse import urlparse
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and any(host == domain or host.endswith("." + domain) for domain in ("pixabay.com", "pixabayusercontent.com"))


def download_pixabay_effect(candidate: dict) -> Path:
    """Tải đúng video được chọn, giới hạn dung lượng và ghi manifest."""
    import shutil
    from core.effects.manifest import register_effect, sha256_file
    if candidate.get("provider") != "pixabay" or not _allowed_pixabay_url(str(candidate.get("download_url") or "")):
        raise ValueError("Kết quả tải không hợp lệ hoặc không thuộc Pixabay.")
    asset_id = int(candidate.get("id") or 0)
    if not asset_id:
        raise ValueError("Kết quả Pixabay thiếu asset id.")
    limit_mb = int(getattr(config, "EFFECT_MAX_DOWNLOAD_MB", 30))
    limit = limit_mb * 1024 * 1024
    if int(candidate.get("file_size") or 0) > limit:
        raise ValueError(f"Video vượt giới hạn {limit_mb} MB.")
    config.EFFECTS_DIR.mkdir(parents=True, exist_ok=True)
    output = config.EFFECTS_DIR / f"pixabay_{asset_id}.mp4"
    if output.is_file() and output.stat().st_size > 1024:
        return output
    partial = output.with_suffix(".mp4.part")
    total = 0
    try:
        with requests.get(candidate["download_url"], stream=True, headers={"User-Agent": "LoFi-Studio/1.0"}, timeout=(15, 120)) as response:
            response.raise_for_status()
            with partial.open("wb") as stream:
                for chunk in response.iter_content(512 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > limit:
                        raise ValueError(f"Video vượt giới hạn {limit_mb} MB.")
                    stream.write(chunk)
        partial.replace(output)
        if not output.is_file() or output.stat().st_size < 1024:
            raise ValueError("File tải về bị lỗi hoặc quá nhỏ.")
        if shutil.which("ffprobe"):
            test = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_type", "-of", "default=nw=1:nk=1", str(output)], capture_output=True, text=True, timeout=20)
            if test.returncode or "video" not in test.stdout:
                raise ValueError("File tải về không phải video hợp lệ.")
    except Exception:
        partial.unlink(missing_ok=True)
        output.unlink(missing_ok=True)
        raise
    register_effect(config.EFFECTS_DIR, {
        "file_name": output.name, "provider": "pixabay", "provider_asset_id": asset_id,
        "source_page_url": candidate.get("page_url"), "license_name": PIXABAY_LICENSE_NAME,
        "license_url": PIXABAY_LICENSE_URL, "query": candidate.get("query"),
        "thumbnail_url": candidate.get("thumbnail_url") or None,
        "tags": candidate.get("tags") or [], "duration_seconds": candidate.get("duration"),
        "width": candidate.get("width"), "height": candidate.get("height"),
        "file_size": output.stat().st_size, "sha256": sha256_file(output), "status": "ready",
    })
    # Tự nhận diện loại nền (phông xanh/nền đen/alpha) ngay sau khi tải.
    try:
        from core.effects.analyzer import analyze_and_register
        analyze_and_register(output)
    except Exception as exc:
        logger.warning(f"[EffectAnalyzer] Không phân tích được {output.name}: {exc}")
    return output


def get_effect_metadata(effect_path: str | Path) -> dict:
    from core.effects.manifest import get_effect_metadata as get_metadata
    return get_metadata(config.EFFECTS_DIR, effect_path)


def sync_effect_manifest(calculate_hashes: bool = False) -> dict:
    """Đồng bộ manifest với thư viện local và tự nhận diện file cũ."""
    from core.effects.manifest import reconcile_manifest
    return reconcile_manifest(config.EFFECTS_DIR, calculate_hashes=calculate_hashes)


def list_effect_records(include_missing: bool = False) -> list[dict]:
    """Lấy metadata thư viện để UI hiển thị nguồn, license và trạng thái."""
    from core.effects.manifest import list_effect_records as _list
    return _list(config.EFFECTS_DIR, include_missing=include_missing)


def remove_missing_manifest_entries() -> int:
    """Dọn metadata của file đã bị xóa khỏi ổ đĩa; không xóa video."""
    from core.effects.manifest import remove_missing_entries
    return remove_missing_entries(config.EFFECTS_DIR)


def build_ai_effect_profile(track: dict, music_tags: list[str], image_context: str) -> dict:
    """Tạo hồ sơ hiệu ứng bằng AI; lỗi API tự chuyển sang mapping local."""
    from core.effects.recommender import build_effect_profile
    return build_effect_profile(
        track, music_tags, image_context,
        api_url=getattr(config, "PROMPT_API_URL", ""),
        api_key=getattr(config, "PROMPT_API_KEY", ""),
        model=getattr(config, "PROMPT_API_MODEL", "openai"),
        timeout=int(getattr(config, "PROMPT_API_TIMEOUT", 40)),
        enabled=bool(getattr(config, "EFFECT_AI_ENABLED", True)),
    )


def search_and_rank_pixabay_effects(profile: dict, api_key: str = "") -> list[dict]:
    """Tìm tối đa 3 query, khử trùng và xếp hạng metadata; không tự tải."""
    from core.effects.recommender import rank_candidates
    max_queries = int(getattr(config, "EFFECT_AI_MAX_QUERIES", 3))
    max_results = int(getattr(config, "EFFECT_AI_MAX_RESULTS", 6))
    candidates = []
    seen_ids = set()
    for query in (profile.get("queries") or [])[:max_queries]:
        for item in search_pixabay_effects(str(query), api_key, max_results=10):
            asset_id = item.get("id")
            if asset_id in seen_ids:
                continue
            seen_ids.add(asset_id)
            candidates.append(item)
    return rank_candidates(candidates, profile)[:max_results]


def recommend_effects(profile: dict, api_key: str = "", min_local_results: int = 3, min_local_score: int = 55) -> dict:
    """
    Luồng đề xuất local-first:
    1. Xếp hạng thư viện local theo hồ sơ AI.
    2. Đủ min_local_results kết quả đạt điểm thì KHÔNG gọi Pixabay (tiết kiệm quota,
       chạy được offline).
    3. Thiếu mới tìm thêm Pixabay; kết quả local vẫn đứng trước.
    """
    from core.effects.recommender import rank_local_effects
    local_ranked = rank_local_effects(list_effect_records(), profile)
    good_local = [item for item in local_ranked if int(item.get("ai_score") or 0) >= min_local_score]
    online: list[dict] = []
    online_error = ""
    if len(good_local) < int(min_local_results):
        try:
            online = search_and_rank_pixabay_effects(profile, api_key)
        except Exception as exc:
            online_error = str(exc)[:300]
    for item in online:
        item["origin"] = "pixabay"
    return {
        "local": local_ranked,
        "online": online,
        "used_pixabay": bool(online),
        "online_error": online_error,
    }


def analyze_effect_type(effect_path: str | Path) -> dict:
    """Phân tích loại nền (phông xanh/nền đen/alpha) và ghi vào manifest."""
    from core.effects.analyzer import analyze_and_register
    return analyze_and_register(Path(effect_path))
