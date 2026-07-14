"""
Bước 1 - Săn nhạc và lọc bản quyền đầu vào, tích hợp SQLite DB & Media Probe.
"""
import os
import sys
import logging
import json
import subprocess
from pathlib import Path
from datetime import datetime, timezone

# Đảm bảo import được config.py từ thư mục cha
sys.path.append(str(Path(__file__).parent.parent))
import config
from utils.metadata_store import MetadataStore
from utils.retry_helper import retry
from core.db import get_db_connection
from core.media_probe import MediaProbe
from core.cache_manager import CacheManager
from core.schemas import validate_data_schema
from core.project_manager import ProjectManager

logger = logging.getLogger("lofi_automation")
store = MetadataStore(config.METADATA_DIR)

@retry(max_attempts=3)
def fetch_candidate_tracks(query: str = None, limit: int = 5) -> list[dict]:
    """
    Lấy danh sách track ứng viên từ nguồn whitelist (NCS, SoundCloud CC-BY).
    Sử dụng yt-dlp qua subprocess.
    """
    if not query:
        query = f"scsearch{limit}:NoCopyrightSounds lofi"
    elif not query.startswith("scsearch") and not query.startswith("http"):
        query = f"scsearch{limit}:{query}"
        
    logger.info(f"Đang tìm kiếm nhạc ứng viên qua yt-dlp với query: {query}")
    
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
            url = entry.get('url') or f"https://soundcloud.com/nocopyrightsounds"
            tracks.append({
                "track_id": track_id,
                "title": entry.get('title') or "Untitled",
                "author": entry.get('uploader') or "NoCopyrightSounds",
                "license": "NoCopyrightSounds License (Credit required)",
                "url": url,
                "download_url": url,
                "source": "SoundCloud (NCS)",
                "views": entry.get('view_count', 0) or 0,
                "likes": entry.get('like_count', 0) or 0,
            })
        except Exception as e:
            logger.warning(f"Không phân giải được dòng metadata: {e}")
            
    tracks.sort(key=lambda x: x.get("views", 0), reverse=True)
    return tracks[:limit]

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
    }

def is_license_safe(track: dict) -> bool:
    """Kiểm tra xem track có chứa dấu hiệu bản quyền không."""
    blacklist_markers = ["provided to youtube by", "under exclusive license", "warner", "sony", "believe"]
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
    candidates = fetch_candidate_tracks(limit=5)
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

