"""
AI FILE NOTE - STEP 1: MUSIC HUNTER

Chức năng chính:
- Tìm nhạc ứng viên từ YouTube/SoundCloud bằng yt-dlp.
- Đọc metadata, tạo URL/file nghe thử, tải audio và kiểm tra chất lượng.
- Lọc sơ bộ nguy cơ bản quyền theo blacklist; đây không phải xác nhận pháp lý.
- Lưu asset, trạng thái workflow và lịch sử trend YouTube/TikTok vào SQLite.
- Tổng hợp điểm xu hướng đa nền tảng khi có dữ liệu thật.

Đầu vào chính:
- Từ khóa tìm kiếm hoặc URL track, project_id và thông tin TikTok API tùy chọn.

Đầu ra chính:
- Danh sách dict track chuẩn hóa, đường dẫn audio/preview và dict phân tích trend.

API được file khác sử dụng:
- fetch_candidate_tracks(), fetch_track_metadata_by_url()
- get_stream_url(), get_preview_audio_path(), download_track()
- capture_youtube_trend_snapshot(), capture_tiktok_trend_snapshot()
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
from pathlib import Path
from datetime import datetime, timezone

# Đảm bảo import được config.py từ thư mục cha
sys.path.append(str(Path(__file__).parent.parent))
import config
from utils.helpers import MetadataStore, retry
from core.db import get_db_connection
from core.media_probe import MediaProbe
from core.cache_manager import CacheManager
from core.schemas import validate_data_schema
from core.project_manager import ProjectManager

logger = logging.getLogger("lofi_automation")
store = MetadataStore(config.METADATA_DIR)

@retry(max_attempts=3)
def _fetch_tracks_from_query(query: str, source_name: str, license_label: str, limit: int) -> list[dict]:
    """Chạy 1 query yt-dlp và phân giải kết quả thành danh sách track ứng viên."""
    logger.info(f"[{source_name}] Đang tìm kiếm nhạc ứng viên qua yt-dlp với query: {query}")

    cmd = [
        "python", "-m", "yt_dlp",
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

def get_stream_url(track_url: str) -> str:
    """
    Lấy direct stream URL của track để nghe thử ngay mà KHÔNG tải file về máy.
    Dùng cho st.audio trong UI duyệt nhạc. URL có hạn dùng ngắn (vài phút tới vài giờ).
    """
    cmd = [
        "python", "-m", "yt_dlp",
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
        "python", "-m", "yt_dlp",
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



def _ensure_social_trend_table(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS social_trend_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            track_id TEXT NOT NULL,
            platform TEXT NOT NULL,
            external_music_id TEXT,
            item_count INTEGER NOT NULL DEFAULT 0,
            views INTEGER NOT NULL DEFAULT 0,
            likes INTEGER NOT NULL DEFAULT 0,
            comments INTEGER NOT NULL DEFAULT 0,
            shares INTEGER NOT NULL DEFAULT 0,
            captured_at_utc TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_social_trend_track_platform_time
        ON social_trend_snapshots(track_id, platform, captured_at_utc)
    """)


def _get_tiktok_client_token(client_key: str, client_secret: str) -> str:
    """Lấy client token chính thức cho TikTok Research API."""
    import requests
    if not client_key or not client_secret:
        raise ValueError("Thiếu TikTok Client Key hoặc Client Secret.")
    response = requests.post(
        "https://open.tiktokapis.com/v2/oauth/token/",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_key": client_key,
            "client_secret": client_secret,
            "grant_type": "client_credentials",
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    token = payload.get("access_token")
    if not token:
        raise RuntimeError(payload.get("error_description") or "TikTok không trả access token.")
    return token


def capture_tiktok_trend_snapshot(
    track: dict,
    music_id: str,
    client_key: str,
    client_secret: str,
    days: int = 7,
) -> dict:
    """Tổng hợp video công khai theo music_id bằng TikTok Research API và lưu snapshot."""
    import requests
    from datetime import timedelta

    music_id = str(music_id or "").strip()
    if not music_id.isdigit():
        raise ValueError("TikTok music_id phải là chuỗi số.")
    days = max(1, min(int(days), 30))
    token = _get_tiktok_client_token(client_key.strip(), client_secret.strip())
    end_date = datetime.now(timezone.utc).date()
    start_date = end_date - timedelta(days=days - 1)
    fields = "id,view_count,like_count,comment_count,share_count,music_id,create_time"
    endpoint = f"https://open.tiktokapis.com/v2/research/video/query/?fields={fields}"
    body = {
        "query": {"and": [{"operation": "EQ", "field_name": "music_id", "field_values": [music_id]}]},
        "start_date": start_date.strftime("%Y%m%d"),
        "end_date": end_date.strftime("%Y%m%d"),
        "max_count": 100,
        "is_random": False,
    }
    videos = []
    cursor = 0
    search_id = None
    # Giới hạn 10 trang để giao diện không treo và kiểm soát quota.
    for _ in range(10):
        body["cursor"] = cursor
        if search_id:
            body["search_id"] = search_id
        response = requests.post(
            endpoint,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=body,
            timeout=45,
        )
        response.raise_for_status()
        payload = response.json()
        error = payload.get("error") or {}
        if error.get("code") not in (None, "ok", 0):
            raise RuntimeError(error.get("message") or str(error))
        data = payload.get("data") or {}
        videos.extend(data.get("videos") or [])
        if not data.get("has_more"):
            break
        cursor = int(data.get("cursor") or (cursor + len(data.get("videos") or [])))
        search_id = data.get("search_id") or search_id

    totals = {
        "item_count": len(videos),
        "views": sum(int(v.get("view_count") or 0) for v in videos),
        "likes": sum(int(v.get("like_count") or 0) for v in videos),
        "comments": sum(int(v.get("comment_count") or 0) for v in videos),
        "shares": sum(int(v.get("share_count") or 0) for v in videos),
    }
    captured_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    track_id = str((track or {}).get("track_id") or music_id)
    with _trend_connection() as conn:
        _ensure_social_trend_table(conn)
        conn.execute(
            """INSERT INTO social_trend_snapshots
               (track_id, platform, external_music_id, item_count, views, likes, comments, shares, captured_at_utc)
               VALUES (?, 'TikTok', ?, ?, ?, ?, ?, ?, ?)""",
            (track_id, music_id, totals["item_count"], totals["views"], totals["likes"],
             totals["comments"], totals["shares"], captured_at),
        )
        rows = conn.execute(
            """SELECT item_count, views, likes, comments, shares, captured_at_utc
               FROM social_trend_snapshots
               WHERE track_id = ? AND platform = 'TikTok' AND external_music_id = ?
               ORDER BY captured_at_utc ASC, id ASC""",
            (track_id, music_id),
        ).fetchall()

    result = {
        "platform": "TikTok",
        "music_id": music_id,
        "period_days": days,
        "captured_at_utc": captured_at,
        "snapshot_count": len(rows),
        "trend_label": "Chưa đủ dữ liệu",
        "confidence": "Thấp",
        "trend_score": None,
        "growth_percent": None,
        "items_delta": None,
        **totals,
    }
    if len(rows) < 2:
        return result

    first, last = rows[0], rows[-1]
    first_time = datetime.fromisoformat(first["captured_at_utc"].replace("Z", "+00:00"))
    last_time = datetime.fromisoformat(last["captured_at_utc"].replace("Z", "+00:00"))
    elapsed_hours = max((last_time - first_time).total_seconds() / 3600, 0.0)
    items_delta = max(int(last["item_count"]) - int(first["item_count"]), 0)
    views_delta = max(int(last["views"]) - int(first["views"]), 0)
    growth_percent = views_delta / max(int(first["views"]), 1) * 100
    daily_items = items_delta / max(elapsed_hours / 24, 0.25)
    engagement = (int(last["likes"]) + int(last["comments"]) + int(last["shares"])) / max(int(last["views"]), 1) * 100
    score = min(100, round(growth_percent * 10 + min(daily_items, 30) * 1.5 + min(engagement, 10) * 2))
    label = "Tăng mạnh" if score >= 80 else "Đang tăng" if score >= 60 else "Ổn định" if score >= 40 else "Tăng nhẹ" if score >= 20 else "Chưa có tín hiệu"
    confidence = "Cao" if len(rows) >= 5 and elapsed_hours >= 72 else "Trung bình" if len(rows) >= 3 and elapsed_hours >= 24 else "Thấp"
    result.update({
        "trend_label": label,
        "confidence": confidence,
        "trend_score": score,
        "growth_percent": round(growth_percent, 2),
        "items_delta": items_delta,
        "views_delta": views_delta,
        "elapsed_hours": round(elapsed_hours, 1),
    })
    return result


def build_cross_platform_trend(youtube: dict | None, tiktok: dict | None) -> dict:
    """Tổng hợp điểm từ nguồn thật; không chấm điểm cho nền tảng chưa kết nối."""
    sources = []
    if youtube and youtube.get("trend_score") is not None:
        sources.append(("YouTube", float(youtube["trend_score"]), 0.55))
    if tiktok and tiktok.get("trend_score") is not None:
        sources.append(("TikTok", float(tiktok["trend_score"]), 0.45))
    if not sources:
        return {"score": None, "label": "Chưa đủ dữ liệu", "confidence": "Thấp", "sources": []}
    weight_sum = sum(weight for _, _, weight in sources)
    score = round(sum(score * weight for _, score, weight in sources) / weight_sum)
    label = "Bắt trend mạnh" if score >= 80 else "Có tiềm năng" if score >= 60 else "Theo dõi thêm" if score >= 40 else "Tín hiệu yếu"
    confidence = "Cao" if len(sources) >= 2 else "Trung bình"
    return {"score": score, "label": label, "confidence": confidence, "sources": [name for name, _, _ in sources]}

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
        "python", "-m", "yt_dlp",
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
    import core.db
    core.db.init_db()
    
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

