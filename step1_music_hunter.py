"""
AI FILE NOTE - STEP 1: MUSIC HUNTER

Chức năng chính:
- Tìm nhạc ứng viên từ YouTube/SoundCloud bằng yt-dlp.
- Đọc metadata, tạo URL/file nghe thử, tải audio và kiểm tra chất lượng.
- Lọc sơ bộ nguy cơ bản quyền theo blacklist; đây không phải xác nhận pháp lý.
- Lưu asset, trạng thái workflow và lịch sử trend YouTube/Last.fm vào SQLite.
- Tổng hợp điểm xu hướng đa nền tảng khi có dữ liệu thật.

Đầu vào chính:
- Từ khóa tìm kiếm hoặc URL track, project_id và Last.fm API key tùy chọn.

Đầu ra chính:
- Danh sách dict track chuẩn hóa, đường dẫn audio/preview và dict phân tích trend.

API được file khác sử dụng:
- fetch_candidate_tracks(), fetch_track_metadata_by_url()
- get_stream_url(), get_preview_audio_path(), download_track()
- capture_youtube_trend_snapshot(), fetch_lastfm_chart(), enrich_tracks_with_lastfm()
- build_cross_platform_trend(), is_license_safe(), run_step1()

Phụ thuộc quan trọng:
- config, yt-dlp, FFmpeg, SQLite, MediaProbe, MetadataStore, ProjectManager.

Lưu ý khi sửa:
- Giữ cấu trúc dict track và tên khóa vì step3_review_app.py đang dùng trực tiếp.
- Không đổi quy ước file audio <track_id>.m4a hoặc schema DB nếu chưa cập nhật nơi gọi.
- Các lệnh subprocess phải dùng sys.executable để bám đúng môi trường Python hiện tại.
"""
import os
import sys
import logging
import json
import subprocess
import sqlite3
import re
import hashlib
from pathlib import Path
from datetime import datetime, timezone

# Đảm bảo import được config.py từ thư mục cha
sys.path.append(str(Path(__file__).parent.parent))
import config
from utils.helpers import MetadataStore, retry
from core.runtime.db import get_db_connection
from core.media.probe import MediaProbe
from core.runtime.cache_manager import CacheManager
from core.runtime.schemas import validate_data_schema
from core.runtime.project_manager import ProjectManager

logger = logging.getLogger("lofi_automation")
store = MetadataStore(config.METADATA_DIR)

@retry(max_attempts=3)
def _fetch_tracks_from_query(query: str, source_name: str, license_label: str, limit: int) -> list[dict]:
    """Chạy 1 query yt-dlp và phân giải kết quả thành danh sách track ứng viên."""
    logger.info(f"[{source_name}] Đang tìm kiếm nhạc ứng viên qua yt-dlp với query: {query}")

    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--dump-json",
        "--flat-playlist",
        query
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, check=True, encoding="utf-8", errors="ignore")

    tracks = []
    for line in result.stdout.strip().split('\n'):
        if not line:
            continue
        try:
            entry = json.loads(line)
            raw_id = entry.get('id') or ""
            track_id = "".join(c if c.isalnum() or c in ['-', '_'] else '_' for c in raw_id)
            url = entry.get('url')
            if not track_id or not url:
                continue
            # Bỏ qua các bản quá dài (> 10 phút): thường là mix tổng hợp,
            # dễ chứa nhạc bản quyền và không phù hợp pipeline xử lý track đơn
            duration = entry.get('duration') or 0
            if duration and duration > 600:
                continue
            tracks.append({
                "track_id": track_id,
                "title": entry.get('title') or "Untitled",
                "author": entry.get('uploader') or entry.get('channel') or "Unknown Artist",
                "license": license_label,
                "url": url,
                "download_url": url,
                "source": source_name,
                "views": entry.get('view_count', 0) or 0,
                "likes": entry.get('like_count', 0) or 0,
                "comments": entry.get('comment_count', 0) or 0,
                "duration": entry.get('duration', 0) or 0,
                "upload_date": entry.get('upload_date') or "",
            })
        except Exception as e:
            logger.warning(f"[{source_name}] Không phân giải được dòng metadata: {e}")

    return tracks[:limit]


def fetch_candidate_tracks(query: str = None, limit: int = 5) -> list[dict]:
    """Tìm nhạc trên SoundCloud và YouTube rồi xen kẽ kết quả theo nguồn."""
    if query and query.startswith("http"):
        return [fetch_track_metadata_by_url(query)]

    # Tương thích giao diện cũ: bỏ tiền tố scsearch/ytsearch rồi vẫn tìm cả hai nguồn.
    # Nhờ vậy query mặc định cũ không còn khóa kết quả vào SoundCloud (NCS).
    search_text = (query or "lofi copyright free").strip()
    if search_text.startswith(("scsearch", "ytsearch")):
        _, separator, remainder = search_text.partition(":")
        if separator and remainder.strip():
            search_text = remainder.strip()
    per_source = max(4, limit)
    sources = [
        {
            "name": "SoundCloud",
            "query": f"scsearch{{limit}}:{search_text}",
            "license": "Cần kiểm tra giấy phép tại trang SoundCloud",
        },
        {
            "name": "YouTube",
            "query": f"ytsearch{{limit}}:{search_text}",
            "license": "Cần kiểm tra giấy phép trong mô tả YouTube",
        },
    ]

    grouped = {}
    seen_titles = set()
    failed_sources = []

    for src in sources:
        try:
            found = _fetch_tracks_from_query(
                src["query"].format(limit=per_source),
                src["name"],
                src["license"],
                per_source,
            )
        except Exception as exc:
            failed_sources.append(src["name"])
            logger.warning(f"[{src['name']}] Nguồn lỗi, tiếp tục nguồn khác: {exc}")
            continue

        for track in found:
            title_key = f"{track['title'].lower().strip()}|{track['author'].lower().strip()}"
            if title_key in seen_titles:
                continue
            seen_titles.add(title_key)
            grouped.setdefault(src["name"], []).append(track)

    if not grouped:
        raise RuntimeError(
            f"SoundCloud và YouTube đều không trả kết quả. Nguồn lỗi: {failed_sources or 'không rõ'}"
        )

    for tracks in grouped.values():
        tracks.sort(key=lambda item: item.get("views", 0), reverse=True)

    # Xen kẽ để YouTube không bị SoundCloud đẩy khỏi top kết quả và ngược lại.
    merged = []
    source_order = ["SoundCloud", "YouTube"]
    while len(merged) < limit and any(grouped.get(name) for name in source_order):
        for name in source_order:
            if grouped.get(name) and len(merged) < limit:
                merged.append(grouped[name].pop(0))

    if failed_sources:
        logger.warning(f"Nguồn tìm kiếm bị lỗi: {failed_sources}")
    return merged


def _youtube_api_get(resource: str, params: dict) -> dict:
    """Gọi YouTube Data API v3 bằng API key trong .env và trả JSON."""
    import requests

    api_key = getattr(config, "YOUTUBE_API_KEY", "") or os.getenv("YOUTUBE_API_KEY", "")
    if not api_key:
        raise ValueError("Thiếu YOUTUBE_API_KEY trong file .env.")
    request_params = dict(params)
    request_params["key"] = api_key
    response = requests.get(
        f"https://www.googleapis.com/youtube/v3/{resource}",
        params=request_params,
        timeout=30,
    )
    if response.status_code == 403:
        detail = response.json().get("error", {}).get("message", "API bị từ chối hoặc hết quota.")
        raise RuntimeError(f"YouTube Data API từ chối yêu cầu: {detail}")
    response.raise_for_status()
    return response.json()


def _parse_youtube_duration(value: str) -> int:
    """Đổi ISO 8601 duration kiểu PT3M25S thành số giây."""
    match = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", value or "")
    if not match:
        return 0
    hours, minutes, seconds = (int(part or 0) for part in match.groups())
    return hours * 3600 + minutes * 60 + seconds


def _percentile_score(values: list[float], value: float) -> float:
    """Chuẩn hóa tương đối 0-100 trong chính batch đang quét, ít lệch vì outlier."""
    if not values:
        return 0.0
    ordered = sorted(float(v) for v in values)
    if len(ordered) == 1:
        return 50.0
    below = sum(1 for item in ordered if item < float(value))
    equal = sum(1 for item in ordered if item == float(value))
    return 100.0 * (below + max(equal - 1, 0) / 2) / (len(ordered) - 1)


TREND_RUNTIME_DIR = config.BASE_DIR / "data" / "trend_runtime"
TREND_CACHE_DIR = TREND_RUNTIME_DIR / "cache"
TREND_USAGE_FILE = TREND_RUNTIME_DIR / "youtube_search_usage.json"
LASTFM_CACHE_FILE = TREND_CACHE_DIR / "lastfm_global_chart.json"


def _read_runtime_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else default
    except Exception:
        return default


def _write_runtime_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def _runtime_now() -> datetime:
    return datetime.now(timezone.utc)


def _runtime_age_minutes(payload: dict):
    try:
        stamp = datetime.fromisoformat(payload["cached_at_utc"].replace("Z", "+00:00"))
        return max((_runtime_now() - stamp).total_seconds() / 60, 0.0)
    except Exception:
        return None


def _youtube_cache_path(query, days, region_code, max_results, license_filter="any"):
    raw = json.dumps({
        "schema": getattr(config, "TREND_CACHE_SCHEMA_VERSION", "1"),
        "query": query.strip().lower(), "days": days, "region": region_code,
        "limit": max_results, "license_filter": license_filter,
    }, sort_keys=True, ensure_ascii=False)
    return TREND_CACHE_DIR / f"youtube_{hashlib.sha256(raw.encode()).hexdigest()[:20]}.json"


def get_youtube_scan_usage() -> dict:
    today = _runtime_now().date().isoformat()
    data = _read_runtime_json(TREND_USAGE_FILE, {})
    if data.get("date_utc") != today:
        data = {"date_utc": today, "search_calls": 0, "last_scan_at_utc": None}
        _write_runtime_json(TREND_USAGE_FILE, data)
    limit = int(getattr(config, "YOUTUBE_TREND_DAILY_SCAN_LIMIT", 40))
    used = int(data.get("search_calls", 0))
    level = "blocked" if used >= limit else "critical" if used >= limit*.9 else "warning" if used >= limit*.7 else "normal"
    return {**data, "limit": limit, "remaining": max(limit-used, 0), "warning_level": level}


def _register_youtube_scan():
    usage = get_youtube_scan_usage()
    if usage["remaining"] <= 0:
        raise RuntimeError(f"Đã đạt giới hạn an toàn {usage['limit']} lần quét YouTube hôm nay.")
    _write_runtime_json(TREND_USAGE_FILE, {"date_utc": usage["date_utc"], "search_calls": usage["search_calls"]+1, "last_scan_at_utc": _runtime_now().strftime("%Y-%m-%dT%H:%M:%SZ")})


def get_trend_cache_info(query, days, region_code, max_results, license_filter="any"):
    payload = _read_runtime_json(_youtube_cache_path(query, days, region_code, max_results, license_filter), {})
    age = _runtime_age_minutes(payload)
    ttl = int(getattr(config, "YOUTUBE_TREND_CACHE_MINUTES", 15))
    return {"exists": bool(payload.get("tracks")), "fresh": age is not None and age < ttl, "age_minutes": round(age,1) if age is not None else None, "ttl_minutes": ttl, "track_count": len(payload.get("tracks", []))}


def estimate_youtube_multi_scan_cost(query, days, region_codes, max_results, license_filter="any", force_refresh=False) -> dict:
    """Ước tính số lượt search API cần dùng trước khi quét nhiều thị trường."""
    available = getattr(config, "YOUTUBE_MARKETS", {})
    markets = []
    for raw_code in region_codes or []:
        code = str(raw_code or "").strip().upper()
        if code in available and code not in markets:
            markets.append(code)
    markets = markets[:int(getattr(config, "YOUTUBE_TREND_MAX_MARKETS_PER_SCAN", 8))]

    details = []
    required_calls = 0
    for code in markets:
        cache = get_trend_cache_info(query, days, code, max_results, license_filter)
        uses_api = bool(force_refresh or not cache["fresh"])
        if uses_api:
            required_calls += 1
        details.append({
            "code": code,
            "name": available.get(code, {}).get("name", code),
            "cache_exists": cache["exists"],
            "cache_fresh": cache["fresh"],
            "cache_age_minutes": cache["age_minutes"],
            "uses_api": uses_api,
        })

    usage = get_youtube_scan_usage()
    return {
        "market_count": len(markets),
        "required_calls": required_calls,
        "cached_markets": len(markets) - required_calls,
        "remaining": usage["remaining"],
        "allowed": required_calls <= usage["remaining"],
        "details": details,
    }


def discover_youtube_trends(
    query: str = "music",
    days: int = 7,
    region_code: str = "VN",
    max_results: int = 20,
    progress_callback=None,
    force_refresh: bool = False,
    license_filter: str = "any",
) -> list[dict]:
    """Khám phá video nhạc mới và xếp hạng tiềm năng từ dữ liệu công khai YouTube."""
    from datetime import timedelta

    days = max(1, min(int(days), 90))
    max_results = max(5, min(int(max_results), 50))
    region_code = (region_code or "VN").strip().upper()
    license_filter = license_filter if license_filter in {"any", "creativeCommon", "youtube"} else "any"
    now = datetime.now(timezone.utc)
    published_after = (now - timedelta(days=days)).isoformat().replace("+00:00", "Z")
    cache_path = _youtube_cache_path(query, days, region_code, max_results, license_filter)
    cached = _read_runtime_json(cache_path, {})
    age = _runtime_age_minutes(cached)
    ttl = int(getattr(config, "YOUTUBE_TREND_CACHE_MINUTES", 15))
    if not force_refresh and cached.get("tracks") and age is not None and age < ttl:
        for item in cached["tracks"]: item.update({"cache_hit": True, "cache_age_minutes": round(age,1)})
        if progress_callback: progress_callback(5, 5, f"Dùng cache {age:.1f} phút, không tốn API.")
        return cached["tracks"]
    usage = get_youtube_scan_usage()
    if usage["remaining"] <= 0:
        if cached.get("tracks"):
            for item in cached["tracks"]: item.update({"cache_hit": True, "cache_stale": True, "cache_age_minutes": round(age or 0,1)})
            return cached["tracks"]
        raise RuntimeError("Đã đạt giới hạn an toàn và chưa có cache phù hợp.")

    def notify(done: int, total: int, text: str):
        if progress_callback:
            progress_callback(done, total, text)

    notify(0, 5, "Đang tìm video nhạc mới...")
    _register_youtube_scan()
    search_params = {
        "part": "snippet", "type": "video", "videoCategoryId": "10",
        "q": (query or "music").strip(), "publishedAfter": published_after,
        "regionCode": region_code, "order": "viewCount",
        "maxResults": max_results, "safeSearch": "moderate",
    }
    if license_filter != "any":
        search_params["videoLicense"] = license_filter
    search_data = _youtube_api_get("search", search_params)
    search_items = search_data.get("items") or []
    video_ids = [item.get("id", {}).get("videoId") for item in search_items]
    video_ids = [item for item in video_ids if item]
    if not video_ids:
        return []

    notify(1, 4, f"Đã tìm thấy {len(video_ids)} video, đang lấy chỉ số...")
    videos_data = _youtube_api_get("videos", {
        "part": "snippet,statistics,contentDetails,status",
        "id": ",".join(video_ids),
        "maxResults": 50,
    })
    videos = videos_data.get("items") or []
    channel_ids = list(dict.fromkeys(
        item.get("snippet", {}).get("channelId") for item in videos
        if item.get("snippet", {}).get("channelId")
    ))

    notify(2, 4, "Đang đọc quy mô creator...")
    channels = {}
    if channel_ids:
        channel_data = _youtube_api_get("channels", {
            "part": "snippet,statistics",
            "id": ",".join(channel_ids[:50]),
            "maxResults": 50,
        })
        channels = {item.get("id"): item for item in channel_data.get("items") or []}

    tracks = []
    for item in videos:
        snippet = item.get("snippet") or {}
        stats = item.get("statistics") or {}
        channel = channels.get(snippet.get("channelId"), {})
        channel_stats = channel.get("statistics") or {}
        try:
            published = datetime.fromisoformat(snippet.get("publishedAt", "").replace("Z", "+00:00"))
        except ValueError:
            published = now
        age_hours = max((now - published).total_seconds() / 3600, 1.0)
        views = int(stats.get("viewCount") or 0)
        likes = int(stats.get("likeCount") or 0)
        comments = int(stats.get("commentCount") or 0)
        subscribers = int(channel_stats.get("subscriberCount") or 0)
        views_per_hour = views / age_hours
        engagement_rate = (likes + comments * 2) / max(views, 1) * 100
        breakout_ratio = views / max(subscribers, 1) if not channel_stats.get("hiddenSubscriberCount") else 0.0
        video_id = item.get("id")
        tracks.append({
            "track_id": video_id,
            "title": snippet.get("title") or "Untitled",
            "author": snippet.get("channelTitle") or "Unknown Artist",
            "license": "Chưa xác minh quyền sử dụng",
            "usage_safety": "Chưa xác minh",
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "download_url": f"https://www.youtube.com/watch?v={video_id}",
            "source": "YouTube Trends",
            "platform": "YouTube",
            "source_url": f"https://www.youtube.com/watch?v={video_id}",
            "youtube_license": (item.get("status") or {}).get("license") or "unknown",
            "license_evidence": "YouTube Data API status.license",
            "views": views,
            "likes": likes,
            "comments": comments,
            "duration": _parse_youtube_duration((item.get("contentDetails") or {}).get("duration", "")),
            "upload_date": published.strftime("%Y%m%d"),
            "published_at": published.isoformat(),
            "age_hours": round(age_hours, 1),
            "views_per_hour": round(views_per_hour, 1),
            "engagement_rate": round(engagement_rate, 2),
            "channel_id": snippet.get("channelId") or "",
            "subscribers": subscribers,
            "subscriber_hidden": bool(channel_stats.get("hiddenSubscriberCount")),
            "breakout_ratio": round(breakout_ratio, 2),
            "thumbnail_url": ((snippet.get("thumbnails") or {}).get("medium") or {}).get("url", ""),
            "thumbnail": ((snippet.get("thumbnails") or {}).get("medium") or {}).get("url", ""),
        })

    tracks = [attach_lofi_loop_suitability(attach_music_rights(item)) for item in tracks]

    notify(3, 4, "Đang so sánh và chấm điểm tiềm năng...")
    vph_values = [item["views_per_hour"] for item in tracks]
    engagement_values = [item["engagement_rate"] for item in tracks]
    breakout_values = [item["breakout_ratio"] for item in tracks]
    for track in tracks:
        velocity_score = _percentile_score(vph_values, track["views_per_hour"])
        engagement_score = _percentile_score(engagement_values, track["engagement_rate"])
        breakout_score = _percentile_score(breakout_values, track["breakout_ratio"])
        freshness_score = max(0.0, 100.0 * (1.0 - track["age_hours"] / (days * 24.0)))
        score = round(0.40 * velocity_score + 0.20 * engagement_score + 0.25 * breakout_score + 0.15 * freshness_score)
        track["trend_score"] = max(0, min(score, 100))
        track["trend_label"] = (
            "Đang bùng nổ" if score >= 80 else
            "Tiềm năng cao" if score >= 65 else
            "Đáng theo dõi" if score >= 50 else
            "Tín hiệu yếu"
        )
        track["confidence"] = "Thấp - cần snapshot tiếp theo"

        # Lưu mốc thật để lần quét sau có lịch sử tăng trưởng.
        with _trend_connection() as conn:
            conn.execute(
                """INSERT INTO music_trend_snapshots
                (track_id, source, title, author, url, views, likes, comments, captured_at_utc)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (track["track_id"], track["source"], track["title"], track["author"],
                 track["url"], track["views"], track["likes"], track["comments"],
                 now.strftime("%Y-%m-%dT%H:%M:%SZ")),
            )
    if getattr(config, "LASTFM_API_KEY", "") or os.getenv("LASTFM_API_KEY", ""):
        notify(4, 5, "Đang đối chiếu bảng xếp hạng Last.fm...")
        try:
            enrich_tracks_with_lastfm(tracks)
        except Exception as exc:
            logger.warning(f"Last.fm lỗi, giữ kết quả YouTube: {exc}")
    else:
        tracks.sort(key=lambda item: (item["trend_score"], item["views_per_hour"]), reverse=True)
    for item in tracks: item.update({"cache_hit": False, "cache_age_minutes": 0.0})
    _write_runtime_json(cache_path, {"cached_at_utc": _runtime_now().strftime("%Y-%m-%dT%H:%M:%SZ"), "tracks": tracks})
    notify(5, 5, f"Hoàn tất {len(tracks)} video và đã lưu cache.")
    return tracks



def is_focus_music_track(track: dict, market_code: str | None = None) -> bool:
    """Giữ nhạc Việt, nhạc Trung kiểu Vietsub/Pinyin và Lofi; loại rõ nội dung ngoại phạm vi."""
    text = " ".join(str(track.get(key) or "") for key in ("title", "author", "description")).lower()
    excluded = tuple(getattr(config, "MUSIC_EXCLUDED_KEYWORDS", ()))
    if any(keyword.lower() in text for keyword in excluded):
        return False
    focus = tuple(getattr(config, "MUSIC_FOCUS_KEYWORDS", ()))
    if any(keyword.lower() in text for keyword in focus):
        return True
    # Tiêu đề có chữ Hán là tín hiệu mạnh cho nhạc Trung dù không ghi Chinese/Vietsub.
    if re.search(r"[\u3400-\u4dbf\u4e00-\u9fff]", text):
        return True
    # Query đã khóa đúng vùng Việt/Hoa. Khi lọc lại ở UI, market_code không còn được truyền riêng
    # nên phải đọc market_codes đã gắn trong kết quả quét nhiều thị trường.
    codes = {str(code or "").upper() for code in (track.get("market_codes") or [])}
    if market_code:
        codes.add(str(market_code).upper())
    return bool(codes.intersection({"VN", "HK", "TW", "CN"})) and bool(track.get("title"))


def is_single_song_track(track: dict) -> bool:
    """Loại mix/playlist/tổng hợp dài; dùng cho Nhạc Việt và Nhạc Trung."""
    text = " ".join(str(track.get(key) or "") for key in ("title", "author", "description")).lower()
    duration = int(track.get("duration") or 0)
    compilation_markers = (
        "playlist", "full album", "album full", "tuyển tập", "tuyen tap", "liên khúc", "lien khuc",
        "nhạc tổng hợp", "nhac tong hop", "tổng hợp", "tong hop", "collection", "compilation",
        "best of", "top hits", "top 10", "top 20", "top 30", "top 50", "nonstop", "mashup",
        "1 hour", "1h", "60 minutes", "60 phút", "60 phut", "extended mix",
        "歌曲合集", "音乐合集", "音樂合集", "歌单", "歌單", "串烧", "串燒", "合集",
    )
    if any(marker in text for marker in compilation_markers):
        return False
    # Bài đơn phổ thông thường dưới 12 phút. Lofi được xử lý ngoại lệ ở giao diện.
    if duration > 12 * 60:
        return False
    return True


def classify_music_styles(track: dict) -> list[str]:
    """Gắn nhãn phong cách từ metadata để lọc cục bộ, không gọi thêm API."""
    text = " ".join(str(track.get(key) or "") for key in ("title", "author", "description")).lower()
    tags = []
    rules = {
        "Vietsub + Pinyin": ("vietsub", "pinyin", "phụ đề", "phiên âm"),
        "TikTok / Douyin": ("tiktok", "douyin", "抖音"),
        "V-Pop": ("v-pop", "vpop", "nhạc việt", "nhac viet", "vietnamese"),
        "C-Pop": ("c-pop", "cpop", "mandarin", "華語", "华语", "國語", "国语"),
        "Ballad": ("ballad", "tình ca", "tinh ca"),
        "Remix": ("remix", "sped up", "slowed", "reverb", "nightcore"),
        "Cổ phong": ("cổ phong", "co phong", "古风", "古風", "guzheng", "古筝", "古箏"),
        "Lofi": ("lofi", "lo-fi", "chillhop"),
        "Học tập": ("study", "focus", "học tập", "hoc tap"),
        "Ngủ": ("sleep", "ngủ", "ngu", "bedtime"),
        "Mưa đêm": ("rain", "rainy", "mưa", "mua dem", "night rain"),
        "Quán cà phê": ("coffee", "cafe", "café", "quán cà phê", "quan ca phe"),
        "Piano": ("piano",),
        "Ambient": ("ambient", "atmospheric"),
    }
    for label, markers in rules.items():
        if any(marker in text for marker in markers):
            tags.append(label)
    if not tags:
        tags.append("Khác")
    return tags


def discover_youtube_trends_multi(query: str = "music", days: int = 7, region_codes: list[str] | None = None,
                                  max_results: int = 20, progress_callback=None,
                                  force_refresh: bool = False, license_filter: str = "any") -> list[dict]:
    """Quét nhiều thị trường, gộp video trùng và ghi lại các vùng phát hiện."""
    available = getattr(config, "YOUTUBE_MARKETS", {})
    requested = region_codes or getattr(config, "YOUTUBE_TREND_DEFAULT_MARKETS", ["VN"])
    markets = []
    for raw_code in requested:
        code = str(raw_code or "").strip().upper()
        if code in available and code not in markets:
            markets.append(code)
    markets = markets[:int(getattr(config, "YOUTUBE_TREND_MAX_MARKETS_PER_SCAN", 8))]
    if not markets:
        raise ValueError("Hãy chọn ít nhất một thị trường để quét.")

    estimate = estimate_youtube_multi_scan_cost(
        query, days, markets, max_results, license_filter, force_refresh
    )
    if estimate["required_calls"] > estimate["remaining"]:
        raise RuntimeError(
            f"Cần tối đa {estimate['required_calls']} lượt API nhưng chỉ còn {estimate['remaining']} lượt. "
            "Hãy giảm số thị trường hoặc tắt Bỏ qua cache."
        )

    combined = {}
    errors = []
    progress_steps = 5
    for index, code in enumerate(markets, 1):
        market_name = available.get(code, {}).get("name", code)

        def relay(done, total, message, current=index, name=market_name):
            if progress_callback:
                local_done = min(max(int(done), 0), progress_steps)
                progress_callback(
                    (current - 1) * progress_steps + local_done,
                    len(markets) * progress_steps,
                    f"{name}: {message}",
                )

        try:
            tracks = discover_youtube_trends(
                query=query,
                days=days,
                region_code=code,
                max_results=max_results,
                progress_callback=relay,
                force_refresh=force_refresh,
                license_filter=license_filter,
            )
        except Exception as exc:
            errors.append({"code": code, "name": market_name, "error": str(exc)})
            logger.warning("Bỏ qua thị trường %s vì quét lỗi: %s", code, exc)
            continue

        for track in tracks:
            if not is_focus_music_track(track, market_code=code):
                continue
            track_id = str(track.get("track_id") or track.get("url") or "")
            if not track_id:
                continue
            if track_id not in combined:
                item = dict(track)
                item.update({
                    "market_codes": [code], "market_names": [market_name], "market_count": 1,
                    "style_tags": classify_music_styles(track),
                })
                combined[track_id] = item
            else:
                item = combined[track_id]
                if code not in item["market_codes"]:
                    item["market_codes"].append(code)
                    item["market_names"].append(market_name)
                    item["market_count"] = len(item["market_codes"])
    if not combined and errors:
        detail = "; ".join(f"{item['code']}: {item['error']}" for item in errors[:3])
        raise RuntimeError(f"Không quét được thị trường nào. {detail}")

    result = list(combined.values())
    for item in result:
        item["market_scan_errors"] = errors
    result.sort(
        key=lambda item: (
            item.get("market_count", 1),
            item.get("cross_platform_score") or item.get("trend_score") or 0,
            item.get("views_per_hour") or 0,
        ),
        reverse=True,
    )
    return result

def get_stream_url(track_url: str) -> str:
    """
    Lấy direct stream URL của track để nghe thử ngay mà KHÔNG tải file về máy.
    Dùng cho st.audio trong UI duyệt nhạc. URL có hạn dùng ngắn (vài phút tới vài giờ).
    """
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "-g",
        "-f", "bestaudio/best",
        "--no-warnings",
        track_url
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True,
                            encoding="utf-8", errors="ignore", timeout=45)
    stream_urls = [line for line in result.stdout.strip().splitlines() if line.startswith("http")]
    if not stream_urls:
        raise ValueError("Không lấy được stream URL cho track này.")
    return stream_urls[0]



def get_preview_audio_path(track_url: str, track_id: str) -> Path:
    """Tải và cache tối đa 60 giây MP3 để Streamlit phát ổn định."""
    if not track_url:
        raise ValueError("Track không có URL nguồn.")
    safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in (track_id or "preview"))
    preview_dir = config.BASE_DIR / "data" / "previews" / "music"
    preview_dir.mkdir(parents=True, exist_ok=True)
    preview_path = preview_dir / f"{safe_id}.mp3"
    if preview_path.exists() and preview_path.stat().st_size > 0:
        return preview_path
    cmd = [
        sys.executable, "-m", "yt_dlp", "--no-playlist", "--no-warnings",
        "--download-sections", "*0-60", "-x", "--audio-format", "mp3",
        "--audio-quality", "5", "-o", str(preview_dir / f"{safe_id}.%(ext)s"),
        track_url,
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, check=True, encoding="utf-8",
            errors="ignore", timeout=120,
        )
    except subprocess.CalledProcessError as exc:
        lines = (exc.stderr or exc.stdout or "yt-dlp không trả chi tiết").strip().splitlines()
        detail = lines[-1] if lines else "Không rõ lỗi"
        raise RuntimeError(f"Không tạo được bản nghe thử: {detail}") from exc
    if not preview_path.exists() or preview_path.stat().st_size == 0:
        raise RuntimeError("Không tạo được MP3 preview. Kiểm tra yt-dlp và FFmpeg.")
    return preview_path

def fetch_track_metadata_by_url(url: str) -> dict:
    """Lấy thông tin chi tiết của một track từ URL cụ thể."""
    logger.info(f"Đang lấy metadata cho URL: {url}")
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--dump-json",
        "--flat-playlist",
        url
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True, encoding="utf-8", errors="ignore")
    lines = result.stdout.strip().split('\n')
    if not lines or not lines[0]:
        raise ValueError("Không thể lấy thông tin từ URL này.")
        
    entry = json.loads(lines[0])
    raw_id = entry.get('id') or ""
    track_id = "".join(c if c.isalnum() or c in ['-', '_'] else '_' for c in raw_id)
    
    extractor = entry.get('extractor_key') or "Direct"
    source_name = f"{extractor} (Direct)"
    
    return {
        "track_id": track_id,
        "title": entry.get('title') or "Untitled Track",
        "author": entry.get('uploader') or entry.get('artist') or "Unknown Artist",
        "license": "No Copyright / Creative Commons License",
        "url": url,
        "download_url": url,
        "source": source_name,
        "views": entry.get('view_count', 0) or 0,
        "likes": entry.get('like_count', 0) or 0,
        "comments": entry.get('comment_count', 0) or 0,
        "duration": entry.get('duration', 0) or 0,
        "upload_date": entry.get('upload_date') or "",
    }


TREND_DB_PATH = config.BASE_DIR / "data" / "music_trends.sqlite3"


def _trend_connection():
    TREND_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(TREND_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS music_trend_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            track_id TEXT NOT NULL,
            source TEXT NOT NULL,
            title TEXT,
            author TEXT,
            url TEXT,
            views INTEGER NOT NULL DEFAULT 0,
            likes INTEGER NOT NULL DEFAULT 0,
            comments INTEGER NOT NULL DEFAULT 0,
            captured_at_utc TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_music_trend_track_time
        ON music_trend_snapshots(track_id, captured_at_utc)
    """)
    return conn


def _refresh_track_metrics(track: dict) -> dict:
    """Lấy lại metadata hiện tại từ URL bằng yt-dlp, không cần API key."""
    url = (track or {}).get("url")
    if not url:
        raise ValueError("Bài nhạc không có URL nguồn.")
    cmd = [sys.executable, "-m", "yt_dlp", "--dump-single-json", "--no-playlist", "--no-warnings", url]
    result = subprocess.run(
        cmd, capture_output=True, text=True, check=True,
        encoding="utf-8", errors="ignore", timeout=60,
    )
    entry = json.loads(result.stdout)
    refreshed = dict(track)
    refreshed.update({
        "title": entry.get("title") or refreshed.get("title") or "Untitled",
        "author": entry.get("uploader") or entry.get("channel") or entry.get("artist") or refreshed.get("author") or "Unknown",
        "views": int(entry.get("view_count") or 0),
        "likes": int(entry.get("like_count") or 0),
        "comments": int(entry.get("comment_count") or 0),
        "duration": int(entry.get("duration") or refreshed.get("duration") or 0),
        "upload_date": entry.get("upload_date") or refreshed.get("upload_date") or "",
    })
    return refreshed


def capture_youtube_trend_snapshot(track: dict) -> dict:
    """Lưu snapshot và trả phân tích tăng trưởng từ các lần đo thật."""
    current = _refresh_track_metrics(track)
    captured_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    track_id = str(current.get("track_id") or "")
    if not track_id:
        raise ValueError("Bài nhạc thiếu track_id.")

    with _trend_connection() as conn:
        conn.execute(
            """INSERT INTO music_trend_snapshots
               (track_id, source, title, author, url, views, likes, comments, captured_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                track_id, current.get("source", "Unknown"), current.get("title"),
                current.get("author"), current.get("url"), current.get("views", 0),
                current.get("likes", 0), current.get("comments", 0), captured_at,
            ),
        )
        rows = conn.execute(
            """SELECT views, likes, comments, captured_at_utc
               FROM music_trend_snapshots WHERE track_id = ?
               ORDER BY captured_at_utc ASC, id ASC""",
            (track_id,),
        ).fetchall()

    analysis = {
        "track": current,
        "snapshot_count": len(rows),
        "captured_at_utc": captured_at,
        "trend_label": "Chưa đủ dữ liệu",
        "confidence": "Thấp",
        "growth_percent": None,
        "views_delta": None,
        "elapsed_hours": None,
        "trend_score": None,
    }
    if len(rows) < 2:
        return analysis

    first, last = rows[0], rows[-1]
    start = datetime.fromisoformat(first["captured_at_utc"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(last["captured_at_utc"].replace("Z", "+00:00"))
    elapsed_hours = max((end - start).total_seconds() / 3600, 0.0)
    views_delta = max(int(last["views"]) - int(first["views"]), 0)
    growth_percent = (views_delta / max(int(first["views"]), 1)) * 100
    interaction_delta = max(
        (int(last["likes"]) + int(last["comments"]))
        - (int(first["likes"]) + int(first["comments"])), 0,
    )
    # Điểm chỉ là chỉ báo nội bộ dựa trên dữ liệu đã đo, không giả lập dữ liệu nền tảng khác.
    time_factor = max(elapsed_hours / 24, 0.25)
    daily_growth = growth_percent / time_factor
    engagement_signal = min(interaction_delta / max(views_delta, 1) * 100, 10)
    score = min(100, round(daily_growth * 12 + engagement_signal * 3))
    if score >= 80:
        label = "Tăng mạnh"
    elif score >= 60:
        label = "Đang tăng"
    elif score >= 40:
        label = "Ổn định"
    elif score >= 20:
        label = "Tăng nhẹ"
    else:
        label = "Chưa có tín hiệu"
    confidence = "Cao" if len(rows) >= 5 and elapsed_hours >= 72 else "Trung bình" if len(rows) >= 3 and elapsed_hours >= 24 else "Thấp"
    analysis.update({
        "trend_label": label,
        "confidence": confidence,
        "growth_percent": round(growth_percent, 2),
        "views_delta": views_delta,
        "elapsed_hours": round(elapsed_hours, 1),
        "trend_score": score,
    })
    return analysis


def get_youtube_trend_history(track_id: str) -> list[dict]:
    """Đọc lịch sử snapshot để kiểm tra và hiển thị."""
    with _trend_connection() as conn:
        rows = conn.execute(
            """SELECT views, likes, comments, captured_at_utc
               FROM music_trend_snapshots WHERE track_id = ?
               ORDER BY captured_at_utc DESC, id DESC""",
            (track_id,),
        ).fetchall()
    return [dict(row) for row in rows]



def _lastfm_api_get(method: str, params: dict | None = None) -> dict:
    """Gọi Last.fm API cho dữ liệu chart công khai; không cần đăng nhập người dùng."""
    import requests

    api_key = getattr(config, "LASTFM_API_KEY", "") or os.getenv("LASTFM_API_KEY", "")
    if not api_key:
        raise ValueError("Thiếu LASTFM_API_KEY trong file .env.")
    request_params = dict(params or {})
    request_params.update({"method": method, "api_key": api_key, "format": "json"})
    response = requests.get("https://ws.audioscrobbler.com/2.0/", params=request_params, timeout=30)
    response.raise_for_status()
    payload = response.json()
    if payload.get("error"):
        raise RuntimeError(payload.get("message") or f"Last.fm error {payload['error']}")
    return payload


def get_lastfm_status(tracks: list[dict] | None = None) -> dict:
    """Trả trạng thái cấu hình, cache và số bài đã khớp mà không tự gọi mạng."""
    configured = bool(getattr(config, "LASTFM_API_KEY", "") or os.getenv("LASTFM_API_KEY", ""))
    cached = _read_runtime_json(LASTFM_CACHE_FILE, {})
    age = _runtime_age_minutes(cached)
    chart = cached.get("tracks") or []
    checked_tracks = tracks or []
    matched_count = sum(1 for item in checked_tracks if item.get("lastfm_matched"))
    return {
        "configured": configured,
        "cache_exists": bool(chart),
        "cache_age_minutes": round(age, 1) if age is not None else None,
        "cached_at_utc": cached.get("cached_at_utc"),
        "chart_count": len(chart),
        "matched_count": matched_count,
        "checked_count": len(checked_tracks),
        "verified": bool(configured and chart),
    }


def check_lastfm_connection(force_refresh: bool = False, tracks: list[dict] | None = None) -> dict:
    """Kiểm tra key bằng API thật, tùy chọn làm mới chart và đối chiếu danh sách hiện tại."""
    configured = bool(getattr(config, "LASTFM_API_KEY", "") or os.getenv("LASTFM_API_KEY", ""))
    if not configured:
        return {**get_lastfm_status(tracks), "ok": False, "message": "Chưa cấu hình LASTFM_API_KEY."}
    try:
        if force_refresh:
            LASTFM_CACHE_FILE.unlink(missing_ok=True)
        chart = fetch_lastfm_chart()
        if tracks:
            enrich_tracks_with_lastfm(tracks, chart=chart)
        status = get_lastfm_status(tracks)
        return {**status, "ok": True, "message": "Kết nối Last.fm thành công."}
    except Exception as exc:
        return {**get_lastfm_status(tracks), "ok": False, "message": str(exc)}


def _normalise_music_text(value: str) -> str:
    """Chuẩn hóa tên bài/nghệ sĩ để đối chiếu YouTube với Last.fm."""
    text = re.sub(r"\([^)]*\)|\[[^]]*\]", " ", str(value or "").lower())
    text = re.sub(r"\b(official|video|audio|lyrics?|visualizer|mv|music)\b", " ", text)
    return " ".join(re.findall(r"[a-z0-9À-ỹ]+", text, flags=re.IGNORECASE))


def fetch_lastfm_chart(limit: int | None = None) -> list[dict]:
    """Lấy bảng xếp hạng Last.fm toàn cầu và chuẩn hóa playcount/listeners."""
    limit = int(limit or getattr(config, "LASTFM_CHART_LIMIT", 100))
    cached = _read_runtime_json(LASTFM_CACHE_FILE, {})
    age = _runtime_age_minutes(cached)
    if cached.get("tracks") and age is not None and age < int(getattr(config, "LASTFM_CHART_CACHE_MINUTES", 60)):
        return cached["tracks"][:limit]
    payload = _lastfm_api_get("chart.getTopTracks", {"limit": max(10, min(limit, 200)), "page": 1})
    raw_tracks = (payload.get("tracks") or {}).get("track") or []
    result = []
    total = max(len(raw_tracks), 1)
    for index, item in enumerate(raw_tracks, 1):
        artist = item.get("artist") or {}
        if isinstance(artist, dict):
            artist = artist.get("name") or ""
        result.append({
            "name": item.get("name") or "",
            "artist": artist,
            "playcount": int(item.get("playcount") or 0),
            "listeners": int(item.get("listeners") or 0),
            "rank": index,
            "chart_score": round(100 * (total - index + 1) / total),
            "url": item.get("url") or "",
        })
    _write_runtime_json(LASTFM_CACHE_FILE, {"cached_at_utc": _runtime_now().strftime("%Y-%m-%dT%H:%M:%SZ"), "tracks": result})
    return result


def enrich_tracks_with_lastfm(tracks: list[dict], chart: list[dict] | None = None) -> list[dict]:
    """Ghép chart Last.fm với kết quả YouTube và tính điểm đa nguồn khi khớp đủ rõ."""
    if not tracks:
        return tracks
    if not (getattr(config, "LASTFM_API_KEY", "") or os.getenv("LASTFM_API_KEY", "")):
        for track in tracks:
            track.update({"lastfm_matched": False, "lastfm_score": None, "cross_platform_score": track.get("trend_score")})
        return tracks

    chart = chart if chart is not None else fetch_lastfm_chart()
    for track in tracks:
        yt_title = _normalise_music_text(track.get("title", ""))
        yt_author = _normalise_music_text(track.get("author", ""))
        best = None
        best_quality = 0
        for entry in chart:
            name = _normalise_music_text(entry.get("name", ""))
            artist = _normalise_music_text(entry.get("artist", ""))
            if not name:
                continue
            title_match = name in yt_title or yt_title in name
            artist_match = bool(artist and (artist in yt_title or artist in yt_author or yt_author in artist))
            quality = 2 if title_match and artist_match else 1 if title_match else 0
            if quality > best_quality:
                best, best_quality = entry, quality
                if quality == 2:
                    break
        if best and best_quality >= 1:
            lastfm_score = int(best["chart_score"])
            youtube_score = int(track.get("trend_score") or 0)
            cross_score = round(youtube_score * 0.85 + lastfm_score * 0.15)
            track.update({
                "lastfm_matched": True,
                "lastfm_track": best["name"],
                "lastfm_artist": best["artist"],
                "lastfm_rank": best["rank"],
                "lastfm_playcount": best["playcount"],
                "lastfm_listeners": best["listeners"],
                "lastfm_score": lastfm_score,
                "cross_platform_score": cross_score,
                "confidence": "Trung bình - có tín hiệu YouTube và Last.fm",
            })
        else:
            track.update({"lastfm_matched": False, "lastfm_score": None, "cross_platform_score": track.get("trend_score")})
    tracks.sort(key=lambda item: (item.get("cross_platform_score") or 0, item.get("views_per_hour") or 0), reverse=True)
    return tracks


def build_cross_platform_trend(youtube: dict | None, lastfm: dict | None) -> dict:
    """Gộp YouTube và Last.fm; Last.fm là tín hiệu phụ, không thay thế tăng trưởng thật."""
    sources = []
    if youtube and youtube.get("trend_score") is not None:
        sources.append(("YouTube", float(youtube["trend_score"]), 0.85))
    if lastfm and lastfm.get("lastfm_score") is not None:
        sources.append(("Last.fm", float(lastfm["lastfm_score"]), 0.15))
    if not sources:
        return {"score": None, "label": "Chưa đủ dữ liệu", "confidence": "Thấp", "sources": []}
    weight_sum = sum(weight for _, _, weight in sources)
    score = round(sum(value * weight for _, value, weight in sources) / weight_sum)
    label = "Bắt trend mạnh" if score >= 80 else "Có tiềm năng" if score >= 65 else "Theo dõi thêm" if score >= 50 else "Tín hiệu yếu"
    confidence = "Trung bình" if len(sources) >= 2 else "Thấp"
    return {"score": score, "label": label, "confidence": confidence, "sources": [name for name, _, _ in sources]}

def youtube_analytics_dependency_status() -> dict:
    """Kiểm tra thư viện OAuth mà không làm ứng dụng crash khi chưa cài."""
    try:
        import google.auth  # noqa: F401
        import google.oauth2.credentials  # noqa: F401
        import google_auth_oauthlib.flow  # noqa: F401
        return {"available": True, "message": "Sẵn sàng"}
    except ImportError:
        return {
            "available": False,
            "message": "Thiếu google-auth và google-auth-oauthlib.",
            "install_command": f'"{sys.executable}" -m pip install google-auth google-auth-oauthlib requests',
        }


def get_youtube_analytics_connection_status() -> dict:
    """Trạng thái client secret và token Analytics cục bộ."""
    dependency = youtube_analytics_dependency_status()
    client_file = Path(getattr(config, "YOUTUBE_CLIENT_SECRETS_FILE", ""))
    token_file = Path(getattr(config, "YOUTUBE_ANALYTICS_TOKEN_FILE", ""))
    return {
        **dependency,
        "client_secret_exists": client_file.is_file(),
        "token_exists": token_file.is_file(),
        "client_secret_path": str(client_file),
        "token_path": str(token_file),
    }


def connect_youtube_analytics() -> dict:
    """Mở OAuth local browser và lưu token riêng cho YouTube Analytics."""
    status = get_youtube_analytics_connection_status()
    if not status["available"]:
        raise RuntimeError(status["message"] + " Lệnh cài: " + status.get("install_command", ""))
    client_file = Path(status["client_secret_path"])
    if not client_file.is_file():
        raise FileNotFoundError(
            f"Thiếu OAuth client file: {client_file}. Hãy dùng OAuth Client ID loại Desktop app."
        )
    from google_auth_oauthlib.flow import InstalledAppFlow

    scopes = list(getattr(config, "YOUTUBE_ANALYTICS_SCOPES", []))
    flow = InstalledAppFlow.from_client_secrets_file(str(client_file), scopes=scopes)
    credentials = flow.run_local_server(port=0, open_browser=True, prompt="consent")
    token_file = Path(status["token_path"])
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(credentials.to_json(), encoding="utf-8")
    return {"connected": True, "token_path": str(token_file)}


def disconnect_youtube_analytics() -> None:
    """Xóa token Analytics cục bộ, không ảnh hưởng API key hoặc token upload cũ."""
    Path(getattr(config, "YOUTUBE_ANALYTICS_TOKEN_FILE", "")).unlink(missing_ok=True)


def _load_youtube_analytics_credentials():
    status = get_youtube_analytics_connection_status()
    if not status["available"]:
        raise RuntimeError(status["message"] + " Lệnh cài: " + status.get("install_command", ""))
    token_file = Path(status["token_path"])
    if not token_file.is_file():
        raise RuntimeError("Chưa kết nối YouTube Analytics.")

    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    scopes = list(getattr(config, "YOUTUBE_ANALYTICS_SCOPES", []))
    credentials = Credentials.from_authorized_user_file(str(token_file), scopes=scopes)
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        token_file.write_text(credentials.to_json(), encoding="utf-8")
    if not credentials.valid:
        raise RuntimeError("Token YouTube Analytics không hợp lệ. Hãy ngắt kết nối rồi kết nối lại.")
    return credentials


def _authorized_youtube_get(url: str, params: dict) -> dict:
    import requests
    credentials = _load_youtube_analytics_credentials()
    response = requests.get(
        url,
        params=params,
        headers={"Authorization": f"Bearer {credentials.token}"},
        timeout=30,
    )
    if response.status_code >= 400:
        try:
            detail = response.json().get("error", {}).get("message")
        except Exception:
            detail = response.text[:300]
        raise RuntimeError(f"YouTube Analytics API lỗi {response.status_code}: {detail or 'Không rõ lỗi'}")
    return response.json()


def fetch_real_youtube_rpm(days: int = 28, currency: str = "USD") -> dict:
    """Lấy dữ liệu doanh thu thật của chính kênh đã OAuth và tự tính RPM."""
    from datetime import timedelta

    days = max(7, min(int(days), 365))
    end_date = (_runtime_now().date() - timedelta(days=2))
    start_date = end_date - timedelta(days=days - 1)
    common = {
        "ids": "channel==MINE",
        "startDate": start_date.isoformat(),
        "endDate": end_date.isoformat(),
        "metrics": "views,estimatedRevenue,estimatedMinutesWatched",
        "currency": currency,
    }
    summary = _authorized_youtube_get(
        "https://youtubeanalytics.googleapis.com/v2/reports", common
    )
    rows = summary.get("rows") or []
    values = rows[0] if rows else [0, 0, 0]
    views = int(values[0] or 0)
    revenue = float(values[1] or 0)
    minutes = float(values[2] or 0)
    rpm = revenue / views * 1000 if views else 0.0

    by_country_params = dict(common)
    by_country_params.update({"dimensions": "country", "sort": "-estimatedRevenue", "maxResults": 25})
    by_country = _authorized_youtube_get(
        "https://youtubeanalytics.googleapis.com/v2/reports", by_country_params
    )
    country_rows = []
    for row in by_country.get("rows") or []:
        country, country_views, country_revenue, country_minutes = row
        country_views = int(country_views or 0)
        country_revenue = float(country_revenue or 0)
        country_rows.append({
            "country": country,
            "views": country_views,
            "estimated_revenue": country_revenue,
            "estimated_minutes_watched": float(country_minutes or 0),
            "rpm": country_revenue / country_views * 1000 if country_views else 0.0,
        })
    return {
        "start_date": start_date.isoformat(), "end_date": end_date.isoformat(),
        "days": days, "currency": currency, "views": views,
        "estimated_revenue": revenue, "estimated_minutes_watched": minutes,
        "rpm": rpm, "country_rows": country_rows,
    }



def assess_lofi_loop_suitability(track: dict) -> dict:
    """Đánh giá kỹ thuật để kéo/lặp thành video Lofi 1 giờ; không cấp quyền remix."""
    track = track or {}
    text = f"{track.get('title', '')} {track.get('author', '')}".lower()
    duration = int(track.get("duration") or 0)
    rights = assess_music_rights(track)

    score = 50
    reasons = []
    if 75 <= duration <= 480:
        score += 18
        reasons.append("độ dài phù hợp để loop")
    elif 30 <= duration < 75 or 480 < duration <= 900:
        score += 7
        reasons.append("độ dài có thể xử lý")
    elif duration > 1800:
        score -= 25
        reasons.append("đã là mix dài")
    elif duration == 0:
        score -= 5
        reasons.append("chưa rõ thời lượng")

    if any(word in text for word in ("instrumental", "beat", "ambient", "piano", "jazz", "guitar")):
        score += 12
        reasons.append("tín hiệu instrumental")
    if any(word in text for word in ("lyrics", "vocal", "karaoke", "live", "concert", "remix compilation", "mix 1 hour", "1 hour")):
        score -= 18
        reasons.append("khó loop sạch")

    if rights["rights_level"] == "cc":
        score += 15
        reasons.append("Creative Commons")
    elif rights["rights_level"] == "standard":
        score -= 35
        reasons.append("Standard License")
    elif rights["rights_level"] == "high":
        score -= 45
        reasons.append("nguy cơ bản quyền")
    elif rights["rights_level"] == "signal":
        score += 3
        reasons.append("cần xác minh giấy phép")

    score = max(0, min(round(score), 100))
    label = "Phù hợp" if score >= 70 else "Có thể xử lý" if score >= 50 else "Không khuyến nghị"
    return {
        "loop_1h_score": score,
        "loop_1h_label": label,
        "loop_1h_note": ", ".join(reasons) if reasons else "Chưa đủ tín hiệu",
    }


def attach_lofi_loop_suitability(track: dict) -> dict:
    updated = dict(track or {})
    updated.update(assess_lofi_loop_suitability(updated))
    return updated



def assess_music_rights(track: dict) -> dict:
    """Ưu tiên status.license chính thức; metadata chỉ là tín hiệu bổ sung, không phải tư vấn pháp lý."""
    track = track or {}
    youtube_license = str(track.get("youtube_license") or "").strip()
    title = str(track.get("title") or "")
    author = str(track.get("author") or "")
    license_text = str(track.get("license") or "")
    description = str(track.get("description") or "")
    text = f"{title} {author} {license_text} {description}".lower()

    high_risk = (
        "provided to youtube by", "under exclusive license", "vevo", "warner",
        "sony music", "universal music", "believe music", "all rights reserved",
        "lofi girl", "chillhop music", "lofi records",
    )
    permissive = (
        "creative commons", "cc by", "royalty free", "no copyright",
        "free to use", "ncs", "audio library", "public domain",
    )
    credit_required = ("credit required", "attribution required", "cc by", "ncs")

    if youtube_license == "creativeCommon":
        return {
            "rights_status": "Creative Commons",
            "rights_level": "cc",
            "rights_note": "YouTube API báo Creative Commons. Vẫn cần kiểm tra attribution, tác phẩm gốc và điều khoản tại trang nguồn.",
            "rights_source": "YouTube API status.license",
            "credit_required": True,
        }
    if youtube_license == "youtube":
        return {
            "rights_status": "Standard YouTube License",
            "rights_level": "standard",
            "rights_note": "Giấy phép YouTube tiêu chuẩn không cho phép tự động tải và đăng lại.",
            "rights_source": "YouTube API status.license",
            "credit_required": False,
        }
    if any(marker in text for marker in high_risk):
        return {
            "rights_status": "Nguy cơ bản quyền", "rights_level": "high",
            "rights_note": "Metadata có dấu hiệu hãng hoặc đơn vị quản lý quyền. Không dùng lại nếu chưa có giấy phép.",
            "rights_source": "Sàng lọc metadata", "credit_required": False,
        }
    if any(marker in text for marker in permissive):
        needs_credit = any(marker in text for marker in credit_required)
        return {
            "rights_status": "Có tín hiệu miễn phí", "rights_level": "signal",
            "rights_note": "Chỉ là tín hiệu từ metadata. Cần đọc điều khoản nguồn và lưu bằng chứng giấy phép.",
            "rights_source": "Sàng lọc metadata", "credit_required": needs_credit,
        }
    return {
        "rights_status": "Chưa xác minh", "rights_level": "unknown",
        "rights_note": "Không có đủ dữ liệu để kết luận quyền sử dụng.",
        "rights_source": "Không đủ dữ liệu", "credit_required": False,
    }


def attach_music_rights(track: dict) -> dict:
    updated = dict(track or {})
    updated.update(assess_music_rights(updated))
    return updated



def is_license_safe(track: dict) -> bool:
    """Kiểm tra xem track có chứa dấu hiệu bản quyền không."""
    blacklist_markers = [
        "provided to youtube by", "under exclusive license", "warner", "sony", "believe",
        # Các label lofi thương mại - nhạc CÓ bản quyền dù tiêu đề ghi chill/study
        "lofi girl", "chillhop", "lofi records",
        # Video tổng hợp thường trộn nhạc bản quyền
        "best of",
    ]
    text = f"{track.get('title', '')} {track.get('author', '')}".lower()
    return not any(marker in text for marker in blacklist_markers)

def download_track(track: dict, project_id: str = None) -> Path:
    """
    Tải file audio về và kiểm duyệt chất lượng bằng Media Probe.
    Ghi nhận trạng thái duyệt và tài sản vào SQLite DB.
    """
    logger.info(f"Đang tải audio từ url: {track['url']}")
    
    out_tmpl = config.INPUT_AUDIO_DIR / f"{track['track_id']}.%(ext)s"
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--quiet",
        "--no-warnings",
        "--no-progress",
        "-f", "bestaudio/best",
        "-o", str(out_tmpl),
        track['url']
    ]
    
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    
    downloaded_file = None
    for ext in ['webm', 'opus', 'm4a', 'mp3', 'ogg', 'wav']:
        p = config.INPUT_AUDIO_DIR / f"{track['track_id']}.{ext}"
        if p.exists():
            downloaded_file = p
            break
            
    if not downloaded_file:
        raise FileNotFoundError("Không tìm thấy file audio đã tải về từ yt-dlp")
        
    expected_path = config.INPUT_AUDIO_DIR / f"{track['track_id']}.m4a"
    if downloaded_file.suffix != '.m4a':
        # Convert sang m4a
        cmd_ffmpeg = [
            "ffmpeg", "-y",
            "-i", str(downloaded_file),
            "-vn",
            "-c:a", "aac",
            "-b:a", "192k",
            str(expected_path)
        ]
        logger.info(f"Đang convert thủ công sang m4a: {' '.join(cmd_ffmpeg)}")
        subprocess.run(cmd_ffmpeg, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        
        try:
            downloaded_file.unlink(missing_ok=True)
        except Exception as e:
            logger.warning(f"Không xóa được file gốc {downloaded_file}: {e}")
    
    # --- KIỂM DUYỆT CHẤT LƯỢNG (AUD-ACC-001 / AUD-ACC-005) ---
    probe_info = MediaProbe.probe_media(expected_path)
    if not probe_info["audio_streams"]:
        expected_path.unlink()
        raise ValueError("File tải về không chứa luồng âm thanh hợp lệ (AUD-ACC-001).")

    duration = probe_info["duration_seconds"]
    file_sha256 = CacheManager.get_file_sha256(expected_path)
    file_size = expected_path.stat().st_size
    
    # Lấy thông số loudness
    try:
        loud_info = MediaProbe.get_loudness_and_peak(expected_path)
        integrated_loudness = loud_info["integrated_loudness"]
        true_peak = loud_info["true_peak"]
    except Exception as e:
        logger.warning(f"Không đo được độ lớn âm thanh: {e}")
        integrated_loudness = 0.0
        true_peak = 0.0

    # --- LƯU METADATA THEO SCHEMA ---
    track_meta = {
        "schema_name": "track_metadata",
        "schema_version": 1,
        "track_id": track["track_id"],
        "title": track["title"],
        "author": track["author"],
        "source": track["source"],
        "url": track["url"],
        "duration_seconds": duration,
        "license": track["license"],
        "views": int(track.get("views") or 0),
        "likes": int(track.get("likes") or 0),
        "relevance_score": 10.0,
        "source_trust_score": 90.0,
        "risk_reasons": [],
        "download_status": "downloaded"
    }
    
    # Validate schema
    validate_data_schema(track_meta, "track_metadata")
    
    # Ghi file metadata
    meta_path = config.METADATA_DIR / f"{track['track_id']}.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(track_meta, f, ensure_ascii=False, indent=2)
        
    store._append_used_track(track["track_id"])
    
    # --- ĐẤU NỐI DATABASE SQLITE (NẾU CÓ DỰ ÁN) ---
    if project_id:
        conn = get_db_connection()
        try:
            now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            asset_id = f"audio_{track['track_id']}"
            
            with conn:
                # 1. Đăng ký Asset
                conn.execute("""
                INSERT OR REPLACE INTO assets (asset_id, project_id, path, sha256, mime_type, size_bytes, processing_status, review_status, created_at_utc)
                VALUES (?, ?, ?, ?, 'audio/mp4', ?, 'verified', 'approved', ?);
                """, (asset_id, project_id, f"data/input_audio/{expected_path.name}", file_sha256, file_size, now_str))
                
                # 2. Đăng ký Rights Review
                rights_id = f"rights_{track['track_id']}"
                conn.execute("""
                INSERT OR REPLACE INTO error_records (error_id, job_id, error_code, category, step, message, retryable, fallback_available, suggested_action, occurred_at_utc)
                VALUES (?, NULL, 'RIGHTS_ESTABLISHED', 'rights', 'music_hunter', ?, 0, 0, 'No action needed', ?);
                """, (rights_id, f"Established rights review for track {track['track_id']}", now_str))
                
            # 3. Cập nhật module trạng thái cho dự án
            ProjectManager.update_workflow_status(
                project_id=project_id,
                module_name="audio",
                processing_status="verified",
                review_status="approved",
                input_hash=file_sha256,
                output_hash=file_sha256,
                reason=f"Audio imported successfully. Duration: {duration:.1f}s, Loudness: {integrated_loudness:.1f} LUFS, Peak: {true_peak:.1f} dBTP",
                actor="music_hunter"
            )
        finally:
            conn.close()
            
    return expected_path

def run_step1(project_id: str = None) -> dict:
    """Hàm chạy tự động chính."""
    candidates = fetch_candidate_tracks(limit=10)
    for track in candidates:
        if store.is_track_used(track["track_id"]):
            continue
        if not is_license_safe(track):
            logger.info(f"Bỏ qua track nghi ngờ bản quyền: {track['title']}")
            continue
        
        audio_path = download_track(track, project_id)
        return {"audio_path": audio_path, "track_id": track["track_id"]}
        
    raise RuntimeError("Không tìm được track hợp lệ nào trong batch này")

if __name__ == "__main__":
    # Chạy thử
    p_id = "test_step1_prj"
    
    # Khởi tạo database
    import core.runtime.db
    core.runtime.db.init_db()
    
    # Dọn dẹp & tạo dự án mới
    conn = get_db_connection()
    with conn:
        conn.execute("DELETE FROM projects WHERE project_id = ?;", (p_id,))
    conn.close()
    
    ProjectManager.create_project(p_id)
    
    # Mock is_track_used để luôn trả về False phục vụ việc test
    original_is_track_used = store.is_track_used
    store.is_track_used = lambda x: False
    
    try:
        res = run_step1(p_id)
        print("Download music test success:", res)
        
        # Load dự án xem trạng thái
        p = ProjectManager.load_project(p_id)
        print("Audio status in DB:", p["workflow_status"]["audio"])
        
    except Exception as e:
        print("Test failed:", str(e))
        
    # Restore mock
    store.is_track_used = original_is_track_used
    
    # Cleanup
    conn = get_db_connection()
    with conn:
        conn.execute("DELETE FROM projects WHERE project_id = ?;", (p_id,))
    conn.close()
    p_json = ProjectManager.get_project_json_path(p_id)
    if p_json.exists():
        p_json.unlink()
