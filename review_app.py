"""
AI FILE NOTE - STREAMLIT REVIEW DASHBOARD

Chức năng chính:
- Giao diện wizard 6 bước: kiểm tra hệ thống, chọn nhạc, tạo ảnh, chọn hiệu ứng, render, upload YouTube.
- Điều phối step1_music_hunter, step2_image_provider, step3_effect_provider, step4_render và step5_uploader.
- Bước hiệu ứng chia 3 tab (Đề xuất/Điều chỉnh/Thư viện), hỗ trợ chroma key và tự nhận diện loại nền.
- Bước upload sinh caption/hashtag bằng AI (core/caption_writer) và upload có tiến độ.
- Quản lý Streamlit session_state, lưu/khôi phục tiến trình tại data/review_app_state.json.
- Hiển thị preview nhạc, ảnh, hiệu ứng, trend và tiến độ render/ETA.
- Quản lý kết nối/cài đặt Stable Diffusion Local từ giao diện.

Cách chạy chuẩn:
- py -3.10 -m streamlit run review_app.py

Đầu vào chính:
- Thao tác người dùng, API key tùy chọn, project_id và các asset đã chọn.

Đầu ra chính:
- Trạng thái dự án, asset được duyệt và video cuối do step4_render tạo.

Luồng phụ thuộc:
- Bước 2 gọi step1_music_hunter để tìm/tải/phân tích nhạc.
- Bước 3 gọi step2_image_provider hoặc nhận ảnh Dreamina upload.
- Bước 4 tạo/chọn overlay, gọi step3_effect_provider.recommend_effects() (local trước Pixabay)
  và step4_render.build_effect_preview() với effect_settings thống nhất.
- Bước 5 gọi step4_render.run_step4() (encoder auto dò GPU) để render video cuối.
- Bước 6 gọi core/caption_writer.generate_caption() và step5_uploader.upload_video().

Lưu ý khi sửa:
- Đây là file điều phối/UI, không chuyển thuật toán xử lý nặng vào đây nếu đã có module core/step riêng.
- Giữ tên các khóa PERSISTED_STATE_KEYS và session_state đang được dùng giữa các bước.
- Sau thao tác làm thay đổi bước hoặc asset, lưu state trước khi st.rerun() khi cần.
- Không đổi chữ ký hàm public của step1/step2/step4 nếu chưa sửa đồng bộ nơi gọi.
"""
import json
import os
import random
import html
from pathlib import Path
import streamlit as st

import config
import step1_music_hunter
import step2_image_provider
import step3_subtitle_provider
import step3_effect_provider
import step4_render
from components.effect_live_preview import effect_live_preview
import importlib

# Streamlit giữ module đã import trong bộ nhớ giữa các lần rerun.
# Reload cấu hình trước, sau đó reload backend để hai bên dùng cùng dữ liệu thị trường.
config = importlib.reload(config)
step1_music_hunter = importlib.reload(step1_music_hunter)
step2_image_provider = importlib.reload(step2_image_provider)
step3_subtitle_provider = importlib.reload(step3_subtitle_provider)
step3_effect_provider = importlib.reload(step3_effect_provider)
step4_render = importlib.reload(step4_render)

_REQUIRED_MUSIC_APIS = (
    "discover_youtube_trends",
    "discover_youtube_trends_multi",
    "get_youtube_scan_usage",
    "get_trend_cache_info",
    "estimate_youtube_multi_scan_cost",
    "get_lastfm_status",
    "check_lastfm_connection",
    "is_focus_music_track",
    "classify_music_styles",
    "is_single_song_track",
    "assess_music_rights",
    "get_youtube_analytics_connection_status",
    "fetch_real_youtube_rpm",
)
_missing_music_apis = [
    name for name in _REQUIRED_MUSIC_APIS
    if not callable(getattr(step1_music_hunter, name, None))
]
if _missing_music_apis:
    raise RuntimeError(
        "step1_music_hunter.py không đồng bộ với giao diện. Thiếu: "
        + ", ".join(_missing_music_apis)
        + f". Module đang nạp từ: {getattr(step1_music_hunter, '__file__', 'không rõ')}"
    )
POLLINATIONS_LABEL = "Pollinations AI (Online, Miễn phí)"

AI_HORDE_LABEL = "AI Horde / Stable Horde (Miễn phí cộng đồng)"

HF_LABEL = "Hugging Face Inference (Free tier)"

CF_LABEL = "Cloudflare Workers AI (Free ~10k/ngày)"

SD_LOCAL_LABEL = "Stable Diffusion Local (Automatic1111)"

HOT_GENRES = {
    "☕ Coffee Shop Lofi": "lofi coffee shop copyright free",
    "🌧️ Rainy Night Beats": "lofi rain copyright free",
    "🌸 Anime Aesthetic": "lofi japanese aesthetic copyright free",
    "🎮 Gaming Chill": "lofi gaming copyright free",
    "🎵 Chill Beats": "chill beats creative commons",
}

DURATION_OPTIONS = {
    "10 giây (Để chạy thử nghiệm nhanh)": 10,
    "5 phút (Mẫu thử đầy đủ)": 300,
    "1 tiếng (Sản phẩm chuẩn 1H Loop)": 3600,
}

def apply_lofi_style() -> None:
    """Áp style tối cho giao diện."""
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
        
        .stApp {
            background: linear-gradient(180deg, #090a15 0%, #111322 100%);
            color: #e2e8f0;
            font-family: 'Inter', sans-serif;
        }
        .block-container {
            padding-top: 2rem;
            padding-bottom: 3rem;
        }
        
        /* Cải tiến style cho nút bấm Streamlit */
        div.stButton > button {
            background-color: #1a1c2e !important;
            color: #e2e8f0 !important;
            border: 1px solid #313552 !important;
            border-radius: 8px !important;
            font-weight: 500 !important;
            padding: 0.5rem 1rem !important;
            transition: all 0.3s cubic-bezier(0.25, 0.8, 0.25, 1) !important;
        }
        div.stButton > button:hover {
            background-color: #7c3aed !important;
            color: #ffffff !important;
            border-color: #7c3aed !important;
            transform: translateY(-2px) !important;
            box-shadow: 0 4px 15px rgba(124, 58, 237, 0.4) !important;
        }
        div.stButton > button:active {
            transform: translateY(0) !important;
        }
        
        /* Style cho nút Primary */
        div.stButton > button[kind="primary"] {
            background: linear-gradient(135deg, #7c3aed 0%, #a78bfa 100%) !important;
            color: #ffffff !important;
            border: none !important;
        }
        div.stButton > button[kind="primary"]:hover {
            box-shadow: 0 4px 20px rgba(124, 58, 237, 0.6) !important;
            filter: brightness(1.1) !important;
        }
        
        /* Hiệu ứng Glassmorphism cho thông báo alert */
        div[data-testid="stNotification"] {
            background: rgba(26, 28, 46, 0.75) !important;
            border: 1px solid rgba(124, 58, 237, 0.2) !important;
            backdrop-filter: blur(12px) !important;
            border-radius: 12px !important;
            color: #e2e8f0 !important;
        }
        
        /* Tối ưu các phần tử nhập liệu */
        div[data-testid="stTextInput"] input, div[data-testid="stSelectbox"] select {
            background-color: #121424 !important;
            border: 1px solid #272a44 !important;
            color: #e2e8f0 !important;
            border-radius: 8px !important;
        }
        
        div[data-testid="stMetricValue"] {
            color: #a78bfa !important;
            font-weight: 700 !important;
        }
        
        /* Thẻ Pills chủ đề dạng Tag */
        .lofi-tag-button {
            display: inline-block;
            background-color: #1b1e36;
            color: #a78bfa;
            border: 1px solid #32385e;
            padding: 4px 12px;
            border-radius: 16px;
            margin: 4px;
            cursor: pointer;
            transition: all 0.2s;
        }
        .lofi-tag-button:hover {
            background-color: #7c3aed;
            color: white;
            border-color: #7c3aed;
        }

        /* Music-first redesign */
        section[data-testid="stSidebar"] { background: #0d0f19; border-right: 1px solid #20243a; }
        section[data-testid="stSidebar"] .block-container { padding-top: 1.25rem; }
        .sidebar-brand { font-size: 1.15rem; font-weight:700; margin-bottom: 1.5rem; }
        .sidebar-brand span { display:block; color:#7f879f; font-size:.76rem; margin:4px 0 0 29px; font-weight:400; }
        .sidebar-label { color:#6f7790; font-size:.68rem; font-weight:700; letter-spacing:.12em; margin:1.35rem 0 .45rem; }
        .sidebar-status { background:#151827; border:1px solid #262b43; border-radius:10px; padding:12px; margin:16px 0; }
        .sidebar-status b, .sidebar-status span { display:block; }
        .sidebar-status span { color:#8f96ad; font-size:.75rem; margin-top:6px; }
        .step-eyebrow { color:#a78bfa; font-size:.72rem; font-weight:700; letter-spacing:.1em; margin-bottom:-.4rem; }
        .selected-track { display:grid; grid-template-columns:100px 1fr; align-items:center; gap:2px 12px; background:#151a2b; border:1px solid #5b43a8; border-radius:10px; padding:12px 14px; margin:14px 0 18px; }
        .selected-track span { grid-row:1/3; color:#a78bfa; font-size:.66rem; font-weight:700; }
        .selected-track small { color:#8f96ad; }
        .track-row { display:flex; justify-content:space-between; align-items:center; background:#141725; border:1px solid #252a40; border-radius:10px; padding:12px 14px; margin-top:10px; }
        .track-row b, .track-row span { display:block; }
        .track-row span { color:#8f96ad; font-size:.78rem; margin-top:3px; }
        .track-row em { color:#a78bfa; font-size:.75rem; font-style:normal; }
        .track-row-selected { border-color:#7c3aed; background:#19172b; }
        div[data-baseweb="tab-list"] { gap:8px; margin:10px 0 18px; }
        button[data-baseweb="tab"] { background:#141725; border:1px solid #292e45; border-radius:9px; padding:10px 14px; }

        /* Giữ preview ảnh luôn thấy khi mở prompt hoặc chỉnh mức crop */
        div[data-testid="stVerticalBlock"]:has(.sticky-image-preview-anchor) {
            position: sticky;
            top: 1rem;
            align-self: flex-start;
            z-index: 2;
        }
        .sticky-image-preview-anchor {
            height: 0;
            overflow: hidden;
        }

        /* Header tối giản: sidebar đã giữ tên ứng dụng và tiến trình */
        .block-container { padding-top: 1rem !important; }
        h2 { margin-top: 0 !important; margin-bottom: .65rem !important; }

        div[data-testid="stVerticalBlock"]:has(.sticky-effect-preview-anchor) {
            position: sticky;
            top: 1rem;
            align-self: flex-start;
            z-index: 2;
        }
        .sticky-effect-preview-anchor { height: 0; overflow: hidden; }

        div[data-testid="stVerticalBlock"]:has(.sticky-render-preview-anchor) {
            position: sticky;
            top: 1rem;
            align-self: flex-start;
            z-index: 2;
        }
        .sticky-render-preview-anchor { height: 0; overflow: hidden; }

        /* Trang render gọn trong một viewport */
        .render-compact-note { color:#8f96ad; font-size:.78rem; }
        div[data-testid="stMetric"] { padding: .25rem 0 !important; }
        </style>
        """,
        unsafe_allow_html=True,
    )

APP_STATE_FILE = config.BASE_DIR / "data" / "review_app_state.json"
PROMPT_SETTINGS_FILE = config.BASE_DIR / "data" / "prompt_api_settings.json"
PROMPT_SETTING_KEYS = (
    "prompt_provider_choice", "prompt_api_key", "prompt_api_url",
    "prompt_api_model", "prompt_profile_choice",
)

def load_prompt_settings() -> dict:
    try:
        value = json.loads(PROMPT_SETTINGS_FILE.read_text(encoding="utf-8")) if PROMPT_SETTINGS_FILE.is_file() else {}
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}

def save_prompt_settings() -> None:
    data = {key: str(st.session_state.get(key) or "") for key in PROMPT_SETTING_KEYS}
    PROMPT_SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp_path = PROMPT_SETTINGS_FILE.with_suffix(".tmp")
    temp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(PROMPT_SETTINGS_FILE)
PERSISTED_STATE_KEYS = (
    "project_id", "query", "candidates", "selected_track", "image_prompt",
    "image_path", "image_source", "effect_path", "effect_preview_path",
    "effect_preview_key", "final_video_path", "output_dir", "image_provider",
    "sd_api_url", "sd_checkpoint", "sd_install_dir", "sd_mode",
    "current_step", "vibe_mode", "effect_enabled", "motion_mode", "previews_dict",
    "effect_live_opacity", "effect_live_speed", "effect_live_blend_mode",
    "effect_type_choice", "effect_key_color", "effect_chroma_similarity",
    "effect_chroma_softness", "effect_despill", "effect_edge_feather",
    "effect_show_matte", "effect_preview_quality",
    "upload_title", "upload_description", "upload_hashtags", "upload_tags_text",
    "upload_language", "upload_privacy_choice", "upload_result_video_id",
    "scene_layers_manifest", "scene_layers_image", "scene_analysis_error",
    "dreamina_zoom_percent", "dreamina_prompt", "dreamina_prompt_vi", "dreamina_prompt_track_id", "dreamina_processed_key",
    "trend_query", "trend_scan_meta", "trend_markets", "music_scan_category", "music_scan_buckets",
    "music_inbox_track_id", "prompt_provider_choice", "prompt_api_key",
    "prompt_api_url", "prompt_api_model", "prompt_profile_choice",
    "text_profile",
)

def _json_safe(value):
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)

def load_persisted_app_state() -> dict:
    try:
        if not APP_STATE_FILE.is_file():
            return {}
        value = json.loads(APP_STATE_FILE.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}

def save_persisted_app_state() -> None:
    data = {k: _json_safe(st.session_state.get(k)) for k in PERSISTED_STATE_KEYS if k in st.session_state}
    APP_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp_path = APP_STATE_FILE.with_suffix(".tmp")
    temp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(APP_STATE_FILE)

def clear_persisted_app_state() -> None:
    APP_STATE_FILE.unlink(missing_ok=True)

def init_session_state() -> None:
    """Khởi tạo session và phục hồi tiến trình từ ổ đĩa."""
    persisted = load_persisted_app_state()
    prompt_settings = load_prompt_settings()
    defaults = {
        "query": "lofi chill copyright free",
        "candidates": [],
        "music_scan_buckets": {},
        "selected_track": None,
        "image_prompt": random.choice(config.IMAGE_PROMPTS),
        "image_path": None,
        "effect_path": None,
        "effect_preview_path": None,
        "final_video_path": None,
        "output_dir": str(config.OUTPUT_DIR),
        "image_provider": POLLINATIONS_LABEL,
        "pollinations_key": getattr(config, "POLLINATIONS_API_KEY", ""),
        "sd_api_url": getattr(config, "SD_LOCAL_API_URL", "http://127.0.0.1:7860"),
        "sd_checkpoint": getattr(config, "SD_LOCAL_CHECKPOINT", "sd_v1.5_anime.safetensors"),
        "sd_install_dir": "",
        "sd_mode": "existing",  # "existing" | "app_managed"
        "ai_horde_key": "0000000000",
        "hf_token": "",
        "hf_model_id": "stabilityai/stable-diffusion-xl-base-1.0",
        "pexels_api_key": "",
        "prompt_provider_choice": prompt_settings.get("prompt_provider_choice", "Gemini"),
        "prompt_api_key": prompt_settings.get("prompt_api_key", getattr(config, "PROMPT_API_KEY", "")),
        "prompt_api_url": prompt_settings.get("prompt_api_url", "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"),
        "prompt_api_model": prompt_settings.get("prompt_api_model", "gemini-2.5-flash"),
        "prompt_profile_choice": prompt_settings.get("prompt_profile_choice", "Tự động"),
        "current_step": 1,
        "vibe_mode": "clean",
        "effect_enabled": False,
        "motion_mode": "smooth_zoom",
        "effect_live_opacity": 0.55,
        "effect_live_speed": 1.0,
        "effect_live_blend_mode": "normal",
        "effect_type_choice": "auto",
        "effect_key_color": "#00FF00",
        "effect_chroma_similarity": 0.18,
        "effect_chroma_softness": 0.08,
        "effect_despill": 0.35,
        "effect_edge_feather": 1.5,
        "effect_show_matte": False,
        "effect_preview_quality": "fast",
        "upload_title": "",
        "upload_description": "",
        "upload_hashtags": "",
        "upload_tags_text": "",
        "upload_language": "vi",
        "upload_privacy_choice": "Riêng tư + tự đặt lịch",
        "upload_result_video_id": None,
        "scene_layers_manifest": None,
        "scene_layers_image": None,
        "scene_analysis_error": None,
        "previews_dict": None,
        "text_profile": None,
    }

    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = persisted.get(key, value)

    for key in PERSISTED_STATE_KEYS:
        if key not in st.session_state and key in persisted:
            st.session_state[key] = persisted[key]

    for path_key in ("image_path", "effect_path", "effect_preview_path", "final_video_path"):
        path_value = st.session_state.get(path_key)
        if path_value and not Path(path_value).exists():
            st.session_state[path_key] = None

    if not st.session_state.candidates:
        try:
            st.session_state.candidates = step1_music_hunter.fetch_candidate_tracks(
                query=st.session_state.query,
                limit=5,
            )
        except Exception:
            st.session_state.candidates = []


def capture_youtube_trend_snapshot_compat(track: dict) -> dict:
    """Fallback local khi module step1 đang bị Python cache hoặc lệch phiên bản."""
    capture = getattr(step1_music_hunter, "capture_youtube_trend_snapshot", None)
    if callable(capture):
        return capture(track)

    import sqlite3
    import subprocess
    import sys
    from datetime import datetime, timezone

    url = (track or {}).get("url")
    if not url:
        raise ValueError("Bài nhạc không có URL nguồn.")

    result = subprocess.run(
        [sys.executable, "-m", "yt_dlp", "--dump-single-json", "--no-playlist", "--no-warnings", url],
        capture_output=True,
        text=True,
        check=True,
        encoding="utf-8",
        errors="ignore",
        timeout=60,
    )
    entry = json.loads(result.stdout)
    current = dict(track)
    current.update({
        "title": entry.get("title") or current.get("title") or "Untitled",
        "author": entry.get("uploader") or entry.get("channel") or current.get("author") or "Unknown",
        "views": int(entry.get("view_count") or 0),
        "likes": int(entry.get("like_count") or 0),
        "comments": int(entry.get("comment_count") or 0),
        "duration": int(entry.get("duration") or current.get("duration") or 0),
        "upload_date": entry.get("upload_date") or current.get("upload_date") or "",
    })

    track_id = str(current.get("track_id") or entry.get("id") or "")
    if not track_id:
        raise ValueError("Bài nhạc thiếu track_id.")
    db_path = config.BASE_DIR / "data" / "music_trends.sqlite3"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    captured_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
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
        conn.execute(
            """INSERT INTO music_trend_snapshots
               (track_id, source, title, author, url, views, likes, comments, captured_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (track_id, current.get("source", "YouTube"), current.get("title"), current.get("author"),
             url, current["views"], current["likes"], current["comments"], captured_at),
        )
        conn.commit()
        rows = conn.execute(
            """SELECT views, likes, comments, captured_at_utc
               FROM music_trend_snapshots WHERE track_id = ?
               ORDER BY captured_at_utc ASC, id ASC""",
            (track_id,),
        ).fetchall()
    finally:
        conn.close()

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
    growth_percent = views_delta / max(int(first["views"]), 1) * 100
    interaction_delta = max(
        int(last["likes"]) + int(last["comments"]) - int(first["likes"]) - int(first["comments"]),
        0,
    )
    daily_growth = growth_percent / max(elapsed_hours / 24, 0.25)
    engagement_signal = min(interaction_delta / max(views_delta, 1) * 100, 10)
    score = min(100, round(daily_growth * 12 + engagement_signal * 3))
    label = "Tăng mạnh" if score >= 80 else "Đang tăng" if score >= 60 else "Ổn định" if score >= 40 else "Tăng nhẹ" if score >= 20 else "Chưa có tín hiệu"
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


def build_cross_platform_trend_compat(youtube: dict | None, lastfm: dict | None) -> dict:
    builder = getattr(step1_music_hunter, "build_cross_platform_trend", None)
    if callable(builder):
        return builder(youtube, lastfm)
    sources = []
    if youtube and youtube.get("trend_score") is not None:
        sources.append(("YouTube", float(youtube["trend_score"]), 0.85))
    if lastfm and lastfm.get("lastfm_score") is not None:
        sources.append(("Last.fm", float(lastfm["lastfm_score"]), 0.15))
    if not sources:
        return {"score": None, "label": "Chưa đủ dữ liệu", "confidence": "Thấp", "sources": []}
    total_weight = sum(weight for _, _, weight in sources)
    score = round(sum(value * weight for _, value, weight in sources) / total_weight)
    label = "Bắt trend mạnh" if score >= 80 else "Có tiềm năng" if score >= 65 else "Theo dõi thêm" if score >= 50 else "Tín hiệu yếu"
    return {"score": score, "label": label, "confidence": "Trung bình" if len(sources) >= 2 else "Thấp", "sources": [name for name, _, _ in sources]}


def format_num(num: int) -> str:
    """Rút gọn số lượt xem/lượt thích."""
    if not num:
        return "0"
    if num >= 1_000_000:
        return f"{num / 1_000_000:.1f}M"
    if num >= 1_000:
        return f"{num / 1_000:.1f}k"
    return str(num)

st.set_page_config(
    page_title="Lo-Fi Studio Dashboard",
    layout="wide",
    page_icon="🎧"
)

apply_lofi_style()
init_session_state()

if st.session_state.get("current_step", 1) > 7:
    st.session_state.current_step = 7

if "current_step" not in st.session_state:
    st.session_state.current_step = 1


WIZARD_STEPS = {
    1: "⚙️ Kiểm tra hệ thống",
    2: "🎵 Chọn nhạc",
    3: "🎨 Tạo ảnh nền",
    4: "🎤 Tạo phụ đề",
    5: "✨ Chọn hiệu ứng",
    6: "🚀 Render video",
    7: "📤 Upload YouTube",
}


# Subtitle follows visual effects so its preview uses the selected final scene.
WIZARD_STEPS.update({4: "✨ Chọn hiệu ứng", 5: "🎤 Tạo phụ đề"})

def render_wizard_header():
    current = st.session_state.get("current_step", 1)
    labels = []
    for step, title in WIZARD_STEPS.items():
        prefix = "🟣" if step == current else "⚪"
        labels.append(f"{prefix} {step}. {title}")
    st.caption("  →  ".join(labels))


def load_project_to_session_state(project_id: str):
    """Đồng bộ cấu hình từ database SQLite vào session state."""
    from core.runtime.project_manager import ProjectManager
    # Đảm bảo dự án tồn tại
    try:
        ProjectManager.create_project(project_id)
    except ValueError:
        pass
    p = ProjectManager.load_project(project_id)
    
    # Đồng bộ sang session state
    if p.get("track"):
        st.session_state["selected_track"] = p["track"]
    if p.get("audio_path"):
        st.session_state["selected_audio_path"] = p["audio_path"]
    if p.get("selected_image"):
        st.session_state["image_path"] = p["selected_image"]

    from core.text.effect_manifest import load_text_profile
    from core.text import provider as text_effect_provider
    loaded = load_text_profile(project_id)
    if loaded:
        p = text_effect_provider.normalize_text_profile(loaded)
    else:
        p = text_effect_provider.default_text_profile()
        
    # Tự động điền tên bài hát nếu phần nội dung chữ đang bị trống
    if not p.get("content") and st.session_state.get("selected_track"):
        p["content"] = st.session_state["selected_track"].get("title", "")
        
    st.session_state["text_profile"] = p

def render_local_sidebar():
    """Thanh tiến trình tối giản, khóa các bước chưa đủ điều kiện."""
    def path_ok(value) -> bool:
        try:
            return bool(value and Path(value).exists())
        except Exception:
            return False

    has_track = bool(st.session_state.get("selected_track"))
    has_image = path_ok(st.session_state.get("image_path"))
    has_final_video = path_ok(st.session_state.get("final_video_path"))
    current = int(st.session_state.get("current_step", 1))
    unlocked_step = 3 if has_track else 2
    if has_image:
        unlocked_step = 5
    if has_final_video:
        unlocked_step = 6

    with st.sidebar:
        st.markdown("<div class='sidebar-brand'>Lo-Fi Studio<span>Trình tạo video</span></div>", unsafe_allow_html=True)
        for step, title in WIZARD_STEPS.items():
            clean_title = title.split(" ", 1)[-1]
            is_current = step == current
            is_done = step < current
            is_locked = step > unlocked_step
            prefix = f"{step:02d}"
            suffix = "  ✓" if is_done else ""
            if st.button(
                f"{prefix}  {clean_title}{suffix}",
                key=f"sidebar_step_{step}",
                use_container_width=True,
                type="primary" if is_current else "secondary",
                disabled=is_locked,
            ):
                st.session_state.current_step = step
                st.rerun()

        st.divider()
        st.caption("DỰ ÁN")
        st.code(st.session_state.get("project_id", "lofi_default_prj"), language=None)
        with st.expander("Cài đặt"):
            proj_id = st.text_input(
                "Mã dự án",
                value=st.session_state.get("project_id", "lofi_default_prj"),
            )
            if proj_id != st.session_state.get("project_id"):
                st.session_state.project_id = proj_id
                load_project_to_session_state(proj_id)
                st.rerun()
            st.caption(f"Output: {st.session_state.output_dir}")
            if st.button("Đặt lại quy trình", use_container_width=True):
                preserved_settings = {key: st.session_state.get(key) for key in PROMPT_SETTING_KEYS}
                save_prompt_settings()
                clear_persisted_app_state()
                st.session_state.clear()
                st.session_state.update({key: value for key, value in preserved_settings.items() if value is not None})
                st.rerun()

def build_image_prompt_from_track(track: dict, variant: int = 0) -> str:
    """Tạo prompt fallback đa dạng; nhạc Trung luôn có nhân vật cổ phong."""
    title = (track or {}).get("title") or "lofi chill music"
    author = (track or {}).get("author") or "unknown artist"
    text = f"{title} {author} {(track or {}).get('description') or ''}".lower()
    markets = {str(code).upper() for code in ((track or {}).get("market_codes") or [])}
    chinese_markers = (
        "chinese", "mandarin", "c-pop", "cpop", "guzheng", "erhu", "pipa", "xianxia", "wuxia",
        "古风", "古風", "中文", "华语", "華語", "国语", "國語", "仙侠", "仙俠", "武侠", "武俠",
        "国风", "國風", "汉服", "漢服", "douyin", "nhạc trung", "nhac trung", "nhạc hoa", "nhac hoa",
    )
    is_chinese = bool(markets.intersection({"CN", "HK", "TW"})) or any(marker in text for marker in chinese_markers)

    if is_chinese:
        character_scenes = [
            "an elegant fictional swordsman in layered white and sapphire hanfu standing beside a moonlit lotus lake, one hand resting on a sheathed sword, silver hair ornaments and long ribbons moving in the wind",
            "a graceful fictional musician in ivory and crimson hanfu seated in an ancient pavilion playing a guzheng, ornate gold hairpins, red maple leaves circling through warm sunset light",
            "a wandering fictional warrior in dark teal wuxia robes crossing a snowy mountain pass, bamboo hat held at the side, embroidered cloak and loose sleeves lifted by the winter wind",
            "a celestial fictional mage in pale blue and silver xianxia robes floating above a sea of clouds, jade crown, luminous water ribbons and small spirit lights orbiting around the character",
            "a refined fictional scholar in cream and jade hanfu holding a folding fan beneath a corridor of lanterns, ancient riverside town glowing through soft evening mist",
            "a fictional guardian in black and gold ceremonial robes standing among desert ruins on the Silk Road, long scarf streaming behind, crescent moon and drifting golden sand",
            "a fictional flute player in emerald hanfu standing inside a rain-soaked bamboo forest, translucent umbrella nearby, fireflies and droplets catching cool green light",
            "a fictional royal figure in pearl white and vermilion court robes walking through a palace garden filled with peach blossoms, embroidered sleeves, ornate crown and sunrise haze",
            "a mysterious fictional traveler in violet and midnight-blue robes on a cliff above an ancient city, paper talismans and glowing butterflies moving through storm clouds",
            "a fictional healer in soft aqua hanfu kneeling beside a luminous lotus pond, herb basket and jade accessories, misty mountains reflected in calm water under dawn light",
            "a fictional archer in white and red wuxia attire standing in a field of silver grass, carved bow lowered at the side, migrating birds and a huge amber moon in the distance",
            "a fictional dancer in flowing turquoise and gold hanfu turning inside a candlelit temple hall, silk sleeves forming sweeping arcs, incense smoke and floating flower petals",
        ]
        palettes = [
            "deep navy, moonlit blue, silver and restrained gold",
            "vermilion, warm ivory, dark wood and lantern gold",
            "jade green, mist gray, pearl white and cyan highlights",
            "plum purple, midnight blue and pale magical glow",
            "snow white, charcoal, cold blue and subtle crimson",
            "teal, turquoise, antique gold and ink-black shadows",
        ]
        scene = character_scenes[int(variant or 0) % len(character_scenes)]
        palette = palettes[(int(variant or 0) * 5 + len(title)) % len(palettes)]
        side = "left third" if int(variant or 0) % 2 == 0 else "right third"
        return (
            f"Chinese fantasy illustration inspired by the song '{title}': {scene}. "
            f"Character placed on the {side}, with clean negative space on the opposite side for a title added later. "
            "Near foreground with petals, leaves, silk ribbons or luminous particles; detailed character and props in the midground; "
            "far background with layered mountains, moon, palace silhouettes or drifting clouds. "
            f"Color palette: {palette}. Cinematic lighting, elegant ink-wash and polished anime painting fusion, highly detailed, "
            "natural anatomy, well-formed hands, wide 16:9 composition, no text, no Chinese characters, no logo, no watermark, no border."
        )

    scene = "cozy lofi bedroom studio, wooden desk, headphones, notebook, soft window light"
    mood = "calm chill mood, warm and relaxing atmosphere"
    colors = "soft blue and warm orange color palette"
    keyword_scenes = [
        (("rain", "rainy", "storm", "thunder"), "rainy night bedroom, raindrops on window, city lights outside, warm desk lamp"),
        (("coffee", "cafe", "café", "shop"), "cozy retro coffee shop interior, rainy street outside, warm lights, small table with headphones"),
        (("study", "homework", "focus", "work"), "quiet study desk, open laptop, books, headphones, cup of tea, night window"),
        (("sleep", "night", "midnight", "dream"), "peaceful night room, moonlight through window, soft blanket, calm city view"),
        (("japan", "japanese", "tokyo", "anime"), "quiet Tokyo side street at night, vending machine glow, small apartment window, gentle rain"),
        (("summer", "sunset", "sun"), "peaceful sunset balcony, plants, headphones on chair, golden sky"),
        (("winter", "snow", "cold"), "warm room during snowy evening, window frost, cozy lamp, headphones on desk"),
        (("space", "dream", "ambient"), "dreamy attic studio, starry sky through skylight, soft glowing desk setup"),
        (("game", "gaming", "pixel"), "cozy gaming desk setup, soft monitor glow, headphones, rainy night window"),
    ]
    all_scenes = [scene_text for _, scene_text in keyword_scenes]
    matched_index = next((i for i, (words, _) in enumerate(keyword_scenes) if any(word in text for word in words)), None)
    if matched_index is not None:
        scene = keyword_scenes[matched_index][1]
    if variant > 0:
        scene = all_scenes[((matched_index or 0) + variant) % len(all_scenes)]
    if any(word in text for word in ("sad", "alone", "lonely", "blue")):
        mood, colors = "quiet reflective mood, gentle melancholic atmosphere", "deep blue, soft purple, warm amber accents"
    elif any(word in text for word in ("happy", "cute", "sweet")):
        mood, colors = "gentle happy mood, cozy and cute atmosphere", "pastel pink, cream, soft yellow palette"
    elif any(word in text for word in ("dark", "deep", "late")):
        mood, colors = "late night calm mood, cinematic low light atmosphere", "dark navy, violet, warm lamp glow"
    return (
        f"{scene}, inspired by the music title '{title}', {mood}, {colors}, lofi anime background, "
        "cinematic wide shot, 16:9 widescreen composition, detailed environment, soft lighting, "
        "no text, no logo, no watermark"
    )


def generate_track_prompt(track: dict) -> str:
    """
    Sinh prompt theo bài nhạc: ưu tiên LLM (mỗi lần bấm ra bối cảnh khác),
    LLM lỗi thì fallback heuristic có xoay vòng bối cảnh.
    """
    track_id = (track or {}).get("track_id", "none")
    history_key = f"prompt_history_{track_id}"
    history = st.session_state.setdefault(history_key, [])

    # Cho phép nhập key API riêng cho LLM viết prompt (không bắt buộc)
    if st.session_state.get("prompt_api_key"):
        config.PROMPT_API_KEY = st.session_state.prompt_api_key
    if st.session_state.get("prompt_api_url"):
        config.PROMPT_API_URL = st.session_state.prompt_api_url
    if st.session_state.get("prompt_api_model"):
        config.PROMPT_API_MODEL = st.session_state.prompt_api_model

    profile_map = {
        "Tự động": "auto",
        "Anime Trung Quốc": "chinese",
        "Anime Việt Nam": "vietnamese",
        "Anime Lofi": "lofi",
        "Anime chung": "general",
    }
    prompt_track = dict(track or {})
    prompt_track["image_prompt_profile"] = profile_map.get(st.session_state.get("prompt_profile_choice"), "auto")
    try:
        from utils.helpers import generate_prompt_from_track, get_last_prompt_diagnostics
        prompt = generate_prompt_from_track(prompt_track, avoid=history)
        st.session_state.prompt_diagnostics = get_last_prompt_diagnostics()
    except Exception as e:
        variant = st.session_state.get(f"prompt_variant_{track_id}", 0) + 1
        st.session_state[f"prompt_variant_{track_id}"] = variant
        prompt = build_image_prompt_from_track(track, variant=variant)
        st.toast(f"LLM viết prompt không phản hồi, dùng bối cảnh dựng sẵn (#{variant}).", icon="⚠️")

    history.append(prompt)
    # Chỉ giữ 10 prompt gần nhất để tránh phình session
    st.session_state[history_key] = history[-10:]
    return prompt


def describe_dreamina_prompt_vi(track: dict, variant: int = 0) -> str:
    """Mô tả tiếng Việt dùng đúng bối cảnh của prompt tiếng Anh hiện tại."""
    title = (track or {}).get("title") or "bản nhạc lofi"
    author = (track or {}).get("author") or "nghệ sĩ chưa rõ"
    text = f"{title} {author}".lower()
    scenes = [
        "phòng ngủ vào đêm mưa, giọt mưa trên cửa kính, ánh đèn thành phố và đèn bàn ấm",
        "quán cà phê retro ấm cúng, phố mưa bên ngoài và bàn nhỏ có tai nghe",
        "góc học tập yên tĩnh với laptop, sách, tai nghe, trà nóng và cửa sổ ban đêm",
        "căn phòng đêm yên bình với ánh trăng, chăn mềm và thành phố tĩnh lặng",
        "con phố nhỏ Tokyo về đêm, ánh máy bán hàng tự động và mưa nhẹ",
        "ban công lúc hoàng hôn với cây xanh, tai nghe trên ghế và bầu trời vàng",
        "căn phòng ấm trong chiều tuyết, cửa kính phủ sương và tai nghe trên bàn",
        "studio gác mái mơ màng với bầu trời sao qua cửa sổ trần",
        "góc máy tính chơi game ấm cúng, ánh màn hình dịu và cửa sổ đêm mưa",
    ]
    keyword_groups = [
        ("rain", "rainy", "storm", "thunder"),
        ("coffee", "cafe", "café", "shop"),
        ("study", "homework", "focus", "work"),
        ("sleep", "night", "midnight"),
        ("japan", "japanese", "tokyo", "anime"),
        ("summer", "sunset", "sun"),
        ("winter", "snow", "cold"),
        ("space", "ambient", "dream"),
        ("game", "gaming", "pixel"),
    ]
    matched_index = next((i for i, words in enumerate(keyword_groups) if any(word in text for word in words)), 0)
    scene = scenes[(matched_index + int(variant or 0)) % len(scenes)]
    mood = "không khí thư giãn, yên tĩnh và ấm áp"
    colors = "bảng màu xanh dịu kết hợp ánh cam ấm"
    if any(word in text for word in ("sad", "alone", "lonely", "blue")):
        mood, colors = "không khí trầm lắng và suy tư", "xanh đậm, tím dịu và điểm nhấn hổ phách"
    elif any(word in text for word in ("happy", "cute", "sweet")):
        mood, colors = "không khí nhẹ nhàng và vui tươi", "hồng pastel, kem và vàng nhạt"
    elif any(word in text for word in ("dark", "deep", "late")):
        mood, colors = "không khí đêm khuya với ánh sáng điện ảnh thấp", "xanh navy, tím và ánh đèn vàng ấm"
    return (
        f"Hình ảnh lấy cảm hứng từ bài “{title}” của {author}: {scene}; {mood}; {colors}. "
        "Bố cục ngang 16:9, phong cách nền anime lofi, góc máy rộng, nhiều chi tiết môi trường, "
        "ánh sáng mềm, không chữ, không logo và không watermark."
    )

def prepare_dreamina_prompt(track: dict, force: bool = False) -> None:
    """Tạo đồng bộ prompt tiếng Anh và mô tả tiếng Việt."""
    track_id = str((track or {}).get("track_id") or "unknown")
    if force or st.session_state.get("dreamina_prompt_track_id") != track_id or not st.session_state.get("dreamina_prompt"):
        variant_key = f"dreamina_prompt_variant_{track_id}"
        variant = int(st.session_state.get(variant_key, 0)) + (1 if force else 0)
        st.session_state[variant_key] = variant
        # Luôn đi qua bộ prompt anime trung tâm. Nếu API lỗi, helpers tự dùng fallback anime.
        prompt_en = generate_track_prompt(track)
        st.session_state.dreamina_prompt = prompt_en
        st.session_state.image_prompt = prompt_en
        st.session_state.dreamina_prompt_vi = describe_dreamina_prompt_vi(track, variant=variant)
        st.session_state.dreamina_prompt_track_id = track_id

def _go_to_step(step: int):
    st.session_state.current_step = step
    st.rerun()

def suggest_effect_query_from_track(track: dict) -> str:
    """Gợi ý hiệu ứng theo tên bài nhạc đang chọn."""
    title = (track or {}).get("title") or ""
    author = (track or {}).get("author") or ""
    text = f"{title} {author}".lower()

    effect_rules = [
        (("rain", "rainy", "storm", "thunder"), "rain overlay"),
        (("night", "midnight", "sleep", "dream", "sad", "alone", "lonely"), "dust particles overlay"),
        (("coffee", "cafe", "café", "shop"), "bokeh overlay"),
        (("study", "homework", "focus", "work"), "film grain overlay"),
        (("japan", "japanese", "tokyo", "anime"), "light leak overlay"),
        (("winter", "snow", "cold"), "snow overlay"),
        (("space", "ambient", "dream"), "cinematic particles"),
        (("dark", "deep", "late"), "smoke overlay"),
        (("summer", "sunset", "sun", "happy", "cute", "sweet"), "light leak overlay"),
        (("game", "gaming", "pixel"), "film grain overlay"),
    ]

    for keywords, effect_query in effect_rules:
        if any(word in text for word in keywords):
            return effect_query

    return "dust particles overlay"


@st.cache_data(ttl=86400, show_spinner=False)
def _search_pixabay_effects_cached(query: str, api_key: str) -> list[dict]:
    """Cache metadata Pixabay 24 giờ; video chỉ tải khi người dùng chọn."""
    return step3_effect_provider.search_pixabay_effects(query, api_key, max_results=12)


@st.cache_data(ttl=86400, show_spinner=False)
def _search_ai_effects_cached(profile_json: str, api_key: str) -> list[dict]:
    """Cache kết quả Pixabay đã được xếp hạng theo hồ sơ AI."""
    return step3_effect_provider.search_and_rank_pixabay_effects(json.loads(profile_json), api_key)


def _apply_ai_effect_settings(profile: dict) -> None:
    st.session_state.effect_live_opacity = float(profile.get("opacity", 0.55))
    st.session_state.effect_live_speed = float(profile.get("speed", 1.0))
    blend = str(profile.get("blend_mode", "normal"))
    st.session_state.effect_live_blend_mode = blend if blend in ("normal", "screen", "lighten", "overlay", "soft-light") else "normal"
    effect_type = str(profile.get("effect_type") or "")
    if effect_type in ("screen_black", "chroma_key", "alpha", "normal"):
        st.session_state.effect_type_choice = effect_type
    if profile.get("key_color"):
        st.session_state.effect_key_color = str(profile["key_color"])
    for state_key, profile_key, default in (
        ("effect_chroma_similarity", "chroma_similarity", 0.18),
        ("effect_chroma_softness", "chroma_softness", 0.08),
        ("effect_despill", "despill", 0.35),
        ("effect_edge_feather", "edge_feather", 1.5),
    ):
        try:
            st.session_state[state_key] = float(profile.get(profile_key, default))
        except (TypeError, ValueError):
            st.session_state[state_key] = default


def _select_effect_path(effect_path: Path) -> None:
    """Chọn hiệu ứng mới và vô hiệu cache preview FFmpeg cũ."""
    st.session_state.effect_enabled = True
    st.session_state.effect_path = str(Path(effect_path).resolve())
    st.session_state.effect_preview_path = None
    st.session_state.effect_preview_key = None



def render_track_preview(track: dict, key_prefix: str):
    """
    Nghe thử track: ưu tiên file đã tải về, chưa có thì phát stream trực tiếp
    qua yt-dlp mà KHÔNG cần tải file (URL stream được cache theo phiên).
    """
    audio_file = config.INPUT_AUDIO_DIR / f"{track.get('track_id')}.m4a"
    if audio_file.exists():
        st.audio(str(audio_file))
        return

    stream_key = f"stream_url_{track.get('track_id')}"
    if st.session_state.get(stream_key):
        st.audio(st.session_state[stream_key])
        return

    if st.button("▶️ Nghe thử ngay (không cần tải)", key=f"{key_prefix}_stream", use_container_width=True):
        with st.spinner("Đang lấy luồng nghe thử..."):
            try:
                st.session_state[stream_key] = step1_music_hunter.get_stream_url(track.get("url"))
                st.rerun()
            except Exception as e:
                st.error(f"Không nghe thử được bài này: {e}")


def _select_track(track: dict, go_next: bool = True):
    """Chọn nhạc, tạo prompt và chuyển thẳng sang bước ảnh."""
    st.session_state.selected_track = track
    prepare_dreamina_prompt(track)
    try:
        from core.text import provider as text_effect_provider
        from core.text.effect_manifest import save_text_profile
        project_id = st.session_state.get("project_id", "lofi_default_prj")
        p = text_effect_provider.default_text_profile()
        p["content"] = track.get("title", "")
        st.session_state["text_profile"] = p
        save_text_profile(project_id, p)
    except Exception:
        pass
    st.session_state.current_step = 3
    st.rerun()

def _toggle_music_preview(track_id: str) -> None:
    """Chỉ cho phép một bài mở trình nghe thử tại một thời điểm."""
    current = st.session_state.get("music_preview_track_id")
    st.session_state.music_preview_track_id = None if current == track_id else track_id


def _format_track_age(hours: float) -> str:
    hours = max(float(hours or 0), 0)
    if hours < 24:
        return f"{hours:.0f}h"
    return f"{hours / 24:.1f}d"


def _track_has_india_signal(track: dict) -> bool:
    """Nhận diện dấu hiệu nhạc Ấn Độ từ vùng quét và metadata, chỉ dùng cho bộ lọc giao diện."""
    if "IN" in (track.get("market_codes") or []):
        return True
    text = " ".join(str(track.get(key) or "") for key in ("title", "author", "description")).lower()
    markers = (
        " india ", "indian", "hindi", "bollywood", "punjabi", "tamil", "telugu",
        "bengali", "malayalam", "kannada", "marathi", "bhojpuri",
    )
    padded = f" {text} "
    return any(marker in padded for marker in markers)


def _toggle_track_details(track_id: str) -> None:
    """Mở hoặc đóng thông tin bổ sung ngay dưới bài."""
    current = str(st.session_state.get("music_inbox_track_id") or "")
    st.session_state.music_inbox_track_id = "" if current == str(track_id) else str(track_id)


def _render_compact_track_row(track: dict, rank: int, key_prefix: str, show_trend: bool = True) -> None:
    """Hàng nhạc toàn chiều rộng, không dùng card viền hoặc emoji trang trí."""
    track_id = str(track.get("track_id") or f"track_{rank}")
    score = int(track.get("cross_platform_score") or track.get("trend_score") or 0)
    view_hour = format_num(int(track.get("views_per_hour") or 0))
    title = html.escape(str(track.get("title") or "Untitled"))
    author = html.escape(str(track.get("author") or "Unknown"))
    rights = step1_music_hunter.assess_music_rights(track)
    markets = track.get("market_codes") or []
    tags = track.get("style_tags") or step1_music_hunter.classify_music_styles(track)
    published = str(track.get("published_at") or "")[:10] or str(track.get("upload_date") or "Chưa rõ")
    original_url = track.get("source_url") or track.get("url")
    expanded = str(st.session_state.get("music_inbox_track_id") or "") == track_id
    flame_class = "flame-xl" if score >= 80 else "flame-lg" if score >= 65 else "flame-md" if score >= 40 else "flame-sm"
    trend_label = "Rất nóng" if score >= 80 else "Đang tăng" if score >= 65 else "Có tín hiệu" if score >= 40 else "Thấp"

    row = st.container()
    with row:
        thumb, info, stats, actions = st.columns([0.66, 4.75, 1.25, 1.2], vertical_alignment="center")
        image = track.get("thumbnail") or track.get("thumbnail_url")
        if image:
            thumb.image(image, use_container_width=True)
        else:
            thumb.markdown(f"**{rank:02d}**")

        rights_class = "safe" if rights["rights_level"] == "cc" else "risk" if rights["rights_level"] in ("standard", "high") else "unknown"
        rights_label = "Creative Commons" if rights["rights_level"] == "cc" else "Cần kiểm tra" if rights["rights_level"] in ("standard", "high") else "Chưa xác minh"
        tag_text = " · ".join(tags[:2])
        info.markdown(
            f"<div class='feed-title'>{title}</div>"
            f"<div class='feed-author'>{author}</div>"
            f"<div class='feed-meta'>{published} · {' · '.join(markets[:3]) or 'YouTube'} · "
            f"{html.escape(tag_text)} · <span class='status-dot {rights_class}'></span>{rights_label}</div>",
            unsafe_allow_html=True,
        )
        if info.button("Thông tin", key=f"{key_prefix}_details_{track_id}", help="Mở thông tin bài", type="tertiary"):
            _toggle_track_details(track_id)
            st.rerun()

        stats.markdown(
            f"<div class='trend-wrap'><span class='css-flame {flame_class}'></span>"
            f"<span class='trend-number'>{score}</span></div>"
            f"<div class='trend-label'>{trend_label}</div>"
            f"<div class='feed-views'>{view_hour}/h <span>lượt xem</span></div>",
            unsafe_allow_html=True,
        )
        if actions.button("Nghe", key=f"{key_prefix}_play_{track_id}", use_container_width=True):
            _toggle_music_preview(track_id)
            st.rerun()
        if actions.button("Chọn bài", key=f"{key_prefix}_pick_{track_id}", type="primary", use_container_width=True):
            _select_track(track)

        if st.session_state.get("music_preview_track_id") == track_id:
            render_track_preview(track, key_prefix=f"{key_prefix}_preview_{track_id}")

        if expanded:
            duration = int(track.get("duration") or 0)
            details = st.columns([1.1, 1.1, 1.1, 1.5, 0.9], vertical_alignment="center")
            details[0].caption("NỀN TẢNG")
            details[0].markdown(f"**{track.get('platform') or 'YouTube'}**")
            details[1].caption("THỜI LƯỢNG")
            details[1].markdown(f"**{duration // 60}:{duration % 60:02d}**")
            details[2].caption("TỔNG VIEW")
            details[2].markdown(f"**{format_num(track.get('views', 0))}**")
            details[3].caption("THỊ TRƯỜNG")
            details[3].markdown(f"**{' · '.join(track.get('market_names') or markets) or 'Chưa rõ'}**")
            if original_url:
                details[4].link_button("Bài gốc", original_url, use_container_width=True)
            st.caption(f"Last.fm #{track.get('lastfm_rank')}" if track.get("lastfm_matched") else "Last.fm chưa khớp")
            st.caption(f"Quyền dùng: {rights['rights_status']} · {rights['rights_note']}")
        st.markdown("<div class='feed-divider'></div>", unsafe_allow_html=True)


def _apply_trend_preset(preset_query: str, force_creative_commons: bool = False) -> None:
    """Callback chạy trước rerun nên cập nhật text_input an toàn."""
    st.session_state.trend_query = preset_query
    st.session_state.compact_trend_query = preset_query
    if force_creative_commons:
        st.session_state.compact_license_filter = "Creative Commons"


def _apply_market_preset(market_codes: list[str]) -> None:
    """Áp nhanh nhóm thị trường và giữ đúng giới hạn cấu hình."""
    available = getattr(config, "YOUTUBE_MARKETS", {})
    limit = int(getattr(config, "YOUTUBE_TREND_MAX_MARKETS_PER_SCAN", 8))
    selected = []
    for code in market_codes:
        code = str(code or "").strip().upper()
        if code in available and code not in selected:
            selected.append(code)
    selected = selected[:limit]
    st.session_state.trend_markets = selected
    st.session_state.compact_trend_regions = selected



def render_music_wizard_step():
    """Màn chọn nhạc đơn giản: chọn nhóm, phong cách, quét rồi chọn bài."""
    st.markdown("BƯỚC 02", unsafe_allow_html=True)
    st.markdown("## Chọn nhạc")
    st.caption("Chọn loại nhạc và thời gian. Hệ thống quét rộng một lần, sau đó lọc phong cách ở kết quả.")
    st.markdown(
        """
        <style>
        .feed-title {font-weight:760;font-size:1rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
        .feed-author {font-size:.86rem;color:#94a3b8;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:.08rem;}
        .feed-meta {font-size:.76rem;color:#7f8a9e;margin-top:.24rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
        .feed-divider {height:1px;background:#222a3a;margin:.62rem 0 .7rem;}
        .status-dot {display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:5px;vertical-align:1px;}
        .status-dot.safe {background:#22c55e;}
        .status-dot.unknown {background:#eab308;}
        .status-dot.risk {background:#ef4444;}
        .trend-wrap {display:flex;align-items:flex-end;justify-content:flex-end;gap:.42rem;height:2.2rem;}
        .css-flame {display:inline-flex;align-items:center;justify-content:center;width:1.25rem;height:1.5rem;line-height:1;transform-origin:center bottom;}
        .css-flame::before {content:"🔥";display:block;font-size:1.35rem;line-height:1;}
        .flame-sm {transform:scale(.72);opacity:.62;}
        .flame-md {transform:scale(.9);opacity:.78;}
        .flame-lg {transform:scale(1.08);opacity:.92;}
        .flame-xl {transform:scale(1.28);filter:drop-shadow(0 0 6px rgba(249,115,22,.45));}
        .trend-number {font-size:1.18rem;font-weight:820;color:#f8fafc;}
        .trend-label {font-size:.76rem;color:#94a3b8;text-align:right;margin-top:.08rem;}
        .feed-views {font-size:.86rem;color:#cbd5e1;text-align:right;margin-top:.22rem;}
        .feed-views span {font-size:.7rem;color:#64748b;text-transform:uppercase;letter-spacing:.03em;}
        </style>
        """,
        unsafe_allow_html=True,
    )

    youtube_ready = bool(getattr(config, "YOUTUBE_API_KEY", ""))
    usage = step1_music_hunter.get_youtube_scan_usage()
    tracks = [item for item in st.session_state.get("candidates", []) if item.get("source") == "YouTube Trends"]
    lastfm_status = step1_music_hunter.get_lastfm_status(tracks)
    youtube_status = "Đã kết nối" if youtube_ready else "Chưa kết nối"
    lastfm_label = "Đã kiểm tra" if lastfm_status["verified"] else "Đã cấu hình" if lastfm_status["configured"] else "Chưa cấu hình"
    matched_text = f" · Khớp {lastfm_status['matched_count']}/{lastfm_status['checked_count']}" if lastfm_status["checked_count"] else ""
    st.caption(
        f"YouTube: {youtube_status}   ·   Last.fm: {lastfm_label}{matched_text}   ·   Quota: {usage['remaining']}/{usage['limit']}"
    )

    category_options = ["🇻🇳 Nhạc Việt", "🇨🇳 Nhạc Trung", "🎧 Lofi thư giãn"]
    scan_buckets = st.session_state.setdefault("music_scan_buckets", {})
    category = st.radio(
        "Loại nhạc", category_options, horizontal=True, key="simple_music_category",
        format_func=lambda name: f"{name.replace('🇻🇳 ', '').replace('🇨🇳 ', '').replace('🎧 ', '')} · {len((scan_buckets.get(name) or {}).get('tracks') or [])}",
    )
    category_bucket = scan_buckets.get(category) or {}
    category_tracks = category_bucket.get("tracks") or []
    category_config = {
        "🇻🇳 Nhạc Việt": {
            "markets": ["VN"],
            "query": "nhạc Việt|V-Pop|ballad Việt|TikTok Việt|lofi Việt",
            "filters": ["Tất cả", "V-Pop", "Ballad", "TikTok / Douyin", "Remix", "Lofi"],
        },
        "🇨🇳 Nhạc Trung": {
            "markets": ["HK", "TW", "CN"],
            "query": "Vietsub|Pinyin|Douyin|C-Pop|nhạc Trung|古风|华语",
            "filters": ["Tất cả", "Vietsub + Pinyin", "TikTok / Douyin", "C-Pop", "Ballad", "Cổ phong", "Remix", "Lofi"],
        },
        "🎧 Lofi thư giãn": {
            "markets": ["VN", "HK", "TW"],
            "query": "lofi|chill beats|study music|sleep music|rain lofi|piano lofi|ambient lofi",
            "filters": ["Tất cả", "Học tập", "Ngủ", "Mưa đêm", "Quán cà phê", "Piano", "Ambient"],
        },
    }
    current = category_config[category]
    for stale_key in ("simple_markets_0", "simple_markets_1", "simple_markets_2"):
        st.session_state.pop(stale_key, None)
    control_cols = st.columns([1.3, 1.0, 1.0], vertical_alignment="bottom")
    with control_cols[0]:
        trend_period = st.selectbox(
            "Thời gian quét", ["7 ngày", "14 ngày", "30 ngày", "60 ngày", "90 ngày"], index=2, key="simple_period"
        )
    with control_cols[1]:
        result_limit = st.selectbox("Số bài / thị trường", [20, 30, 50], index=0, key="simple_result_limit")
    query = current["query"]
    selected_markets = list(current["markets"])
    days_map = {"7 ngày": 7, "14 ngày": 14, "30 ngày": 30, "60 ngày": 60, "90 ngày": 90}
    force_refresh = False
    license_filter = "any"

    with st.popover("Cài đặt"):
        advanced_cols = st.columns([1.2, 1.2, 2.2], vertical_alignment="bottom")
        force_refresh = advanced_cols[0].checkbox("Bỏ qua cache", value=False, key="simple_force_refresh")
        license_label = advanced_cols[1].selectbox(
            "Giấy phép", ["Tất cả", "Creative Commons", "Standard YouTube License"], key="simple_license"
        )
        license_filter = {
            "Tất cả": "any", "Creative Commons": "creativeCommon", "Standard YouTube License": "youtube"
        }[license_label]
        market_data = getattr(config, "YOUTUBE_MARKETS", {})
        fallback_names = {"VN": "Việt Nam", "HK": "Hong Kong", "TW": "Đài Loan", "CN": "Trung Quốc"}
        # Thị trường được tự chọn theo loại nhạc. Không dùng multiselect để tránh state rỗng khóa nút Quét.
        selected_markets = [code for code in current["markets"] if code in {"VN", "HK", "TW", "CN"}]
        advanced_cols[2].text_input(
            "Thị trường tự động",
            value=", ".join(market_data.get(code, {}).get("name", fallback_names.get(code, code)) for code in selected_markets),
            disabled=True,
            key=f"simple_markets_display_{category_options.index(category)}",
        )
        lastfm_cols = st.columns([1, 1, 2.4], vertical_alignment="center")
        check_fm = lastfm_cols[0].button("Kiểm tra Last.fm", use_container_width=True, disabled=not lastfm_status["configured"])
        refresh_fm = lastfm_cols[1].button("Làm mới Last.fm", use_container_width=True, disabled=not lastfm_status["configured"])
        lastfm_cols[2].caption(
            f"Chart {lastfm_status['chart_count']} bài · cache "
            + (f"{lastfm_status['cache_age_minutes']:.0f} phút" if lastfm_status.get("cache_age_minutes") is not None else "chưa có")
        )
        if check_fm or refresh_fm:
            result = step1_music_hunter.check_lastfm_connection(refresh_fm, tracks)
            if result["ok"]:
                st.success("Last.fm hoạt động.")
                save_persisted_app_state()
                st.rerun()
            else:
                st.error(result["message"])

    estimate = step1_music_hunter.estimate_youtube_multi_scan_cost(
        query, days_map[trend_period], selected_markets, result_limit, license_filter, force_refresh
    )
    required_calls = max(0, int(estimate.get("required_calls", 0)))
    remaining = max(0, int(estimate.get("remaining", usage["remaining"])))
    blocked = bool(selected_markets) and required_calls > remaining
    market_names = [getattr(config, "YOUTUBE_MARKETS", {}).get(code, {}).get("name", code) for code in selected_markets]
    st.caption(
        f"Quét {len(selected_markets)} thị trường ({', '.join(market_names)}) · dự kiến {required_calls} lượt API · "
        f"{estimate['cached_markets']} vùng dùng cache"
    )
    with control_cols[2]:
        scan_clicked = st.button(
            "Quét nhạc", type="primary", use_container_width=True,
            disabled=not youtube_ready or not selected_markets or blocked,
        )
    if not youtube_ready:
        st.error("Chưa có YouTube API key.")
    elif blocked:
        st.error(f"Không đủ quota: cần {required_calls} lượt nhưng chỉ còn {remaining}.")

    if scan_clicked:
        from datetime import datetime
        progress = st.progress(0, text="Đang quét...")
        status = st.empty()

        def update_progress(done, total, message):
            progress.progress(int(done / max(total, 1) * 100), text=message)
            status.caption(message)

        try:
            found = step1_music_hunter.discover_youtube_trends_multi(
                query=query,
                days=days_map[trend_period],
                region_codes=selected_markets,
                max_results=result_limit,
                progress_callback=update_progress,
                force_refresh=force_refresh,
                license_filter=license_filter,
            )
            if category != "🎧 Lofi thư giãn":
                found = [item for item in found if step1_music_hunter.is_single_song_track(item)]
            if not found:
                st.warning(
                    "Không tìm thấy bài đơn phù hợp. Hãy giữ 30 ngày, tăng số bài mỗi thị trường "
                    "hoặc bật Bỏ qua cache rồi quét lại."
                )
                st.stop()
            for item in found:
                item["style_tags"] = item.get("style_tags") or step1_music_hunter.classify_music_styles(item)
            scan_meta = {
                "period": trend_period, "regions": selected_markets, "query": query,
                "category": category, "result_limit": result_limit,
                "scanned_at": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
                "track_count": len(found),
                "single_song_only": category != "🎧 Lofi thư giãn",
                "cache_hit": bool(found and found[0].get("cache_hit")),
            }
            scan_buckets[category] = {"tracks": found, "meta": scan_meta}
            st.session_state.music_scan_buckets = scan_buckets
            st.session_state.trend_query = query
            st.session_state.trend_markets = selected_markets
            st.session_state.music_scan_category = category
            st.session_state.candidates = found
            st.session_state.compact_trend_display_limit = 8
            st.session_state.trend_scan_meta = scan_meta
            save_persisted_app_state()
            st.rerun()
        except Exception as exc:
            st.error(f"Quét thất bại: {exc}")

    category_bucket = st.session_state.get("music_scan_buckets", {}).get(category) or {}
    tracks = [
        item for item in (category_bucket.get("tracks") or [])
        if item.get("source") == "YouTube Trends"
        and step1_music_hunter.is_focus_music_track(item)
        and (category == "🎧 Lofi thư giãn" or step1_music_hunter.is_single_song_track(item))
    ]
    if not tracks:
        st.info(f"{category} chưa có dữ liệu. Bấm Quét nhạc để tạo danh sách riêng cho mục này.")
        return

    bucket_meta = category_bucket.get("meta") or {}
    st.markdown(f"### {category} <small style='color:#7f8a9e'>· {len(tracks)} bài</small>", unsafe_allow_html=True)
    scan_mode = "Bài đơn" if bucket_meta.get("single_song_only") else "Cho phép mix dài"
    scan_source = "Cache" if bucket_meta.get("cache_hit") else "API"
    st.caption(
        f"Lần quét gần nhất: {bucket_meta.get('scanned_at', 'chưa rõ')}   ·   {bucket_meta.get('period', 'không rõ')}   ·   "
        f"{scan_mode}   ·   {scan_source}"
    )
    available_filters = current["filters"]
    style_filter = st.segmented_control(
        "Phong cách", available_filters, default="Tất cả", key="simple_style_filter"
    ) or "Tất cả"
    filter_cols = st.columns([1.15, 1.0, 0.72, 0.72, 0.7], vertical_alignment="bottom")
    sort_mode = filter_cols[0].selectbox("Sắp xếp", ["Xu hướng", "View/giờ"], key="simple_sort")
    minimum_score = filter_cols[1].selectbox("Điểm tối thiểu", [0, 50, 65, 80], key="simple_min_score")
    safer_only = filter_cols[2].toggle("An toàn", value=False, key="simple_safer", help="Ẩn bài có nguy cơ quyền sử dụng")
    lastfm_only = filter_cols[3].toggle("Last.fm", value=False, key="simple_lastfm_only", help="Chỉ hiện bài khớp Last.fm")
    if filter_cols[4].button("Đặt lại", use_container_width=True):
        for reset_key in ("simple_style_filter", "simple_sort", "simple_min_score", "simple_safer", "simple_lastfm_only"):
            st.session_state.pop(reset_key, None)
        st.rerun()

    visible = []
    for item in tracks:
        style_tags = item.get("style_tags") or step1_music_hunter.classify_music_styles(item)
        if style_filter != "Tất cả" and style_filter not in style_tags:
            continue
        score = int(item.get("cross_platform_score") or item.get("trend_score") or 0)
        rights = step1_music_hunter.assess_music_rights(item)
        if score < minimum_score:
            continue
        if safer_only and rights["rights_level"] in ("standard", "high"):
            continue
        if lastfm_only and not item.get("lastfm_matched"):
            continue
        visible.append(item)
    sorters = {
        "Xu hướng": lambda item: item.get("cross_platform_score") or item.get("trend_score") or 0,
        "View/giờ": lambda item: item.get("views_per_hour") or 0,
    }
    visible.sort(key=sorters[sort_mode], reverse=True)
    st.caption(f"Hiển thị {len(visible)}/{len(tracks)} bài")
    display_limit = int(st.session_state.get("compact_trend_display_limit", 10))
    for rank, track in enumerate(visible[:display_limit], 1):
        _render_compact_track_row(track, rank, "trend", show_trend=True)
    if len(visible) > display_limit:
        if st.button(f"Xem thêm {min(10, len(visible) - display_limit)} bài", use_container_width=True):
            st.session_state.compact_trend_display_limit = display_limit + 10
            st.rerun()


def _short_error_message(error, provider_name: str = "") -> str:
    """Rút gọn lỗi kỹ thuật để UI dễ đọc."""
    text = str(error or "")
    provider_name = provider_name or ""

    if "HuggingFaceProvider" in text or "has no attribute" in text:
        return "File step2_image_provider.py chưa cập nhật. Hãy chép file mới hoặc chọn nguồn ảnh khác."
    if "AIHordeProvider" in text:
        return "File step2_image_provider.py chưa cập nhật AI Horde. Hãy chép file mới hoặc chọn nguồn khác."
    if "429" in text or "Too Many Requests" in text:
        return "Nguồn tạo ảnh đang giới hạn lượt. Đợi vài phút hoặc đổi nguồn khác."
    if "530" in text:
        return "Máy chủ tạo ảnh đang lỗi tạm thời. Hãy đổi nguồn khác hoặc thử lại sau."
    if "503" in text:
        return "Model đang tải hoặc máy chủ đang bận. Hãy thử lại sau."
    if "401" in text or "Unauthorized" in text:
        return "API key/token không đúng hoặc đã hết quyền dùng."
    if "402" in text or "Insufficient" in text:
        return "Tài khoản/API đã hết credit. Hãy đổi nguồn khác."
    if "Connection" in text or "Failed to establish" in text or "Max retries" in text:
        return "Chưa kết nối được nguồn tạo ảnh. Kiểm tra mạng hoặc nguồn đang chạy chưa."
    if "timeout" in text.lower() or "timed out" in text.lower():
        return "Nguồn tạo ảnh phản hồi quá lâu. Hãy thử lại hoặc đổi nguồn khác."
    if "Chưa nhập Hugging Face token" in text:
        return "Bạn chưa nhập Hugging Face token."

    clean = text.replace("\n", " ").strip()
    if len(clean) > 180:
        clean = clean[:180].rstrip() + "..."
    return f"Lỗi sinh ảnh: {clean}" if clean else "Lỗi sinh ảnh không rõ."


def _scale_and_register_image(raw_path: Path, provider_name: str, prompt_text: str) -> Path:
    from step2_image_provider import scale_image_ffmpeg, validate_image_file
    from core.runtime.db import get_db_connection
    from core.runtime.schemas import validate_data_schema
    from core.runtime.project_manager import ProjectManager
    from core.runtime.cache_manager import CacheManager
    import json
    from datetime import datetime, timezone
    
    full_hd_path = config.TEMP_IMAGE_DIR / f"bg_full_hd_{random.randint(1000, 9999)}.png"
    scale_image_ffmpeg(raw_path, full_hd_path, 1920, 1080)
    validate_image_file(full_hd_path)
    
    try:
        raw_path.unlink(missing_ok=True)
    except Exception:
        pass
        
    file_sha256 = CacheManager.get_file_sha256(full_hd_path)
    file_size = full_hd_path.stat().st_size
    
    image_meta = {
        "schema_name": "image_metadata",
        "schema_version": 1,
        "provider": provider_name,
        "model": st.session_state.get("sd_checkpoint", "unknown_model"),
        "prompt": prompt_text,
        "negative_prompt": getattr(config, "IMAGE_NEGATIVE_PROMPT", ""),
        "seed": random.randint(100000, 999999),
        "source_size": "960x540",
        "final_size": "1920x1080",
        "upscale_method": "lanczos",
        "source_path": f"data/temp_image/{full_hd_path.name}",
        "full_hd_path": f"data/temp_image/{full_hd_path.name}"
    }
    
    validate_data_schema(image_meta, "image_metadata")
    
    meta_path = config.METADATA_DIR / f"image_{file_sha256[:12]}.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(image_meta, f, ensure_ascii=False, indent=2)

    p_id = st.session_state.get("project_id", "lofi_default_prj")
    conn = get_db_connection()
    try:
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        asset_id = f"image_{file_sha256[:12]}"
        with conn:
            conn.execute("""
            INSERT OR REPLACE INTO assets (asset_id, project_id, path, sha256, mime_type, size_bytes, processing_status, review_status, created_at_utc)
            VALUES (?, ?, ?, ?, 'image/png', ?, 'verified', 'approved', ?);
            """, (asset_id, p_id, f"data/temp_image/{full_hd_path.name}", file_sha256, file_size, now_str))
            
        ProjectManager.update_workflow_status(
            project_id=p_id,
            module_name="image",
            processing_status="verified",
            review_status="approved",
            input_hash=file_sha256,
            output_hash=file_sha256,
            reason=f"Image generated via {provider_name} and scaled to Full HD 1920x1080.",
            actor="review_app"
        )
    finally:
        conn.close()
        
    return full_hd_path

def _generate_image_with_fallback(prompt_text: str, provider_name: str):
    """Tạo ảnh nền, tự fallback sang SD Local nếu Pollinations bị giới hạn."""
    config.POLLINATIONS_API_KEY = st.session_state.pollinations_key
    config.SD_LOCAL_API_URL = st.session_state.sd_api_url
    config.SD_LOCAL_CHECKPOINT = st.session_state.sd_checkpoint
    config.TEMP_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    out_path = config.TEMP_IMAGE_DIR / f"bg_{random.randint(1000, 9999)}.png"

    try:
        if "Pollinations" in provider_name:
            provider = step2_image_provider.PollinationsProvider()
        elif "AI Horde" in provider_name or "Stable Horde" in provider_name:
            if not hasattr(step2_image_provider, "AIHordeProvider"):
                return None, "File step2_image_provider.py chưa cập nhật AI Horde. Hãy chép file mới hoặc chọn nguồn khác."
            provider = step2_image_provider.AIHordeProvider(st.session_state.get("ai_horde_key", "0000000000"))
        elif "Hugging Face" in provider_name:
            if not hasattr(step2_image_provider, "HuggingFaceProvider"):
                return None, "File step2_image_provider.py chưa cập nhật Hugging Face. Hãy chép file mới hoặc chọn nguồn khác."
            provider = step2_image_provider.HuggingFaceProvider(
                token=st.session_state.get("hf_token", ""),
                model_id=st.session_state.get("hf_model_id", "stabilityai/stable-diffusion-xl-base-1.0"),
            )
        elif "Cloudflare" in provider_name:
            config.CLOUDFLARE_ACCOUNT_ID = st.session_state.get("cf_account_id", "") or config.CLOUDFLARE_ACCOUNT_ID
            config.CLOUDFLARE_API_TOKEN = st.session_state.get("cf_api_token", "") or config.CLOUDFLARE_API_TOKEN
            provider = step2_image_provider.CloudflareWorkersAIProvider()
        else:
            provider = step2_image_provider.SDLocalProvider()
        
        raw_img = provider.generate(prompt_text, out_path)
        final_img = _scale_and_register_image(raw_img, provider_name, prompt_text)
        return final_img, None
    except Exception as e:
        error_text = str(e)
        is_rate_limit = "Pollinations" in provider_name and ("429" in error_text or "Too Many Requests" in error_text)
        if is_rate_limit:
            try:
                fallback_path = config.TEMP_IMAGE_DIR / f"bg_sd_{random.randint(1000, 9999)}.png"
                fallback_provider = step2_image_provider.SDLocalProvider()
                raw_fallback = fallback_provider.generate(prompt_text, fallback_path)
                final_fallback = _scale_and_register_image(raw_fallback, "SD Local (Fallback)", prompt_text)
                return final_fallback, "Pollinations đang giới hạn lượt tạo ảnh, đã chuyển sang SD Local."
            except Exception as sd_error:
                return None, (
                    "Pollinations đang giới hạn lượt tạo ảnh. SD Local cũng chưa chạy được. "
                    "Hãy mở Stable Diffusion WebUI/ComfyUI local hoặc đợi vài phút rồi thử lại.\n"
                    f"Chi tiết SD Local: {sd_error}"
                )
        return None, _short_error_message(e, provider_name)


def _prepare_uploaded_background(input_path: Path, output_path: Path, zoom_percent: int = 2) -> Path:
    """Cắt giữa 16:9, phóng mép và xuất Full HD, không phụ thuộc module step2."""
    import subprocess

    input_path = Path(input_path)
    output_path = Path(output_path)
    if not input_path.exists():
        raise FileNotFoundError(f"Không tìm thấy ảnh upload: {input_path}")

    zoom_percent = max(0, min(int(zoom_percent), 20))
    keep_ratio = max(0.80, 1.0 - zoom_percent / 100.0)
    target_ratio = 16 / 9
    crop_filter = (
        f"crop='if(gt(iw/ih,{target_ratio}),ih*{target_ratio},iw)*{keep_ratio}':"
        f"'if(gt(iw/ih,{target_ratio}),ih,iw/{target_ratio})*{keep_ratio}':"
        "(iw-ow)/2:(ih-oh)/2,scale=1920:1080:flags=lanczos,setsar=1"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-i", str(input_path),
        "-vf", crop_filter, "-frames:v", "1", str(output_path),
    ]
    try:
        subprocess.run(
            cmd, capture_output=True, text=True, check=True,
            encoding="utf-8", errors="ignore", timeout=90,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("Không tìm thấy FFmpeg trong PATH.") from exc
    except subprocess.CalledProcessError as exc:
        lines = (exc.stderr or exc.stdout or "FFmpeg không trả chi tiết").strip().splitlines()
        detail = lines[-1] if lines else "Không rõ lỗi"
        raise RuntimeError(f"FFmpeg xử lý ảnh lỗi: {detail}") from exc

    if not output_path.exists() or output_path.stat().st_size < 1024:
        raise RuntimeError("Ảnh Full HD không được tạo hoặc file quá nhỏ.")
    return output_path


def render_copy_prompt_button(prompt_text: str) -> None:
    """Hiển thị prompt bằng st.code để dùng nút sao chép native của Streamlit."""
    with st.popover("Sao chép prompt", use_container_width=True):
        st.caption("Bấm biểu tượng sao chép ở góc phải khung prompt.")
        st.code(prompt_text or "", language=None, wrap_lines=True)

def render_image_wizard_step():
    """Màn hình ảnh nền gọn, ưu tiên Dreamina và hạn chế cuộn trang."""
    st.markdown("## Ảnh nền")
    if not st.session_state.selected_track:
        st.warning("🔒 Hãy chọn nhạc ở Bước 2 trước.")
        st.stop()

    track = st.session_state.selected_track
    prepare_dreamina_prompt(track)

    control_col, preview_col = st.columns([0.92, 1.08], gap="large")

    with control_col:
        mode = st.radio(
            "Nguồn ảnh",
            ["Dreamina / Upload", "Tạo bằng AI"],
            horizontal=True,
            label_visibility="collapsed",
        )

        if mode == "Dreamina / Upload":
            # 1. Chọn phong cách và tạo prompt. Các cài đặt kỹ thuật được thu gọn bên dưới.
            st.selectbox(
                "Phong cách anime",
                ["Tự động", "Anime Trung Quốc", "Anime Việt Nam", "Anime Lofi", "Anime chung"],
                key="prompt_profile_choice",
                help="Tự động nhận diện theo bài nhạc. Chỉ chọn thủ công khi nhận diện chưa đúng.",
            )

            diagnostics = st.session_state.get("prompt_diagnostics") or {}
            if diagnostics:
                st.caption(
                    f"{diagnostics.get('profile', 'Anime')} · "
                    f"{diagnostics.get('quality_score', '?')}/100 · "
                    f"{diagnostics.get('source', 'AI')}"
                )

            if st.button(
                "Tạo prompt mới",
                key="dreamina_regenerate_prompt",
                type="primary",
                use_container_width=True,
            ):
                prepare_dreamina_prompt(track, force=True)
                st.toast("Đã tạo prompt anime mới.", icon="✅")
                st.rerun()

            prompt_value = st.session_state.get("dreamina_prompt", "")
            edited_prompt = st.text_area(
                "Prompt anime cho Dreamina",
                value=prompt_value,
                height=150,
                key=f"dreamina_prompt_editor_{st.session_state.get('dreamina_prompt_variant_' + str(track.get('track_id')), 0)}",
                help="Dùng nút sao chép có sẵn trong ô nếu cần gửi sang Dreamina.",
            )
            st.session_state.dreamina_prompt = edited_prompt
            st.session_state.image_prompt = edited_prompt

            with st.expander("Cài đặt Gemini", expanded=False):
                prompt_provider = st.selectbox(
                    "Nhà cung cấp",
                    ["Gemini", "API tương thích OpenAI"],
                    key="prompt_provider_choice",
                )
                st.session_state.prompt_api_key = st.text_input(
                    "API key",
                    value=st.session_state.get("prompt_api_key", ""),
                    type="password",
                    placeholder="Dán API key tại đây",
                )
                if prompt_provider == "Gemini":
                    model_options = ["gemini-2.5-flash", "gemini-2.5-pro"]
                    current_model = st.session_state.get("prompt_api_model", model_options[0])
                    model_index = model_options.index(current_model) if current_model in model_options else 0
                    st.session_state.prompt_api_model = st.selectbox(
                        "Model",
                        model_options,
                        index=model_index,
                    )
                    st.session_state.prompt_api_url = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
                else:
                    st.session_state.prompt_api_url = st.text_input(
                        "API URL",
                        value=st.session_state.get("prompt_api_url", ""),
                    )
                    st.session_state.prompt_api_model = st.text_input(
                        "Model",
                        value=st.session_state.get("prompt_api_model", ""),
                    )
                st.caption(
                    "Đã kết nối Gemini" if st.session_state.prompt_api_key
                    else "Chưa có key · hệ thống vẫn dùng fallback anime"
                )
                # Tự lưu, không cần thêm nút Lưu cấu hình.
                save_prompt_settings()

            with st.expander("Mô tả tiếng Việt", expanded=False):
                current_variant = int(st.session_state.get("dreamina_prompt_variant_" + str(track.get("track_id")), 0))
                st.write(st.session_state.get("dreamina_prompt_vi") or describe_dreamina_prompt_vi(track, current_variant))

            # 2. Tải ảnh. Ảnh được tự xử lý khi upload hoặc khi đổi trạng thái phóng.
            uploaded_bg = st.file_uploader(
                "Tải ảnh từ Dreamina",
                type=["png", "jpg", "jpeg", "webp"],
                key="dreamina_background_upload",
                help="Ảnh sẽ tự crop 16:9 và xuất Full HD.",
            )
            if uploaded_bg is not None:
                import hashlib
                upload_bytes = uploaded_bg.getvalue()
                upload_hash = hashlib.sha256(upload_bytes).hexdigest()[:16]
                if st.session_state.get("dreamina_upload_hash") != upload_hash:
                    st.session_state.dreamina_upload_bytes = upload_bytes
                    st.session_state.dreamina_upload_name = uploaded_bg.name
                    st.session_state.dreamina_upload_hash = upload_hash
                    st.session_state.dreamina_processed_key = None

            has_upload = bool(st.session_state.get("dreamina_upload_bytes"))
            zoom_enabled = st.toggle(
                "Phóng nhẹ và cắt viền 2%",
                value=int(st.session_state.get("dreamina_zoom_percent", 2)) > 0,
                key="dreamina_zoom_enabled",
                disabled=not has_upload,
                help="Tắt để giữ mức phóng 0%. Crop 16:9 vẫn được áp dụng.",
            )
            zoom_percent = 2 if zoom_enabled else 0
            st.session_state.dreamina_zoom_percent = zoom_percent

            if has_upload:
                upload_hash = st.session_state.dreamina_upload_hash
                desired_key = f"{upload_hash}:{zoom_percent}"
                applied_key = st.session_state.get("dreamina_processed_key")
                if desired_key != applied_key:
                    raw_path = None
                    try:
                        upload_bytes = st.session_state.dreamina_upload_bytes
                        original_name = st.session_state.get("dreamina_upload_name", "dreamina.png")
                        config.TEMP_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
                        ext = Path(original_name).suffix.lower() or ".png"
                        raw_path = config.TEMP_IMAGE_DIR / f"uploaded_bg_{upload_hash}{ext}"
                        final_path = config.TEMP_IMAGE_DIR / f"bg_full_hd_{upload_hash}_z{zoom_percent}.png"
                        raw_path.write_bytes(upload_bytes)
                        with st.spinner("Đang chuẩn hóa ảnh 16:9..."):
                            _prepare_uploaded_background(raw_path, final_path, zoom_percent=zoom_percent)
                        if not final_path.exists() or final_path.stat().st_size < 1024:
                            raise RuntimeError("File ảnh sau xử lý không hợp lệ.")
                        st.session_state.image_path = str(final_path.resolve())
                        st.session_state.image_source = f"Dreamina · crop {zoom_percent}% · 1920×1080"
                        st.session_state.dreamina_processed_key = desired_key
                        save_persisted_app_state()
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Không xử lý được ảnh: {exc}")
                    finally:
                        if raw_path:
                            raw_path.unlink(missing_ok=True)
                else:
                    st.caption(f"Ảnh sẵn sàng · 1920×1080 · crop {zoom_percent}%")

        else:
            prompt_text = st.text_area(
                "Prompt tạo ảnh",
                value=st.session_state.image_prompt,
                height=100,
            )
            st.session_state.image_prompt = prompt_text
            provider_options = [POLLINATIONS_LABEL, AI_HORDE_LABEL, HF_LABEL, CF_LABEL, SD_LOCAL_LABEL]
            current_provider = st.session_state.image_provider if st.session_state.image_provider in provider_options else POLLINATIONS_LABEL
            provider_choice = st.selectbox(
                "Nguồn tạo ảnh",
                provider_options,
                index=provider_options.index(current_provider),
            )
            st.session_state.image_provider = provider_choice

            with st.expander("Cấu hình nguồn", expanded=False):
                if provider_choice == POLLINATIONS_LABEL:
                    st.session_state.pollinations_key = st.text_input("Pollinations API key", value=st.session_state.pollinations_key, type="password")
                elif provider_choice == AI_HORDE_LABEL:
                    st.session_state.ai_horde_key = st.text_input("AI Horde API key", value=st.session_state.get("ai_horde_key", "0000000000"), type="password")
                elif provider_choice == HF_LABEL:
                    st.session_state.hf_token = st.text_input("Hugging Face token", value=st.session_state.get("hf_token", ""), type="password")
                    st.session_state.hf_model_id = st.text_input("Model ID", value=st.session_state.get("hf_model_id", "stabilityai/stable-diffusion-xl-base-1.0"))
                elif provider_choice == CF_LABEL:
                    st.session_state.cf_account_id = st.text_input("Cloudflare Account ID", value=st.session_state.get("cf_account_id", ""))
                    st.session_state.cf_api_token = st.text_input("Cloudflare API token", value=st.session_state.get("cf_api_token", ""), type="password")
                else:
                    st.session_state.sd_api_url = st.text_input("SD Local API URL", value=st.session_state.sd_api_url)

            if st.button("Tạo ảnh AI", type="primary", use_container_width=True):
                with st.spinner("Đang tạo ảnh..."):
                    image_path, note = _generate_image_with_fallback(prompt_text, provider_choice)
                if image_path:
                    st.session_state.image_path = str(image_path)
                    st.session_state.image_source = provider_choice
                    if note:
                        st.warning(note)
                    st.rerun()
                else:
                    st.error(note or "Không tạo được ảnh.")

        if st.button("← Chọn lại nhạc", use_container_width=True):
            _go_to_step(2)

    with preview_col:
        st.markdown("<div class='sticky-image-preview-anchor'></div>", unsafe_allow_html=True)
        current_image = st.session_state.get("image_path")
        if current_image and Path(current_image).exists():
            st.image(str(current_image), use_container_width=True)
            st.caption(f"Sẵn sàng · {st.session_state.get('image_source', 'Ảnh nền')}")
            clear_col, next_col = st.columns([1, 2])
            with clear_col:
                if st.button("Bỏ ảnh", use_container_width=True):
                    st.session_state.image_path = None
                    st.rerun()
            with next_col:
                if st.button("Dùng ảnh này →", type="primary", use_container_width=True):
                    _go_to_step(4)
        else:
            st.markdown(
                "<div style='min-height:320px;border:1px dashed #343a55;border-radius:12px;"
                "display:flex;align-items:center;justify-content:center;color:#7f879f;'>"
                "Ảnh xem trước sẽ xuất hiện tại đây</div>",
                unsafe_allow_html=True,
            )
            st.caption("Tải ảnh Dreamina hoặc tạo ảnh AI ở cột bên trái.")

def render_subtitle_wizard_step_simple():
    """Màn phụ đề tối giản: online trước, AI chỉ là lựa chọn dự phòng."""
    from core.media.probe import MediaProbe

    st.markdown("## Phụ đề Karaoke")
    track = st.session_state.get("selected_track")
    if not track:
        st.error("Hãy chọn bài nhạc trước khi tạo phụ đề.")
        if st.button("← Quay lại chọn nhạc"):
            _go_to_step(2)
        return

    project_id = st.session_state.get("project_id", "lofi_default_prj")
    track_id = str(track.get("track_id") or "")
    audio_file = config.INPUT_AUDIO_DIR / f"{track_id}.m4a"
    if "subtitle_manifest_data" not in st.session_state or st.session_state.get("subtitle_project_id") != project_id:
        st.session_state["subtitle_manifest_data"] = step3_subtitle_provider.load_project_subtitles(project_id)
        st.session_state["subtitle_project_id"] = project_id
    manifest = st.session_state["subtitle_manifest_data"]
    manifest["enabled"] = st.checkbox("Hiển thị phụ đề trong video", value=manifest.get("enabled", True))

    duration = 180.0
    try:
        duration = float(MediaProbe.probe_media(audio_file).get("duration_seconds") or duration)
    except Exception:
        pass

    st.caption(f"{track.get('title', 'Chưa rõ tên bài')} · {track.get('author', 'Chưa rõ ca sĩ')}")
    search_col, info_col = st.columns([1, 1.4], gap="medium")
    if search_col.button("🔎 Tìm lời bài hát online", type="primary", use_container_width=True):
        with st.spinner("Đang tìm lời bài hát đã xuất bản..."):
            result = step3_subtitle_provider.search_online_lyrics(
                str(track.get("title") or ""), str(track.get("author") or ""), duration
            )
        if result.get("found"):
            st.session_state["online_lyrics_candidates"] = result["candidates"]
            st.rerun()
        st.warning(result.get("reason", "Không tìm thấy lời online."))
    candidates = st.session_state.get("online_lyrics_candidates") or []
    if candidates:
        choice = st.selectbox(
            "Chọn đúng phiên bản lời bài hát",
            options=list(range(len(candidates))),
            format_func=lambda index: (
                f"{candidates[index]['track_name']} — {candidates[index]['artist_name']}"
                f" · {candidates[index]['album_name'] or 'Không rõ album'}"
                f" · {'Có timestamp' if candidates[index]['timing'] == 'synced' else 'Lời thường'}"
            ),
            key="online_lyrics_choice",
        )
        if st.button("Dùng phiên bản này", type="primary", use_container_width=True):
            selected = candidates[choice]
            manifest["lyrics"] = selected["segments"]
            manifest["lyrics_source"] = selected["source"]
            manifest["lyrics_timing"] = selected["timing"]
            manifest["reviewed"] = False
            st.session_state.pop("online_lyrics_candidates", None)
            st.rerun()
    if manifest.get("lyrics"):
        source = manifest.get("lyrics_source") or "Nhập thủ công"
        timing = "đã có mốc thời gian" if manifest.get("lyrics_timing") == "synced" else "mốc thời gian ước lượng"
        info_col.success(f"Đã có {len(manifest['lyrics'])} dòng · {source} · {timing}")
    else:
        info_col.info("Chưa có lời. Hãy tìm online trước.")

    with st.expander("Không tìm thấy lời online? Dùng AI hoặc dán lời", expanded=not bool(manifest.get("lyrics"))):
        whisper_ready = step3_subtitle_provider.transcriber.is_whisper_installed()
        fallback_col, paste_col = st.columns(2, gap="large")
        with fallback_col:
            model = st.selectbox("Model Whisper", ["tiny", "base", "small"], index=1)
            if st.button("Tạo lời bằng AI", use_container_width=True, disabled=not whisper_ready):
                try:
                    with st.spinner("AI đang nhận dạng lời hát..."):
                        manifest["lyrics"] = step3_subtitle_provider.run_transcription(audio_file, model_name=model)
                    manifest["lyrics_source"] = "Whisper (AI)"
                    manifest["lyrics_timing"] = "synced"
                    manifest["reviewed"] = False
                    st.rerun()
                except Exception as exc:
                    st.error(f"Không thể nhận dạng lời: {exc}")
            if not whisper_ready:
                st.caption("Cần cài openai-whisper để dùng lựa chọn này.")
        with paste_col:
            pasted = st.text_area("Hoặc dán lời (mỗi dòng một câu)", height=135, key="simple_pasted_lyrics")
            if st.button("Dùng lời đã dán", use_container_width=True):
                manifest["lyrics"] = _simple_lyrics_to_segments(pasted, duration)
                manifest["lyrics_source"] = "Nhập thủ công"
                manifest["lyrics_timing"] = "estimated"
                manifest["reviewed"] = False
                st.rerun()

    lyrics = manifest.get("lyrics") or []
    if lyrics:
        current_text = "\n".join(str(item.get("text") or "") for item in lyrics)
        edited_text = st.text_area("Lời bài hát", value=current_text, height=280, help="Mỗi dòng tương ứng một câu phụ đề.")
        edit_col, translate_col = st.columns(2)
        if edit_col.button("Áp dụng nội dung đã sửa", use_container_width=True):
            manifest["lyrics"] = _simple_lyrics_to_segments(edited_text, duration)
            manifest["lyrics_timing"] = "estimated"
            manifest["reviewed"] = False
            st.rerun()
        language = manifest.get("language", "zh")
        if translate_col.button("Dịch nghĩa & Pinyin", use_container_width=True):
            with st.spinner("Đang dịch lời bài hát..."):
                manifest["lyrics"] = step3_subtitle_provider.auto_translate_and_pinyin(lyrics, language, track)
            st.rerun()

    with st.expander("Tùy chỉnh kiểu chữ", expanded=False):
        style = manifest.setdefault("style", {})
        style["font_name"] = st.text_input("Font", value=style.get("font_name", "Arial"))
        style["font_size_original"] = st.slider("Cỡ chữ", 20, 56, int(style.get("font_size_original", 32)))
        style["margin_v"] = st.slider("Cách mép dưới", 10, 200, int(style.get("margin_v", 60)))

    image_path = st.session_state.get("image_path")
    effect_path = st.session_state.get("effect_path")
    if lyrics and image_path and effect_path and Path(image_path).is_file() and Path(effect_path).is_file():
        st.markdown("#### Live Preview")
        try:
            effect_live_preview(
                image_path,
                effect_path,
                motion_mode=st.session_state.get("motion_mode", "smooth_zoom"),
                quality="fast",
                text_profile=st.session_state.get("text_profile"),
                height=380,
            )
        except Exception as exc:
            st.warning(f"Live Preview phụ đề không khả dụng: {exc}")
        if st.button("▶ Tạo preview phụ đề 10 giây", use_container_width=True):
            try:
                manifest["reviewed"] = True
                step3_subtitle_provider.save_project_subtitles(project_id, manifest)
                step3_subtitle_provider.generate_subtitles_file(project_id, manifest["lyrics"], manifest["style"])
                preview_path = config.TEMP_IMAGE_DIR / f"subtitle_preview_{project_id}.mp4"
                with st.spinner("Đang ghép hiệu ứng và phụ đề để xem trước..."):
                    step4_render.build_effect_preview(
                        Path(image_path), Path(effect_path), preview_path, duration=10,
                        motion_mode=st.session_state.get("motion_mode", "smooth_zoom"),
                        text_profile=st.session_state.get("text_profile"), project_id=project_id,
                    )
                st.session_state["subtitle_preview_path"] = str(preview_path)
            except Exception as exc:
                st.error(f"Không tạo được preview: {exc}")
        preview_path = st.session_state.get("subtitle_preview_path")
        if preview_path and Path(preview_path).is_file():
            st.caption("Preview FFmpeg 10 giây")
            st.video(preview_path)
    save_col, back_col = st.columns(2)
    if save_col.button("Lưu phụ đề & tiếp tục →", type="primary", use_container_width=True, disabled=not bool(lyrics)):
        manifest["reviewed"] = True
        step3_subtitle_provider.save_project_subtitles(project_id, manifest)
        step3_subtitle_provider.generate_subtitles_file(project_id, manifest["lyrics"], manifest["style"])
        _go_to_step(6)
    if back_col.button("← Quay lại ảnh nền", use_container_width=True):
        _go_to_step(4)

def _simple_lyrics_to_segments(raw_text: str, duration: float) -> list[dict]:
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    segment_duration = duration / max(1, len(lines))
    return [
        {"start": index * segment_duration, "end": (index + 1) * segment_duration, "text": line,
         "pinyin": "", "vietnamese": "", "words": []}
        for index, line in enumerate(lines)
    ]

def render_subtitle_wizard_step():
    """Trang quản lý phụ đề: tách vocal, transcribe, dịch và chỉnh sửa lời bài hát."""
    import step3_subtitle_provider
    from core.media.probe import MediaProbe
    import os
    
    st.markdown("## Tạo & Duyệt phụ đề Karaoke")
    track = st.session_state.get("selected_track")
    if not track:
        st.error("Chưa chọn bài nhạc. Hãy quay lại bước 2.")
        if st.button("← Quay lại", use_container_width=True):
            _go_to_step(2)
        st.stop()
        
    project_id = st.session_state.get("project_id", "lofi_default_prj")
    track_id = track.get("track_id")
    audio_file = config.INPUT_AUDIO_DIR / f"{track_id}.m4a"
    
    # Load manifest nếu chưa có trong session_state
    if "subtitle_manifest_data" not in st.session_state or st.session_state.get("subtitle_project_id") != project_id:
        st.session_state["subtitle_manifest_data"] = step3_subtitle_provider.load_project_subtitles(project_id)
        st.session_state["subtitle_project_id"] = project_id
        
    m = st.session_state["subtitle_manifest_data"]
    title = str(track.get("title") or "")
    artist = str(track.get("author") or "")
    st.info("Ưu tiên dùng lời bài hát đã xuất bản trên mạng. AI/Whisper chỉ dùng khi không tìm được hoặc cần căn thời gian chính xác hơn.")
    online_col, status_col = st.columns([1, 1.5], gap="medium")
    if online_col.button("🔎 Tìm lời bài hát online", type="primary", use_container_width=True):
        duration = 180.0
        try:
            duration = float(MediaProbe.probe_media(audio_file).get("duration_seconds") or duration)
        except Exception:
            pass
        with st.spinner(f"Đang tìm lời của {title}..."):
            result = step3_subtitle_provider.find_online_lyrics(title, artist, duration)
        if result.get("found"):
            m["lyrics"] = result["segments"]
            m["lyrics_source"] = result["source"]
            m["lyrics_timing"] = result["timing"]
            m["reviewed"] = False
            st.success(f"Đã lấy {len(result['segments'])} dòng từ {result['source']}.")
            st.rerun()
        else:
            st.warning(result.get("reason", "Chưa tìm thấy lời online. Hãy dùng AI dự phòng bên dưới."))
    if m.get("lyrics_source"):
        timing_label = "có timestamp" if m.get("lyrics_timing") == "synced" else "đã chia thời gian ước lượng"
        status_col.success(f"Nguồn hiện tại: {m['lyrics_source']} · {timing_label}")
    else:
        status_col.caption("Chưa có lời. Nếu tìm không thấy, dùng AI dự phòng ở cột trái.")
    
    # Nút điều hướng chính
    control_col, editor_col = st.columns([0.8, 1.2], gap="large")
    
    with control_col:
        # Cấu hình chính
        st.markdown("#### Công cụ & Cài đặt")
        enabled = st.checkbox("Bật phụ đề Karaoke cho video này", value=m.get("enabled", True))
        m["enabled"] = enabled
        
        language = st.selectbox(
            "Ngôn ngữ gốc của bài hát",
            options=[("zh", "Tiếng Trung (Giản/Phồn thể)"), ("en", "Tiếng Anh / Ngôn ngữ khác")],
            index=0 if m.get("language") == "zh" else 1,
            format_func=lambda x: x[1]
        )[0]
        m["language"] = language
        
        st.divider()
        st.markdown("##### 1. Tách Vocal & Beat (Demucs)")
        model_choice = st.selectbox("Model tách", ["htdemucs"], index=0)
        
        vocal_path = step3_subtitle_provider.get_vocals_dir() / f"{track_id}_vocals.wav"
        inst_path = step3_subtitle_provider.get_vocals_dir() / f"{track_id}_instrumental.wav"
        
        has_vocal = vocal_path.is_file() and vocal_path.stat().st_size > 1024 * 1024
        
        demucs_installed = step3_subtitle_provider.separator.is_demucs_installed()
        if not demucs_installed:
            st.info("Chưa cài `demucs` trên máy. Nhận dạng lời vẫn hoạt động trên nhạc gốc (hoặc dán lời thủ công).")
            
        col1, col2 = st.columns(2)
        with col1:
            if st.button("Tách âm", type="primary", use_container_width=True, disabled=not demucs_installed):
                try:
                    with st.spinner("Đang tách vocal (mất khoảng 1-3 phút)..."):
                        step3_subtitle_provider.run_vocal_separation(
                            audio_path=audio_file,
                            model=model_choice,
                            progress_callback=lambda p, msg: logger.info(f"[UI] {msg} ({p:.0%})")
                        )
                    st.success("Tách vocal thành công!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Lỗi: {e}")
                    
        with col2:
            if st.button("Bỏ tách", use_container_width=True, disabled=not has_vocal):
                vocal_path.unlink(missing_ok=True)
                inst_path.unlink(missing_ok=True)
                st.rerun()
                
        if has_vocal:
            st.caption("🎧 Nghe thử Vocal đã tách:")
            st.audio(str(vocal_path), format="audio/wav")
            
        st.divider()
        st.markdown("##### 2. Nhận dạng lời (Whisper)")
        whisper_model = st.selectbox("Whisper model", ["tiny", "base", "small"], index=1)
        whisper_installed = step3_subtitle_provider.transcriber.is_whisper_installed()
        if not whisper_installed:
            st.info("Chưa cài `openai-whisper` trên máy. Bạn có thể dán lời thô thủ công ở cột bên phải.")
            
        if st.button("Tự động nhận dạng lời", type="primary", use_container_width=True, disabled=not whisper_installed):
            target_audio = vocal_path if has_vocal else audio_file
            try:
                with st.spinner("Whisper đang nghe và nhận dạng lời hát..."):
                    segments = step3_subtitle_provider.run_transcription(
                        vocal_path=target_audio,
                        model_name=whisper_model,
                        language=language if language == "zh" else None,
                        progress_callback=lambda p, msg: logger.info(f"[UI Whisper] {msg} ({p:.0%})")
                    )
                m["lyrics"] = segments
                st.success(f"Nhận dạng xong {len(segments)} dòng!")
                st.rerun()
            except Exception as e:
                st.error(f"Lỗi nhận dạng: {e}")
                
        st.divider()
        st.markdown("##### 3. Dịch nghĩa & Phiên âm")
        if st.button("Tự động dịch & Pinyin", type="primary", use_container_width=True, disabled=not m.get("lyrics")):
            try:
                with st.spinner("AI đang tạo Pinyin và dịch nghĩa tiếng Việt..."):
                    updated = step3_subtitle_provider.auto_translate_and_pinyin(
                        m["lyrics"],
                        source_language=language,
                        song_info=track
                    )
                m["lyrics"] = updated
                st.success("Đã hoàn tất dịch thuật!")
                st.rerun()
            except Exception as e:
                st.error(f"Lỗi dịch thuật: {e}")
                
        st.divider()
        st.markdown("##### 4. Định dạng chữ (Style)")
        style = m["style"]
        style["font_name"] = st.text_input("Tên Font", value=style.get("font_name", "Arial"))
        col_s1, col_s2 = st.columns(2)
        style["font_size_original"] = col_s1.number_input("Cỡ câu gốc", value=int(style.get("font_size_original", 32)), step=1)
        style["font_size_translation"] = col_s2.number_input("Cỡ câu dịch", value=int(style.get("font_size_translation", 20)), step=1)
        
        col_c1, col_c2, col_c3 = st.columns(3)
        style["primary_color"] = col_c1.color_picker("Chữ chờ", value=style.get("primary_color", "#FFFFFF"))
        style["secondary_color"] = col_c2.color_picker("Chữ chạy", value=style.get("secondary_color", "#FFC0CB"))
        style["outline_color"] = col_c3.color_picker("Viền", value=style.get("outline_color", "#000000"))
        
        style["margin_v"] = st.slider("Khoảng cách mép dưới (Margin V)", min_value=10, max_value=200, value=int(style.get("margin_v", 60)))
        
        st.divider()
        if st.button("💾 Xuất & Duyệt phụ đề", type="primary", use_container_width=True):
            if m.get("lyrics"):
                m["reviewed"] = True
                step3_subtitle_provider.save_project_subtitles(project_id, m)
                try:
                    step3_subtitle_provider.generate_subtitles_file(project_id, m["lyrics"], style)
                    st.success("Đã duyệt phụ đề và lưu file thành công!")
                except Exception as e:
                    st.error(f"Lỗi ghi file phụ đề: {e}")
            else:
                st.warning("Chưa có nội dung lời bài hát để duyệt.")
                
        if st.button("← Quay lại ảnh nền", use_container_width=True):
            _go_to_step(3)
            
    with editor_col:
        st.markdown("#### Nội dung & Căn chỉnh lời")
        
        # Hộp dán lời thô (nếu không dùng Whisper)
        with st.expander("📝 Dán lời thô thủ công (Bypass Whisper)", expanded=not bool(m.get("lyrics"))):
            raw_lyric_input = st.text_area("Dán lời bài hát thô tại đây (Mỗi dòng một câu)", height=150)
            if st.button("Tách dòng & Phân bổ thời gian đều", use_container_width=True):
                if raw_lyric_input.strip():
                    lines = [line.strip() for line in raw_lyric_input.splitlines() if line.strip()]
                    duration = 180.0
                    try:
                        probe = MediaProbe.probe_media(audio_file)
                        duration = float(probe.get("duration_seconds") or 180.0)
                    except Exception:
                        pass
                    segment_dur = duration / max(1, len(lines))
                    segments = []
                    for idx, line in enumerate(lines):
                        start_time = idx * segment_dur
                        end_time = (idx + 1) * segment_dur
                        words = line.split()
                        words_list = []
                        word_dur = (end_time - start_time) / max(1, len(words))
                        for w_idx, w in enumerate(words):
                            words_list.append({
                                "word": w,
                                "start": start_time + w_idx * word_dur,
                                "end": start_time + (w_idx + 1) * word_dur
                            })
                        segments.append({
                            "start": start_time,
                            "end": end_time,
                            "text": line,
                            "pinyin": "",
                            "vietnamese": "",
                            "words": words_list
                        })
                    m["lyrics"] = segments
                    st.success(f"Đã tạo {len(segments)} dòng thô!")
                    st.rerun()
                else:
                    st.warning("Hãy dán nội dung lời bài hát.")
                    
        # Danh sách câu hát
        lyrics = m.get("lyrics") or []
        if not lyrics:
            st.info("Chưa có dữ liệu lời bài hát. Hãy nhận dạng hoặc dán lời thủ công ở trên.")
        else:
            st.markdown(f"**Danh sách các câu ({len(lyrics)} dòng):**")
            
            lines_per_page = 10
            num_pages = len(lyrics) // lines_per_page + (1 if len(lyrics) % lines_per_page > 0 else 0)
            
            if "lyrics_page" not in st.session_state:
                st.session_state.lyrics_page = 1
                
            page = st.selectbox(
                "Trang biên tập",
                options=list(range(1, num_pages + 1)),
                format_func=lambda x: f"Trang {x} / {num_pages} (Dòng {(x-1)*lines_per_page + 1} - {min(len(lyrics), x*lines_per_page)})",
                key="editor_page_select"
            )
            
            start_idx = (page - 1) * lines_per_page
            end_idx = min(len(lyrics), page * lines_per_page)
            
            for idx in range(start_idx, end_idx):
                item = lyrics[idx]
                with st.container(border=True):
                    c1, c2 = st.columns([1, 4])
                    c1.markdown(f"**Dòng {idx + 1}**")
                    
                    if c1.button("▶️ Nghe", key=f"play_seg_{idx}"):
                        try:
                            import subprocess
                            temp_seg_file = config.TEMP_IMAGE_DIR / f"temp_preview_segment_{idx}.mp3"
                            config.TEMP_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
                            subprocess.run([
                                "ffmpeg", "-y", "-ss", str(item["start"]), "-t", str(item["end"] - item["start"]),
                                "-i", str(audio_file), "-q:a", "4", str(temp_seg_file)
                            ], capture_output=True, check=True, timeout=10)
                            st.session_state[f"audio_preview_play_{idx}"] = str(temp_seg_file)
                        except Exception as e:
                            st.error(f"Lỗi cắt preview: {e}")
                            
                    if st.session_state.get(f"audio_preview_play_{idx}"):
                        c1.audio(st.session_state[f"audio_preview_play_{idx}"])
                        
                    time_col1, time_col2 = c2.columns(2)
                    item["start"] = float(time_col1.number_input("Bắt đầu (giây)", value=float(item["start"]), step=0.1, key=f"start_{idx}"))
                    item["end"] = float(time_col2.number_input("Kết thúc (giây)", value=float(item["end"]), step=0.1, key=f"end_{idx}"))
                    
                    item["text"] = c2.text_input("Lời gốc", value=item.get("text", ""), key=f"orig_{idx}")
                    if language == "zh":
                        item["pinyin"] = c2.text_input("Phiên âm Pinyin", value=item.get("pinyin", ""), key=f"pin_{idx}")
                    item["vietnamese"] = c2.text_input("Dịch nghĩa Việt", value=item.get("vietnamese", ""), key=f"viet_{idx}")
                    
                    op_col1, op_col2 = c2.columns(2)
                    if op_col1.button("🗑️ Xóa dòng này", key=f"del_{idx}", use_container_width=True):
                        lyrics.pop(idx)
                        st.session_state["subtitle_manifest_data"]["lyrics"] = lyrics
                        st.rerun()
                    if op_col2.button("➕ Thêm dòng mới phía dưới", key=f"add_{idx}", use_container_width=True):
                        new_item = {
                            "start": item["end"],
                            "end": item["end"] + 3.0,
                            "text": "",
                            "pinyin": "",
                            "vietnamese": "",
                            "words": []
                        }
                        lyrics.insert(idx + 1, new_item)
                        st.session_state["subtitle_manifest_data"]["lyrics"] = lyrics
                        st.rerun()
                        
            if st.button("➕ Thêm dòng mới ở cuối danh sách", use_container_width=True):
                last_end = lyrics[-1]["end"] if lyrics else 0.0
                lyrics.append({
                    "start": last_end,
                    "end": last_end + 3.0,
                    "text": "",
                    "pinyin": "",
                    "vietnamese": "",
                    "words": []
                })
                st.session_state["subtitle_manifest_data"]["lyrics"] = lyrics
                st.rerun()
                
        st.divider()
        if st.button("Xác nhận & Sang bước chọn hiệu ứng →", type="primary", use_container_width=True):
            step3_subtitle_provider.save_project_subtitles(project_id, m)
            try:
                step3_subtitle_provider.generate_subtitles_file(project_id, m["lyrics"], m["style"])
            except Exception:
                pass
            _go_to_step(5)

def _ensure_effect_off_video() -> Path:
    """Tạo overlay đen trung tính để renderer giữ nguyên ảnh khi hiệu ứng tắt."""
    import subprocess
    path = config.EFFECTS_DIR / "effect_off.mp4"
    if path.exists() and path.stat().st_size > 1024:
        return path
    config.EFFECTS_DIR.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i",
        "color=c=black:s=1920x1080:r=24:d=2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
    ], capture_output=True, check=True, timeout=60)
    return path


def _effect_display_name(path: Path) -> str:
    """Tên hiệu ứng ngắn, dễ đọc trên giao diện."""
    name = Path(path).stem.lower()
    labels = {
        "effect_rain_fall": "Mưa rơi",
        "effect_snow_fall": "Tuyết rơi",
        "effect_dust_soft": "Bụi sáng nhẹ",
        "effect_retro_scanline": "Retro scanline",
        "effect_light_film_grain": "Film grain",
        "default_effect": "Mặc định",
    }
    if name in labels:
        return labels[name]
    return name.replace("effect_", "").replace("pexels_", "").replace("_", " ").title()


EFFECT_TYPE_LABELS = {
    "auto": "Tự động (theo phân tích)",
    "screen_black": "Nền đen - Screen",
    "chroma_key": "Phông xanh - Chroma key",
    "alpha": "Có kênh alpha",
    "normal": "Video thường - Giữ nguyên",
}
BLEND_MODE_LABELS = {
    "normal": "Bình thường (khuyên dùng)",
    "screen": "Screen",
    "lighten": "Lighten",
    "overlay": "Overlay",
    "soft-light": "Soft light",
}


def _resolve_pixabay_api_key() -> tuple[str, str]:
    """Một API key duy nhất: .env/config → st.secrets → ô nhập. Trả về (key, nguồn)."""
    env_key = ""
    try:
        env_file = config.BASE_DIR / ".env"
        if env_file.is_file():
            for line in env_file.read_text(encoding="utf-8-sig").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    k, v = k.strip(), v.strip().strip('"').strip("'")
                    if k == "PIXABAY_API_KEY" and v:
                        env_key = v
                        os.environ["PIXABAY_API_KEY"] = v
                        config.PIXABAY_API_KEY = v
                        break
    except Exception:
        pass

    key = env_key or str(getattr(config, "PIXABAY_API_KEY", "") or os.getenv("PIXABAY_API_KEY", "") or "").strip()
    if key:
        return key, "env"
    try:
        key = str(st.secrets.get("PIXABAY_API_KEY", "") or "").strip()
    except Exception:
        key = ""
    if key:
        return key, "secrets"
    return str(st.session_state.get("pixabay_api_key_input") or "").strip(), "input"


def _selected_effect_metadata(effect_path) -> dict:
    try:
        return step3_effect_provider.get_effect_metadata(effect_path)
    except Exception:
        return {}


def _resolved_effect_type(metadata: dict) -> str:
    """effect_type thực tế sau khi giải nghĩa lựa chọn 'Tự động'."""
    choice = str(st.session_state.get("effect_type_choice", "auto"))
    if choice != "auto":
        return choice
    return str(metadata.get("effect_type") or "screen_black")


def _current_effect_settings(metadata: dict | None = None) -> dict:
    """Bộ thông số compositing thống nhất cho Live Preview, preview FFmpeg và render cuối."""
    metadata = metadata if metadata is not None else _selected_effect_metadata(st.session_state.get("effect_path") or "")
    return {
        "effect_type": _resolved_effect_type(metadata),
        "blend_mode": str(st.session_state.get("effect_live_blend_mode", "normal")),
        "opacity": float(st.session_state.get("effect_live_opacity", 0.55)),
        "speed": float(st.session_state.get("effect_live_speed", 1.0)),
        "key_color": str(st.session_state.get("effect_key_color", "#00FF00")),
        "chroma_similarity": float(st.session_state.get("effect_chroma_similarity", 0.18)),
        "chroma_softness": float(st.session_state.get("effect_chroma_softness", 0.08)),
        "despill": float(st.session_state.get("effect_despill", 0.35)),
        "edge_feather": float(st.session_state.get("effect_edge_feather", 1.5)),
    }


def _apply_recommended_composite(metadata: dict) -> None:
    """Khôi phục thông số từ phân tích local hoặc hồ sơ AI (ưu tiên phân tích)."""
    recommended = dict((metadata or {}).get("recommended_composite") or {})
    profile = st.session_state.get("ai_effect_profile") or {}
    source = recommended or profile
    if not source:
        return
    def _num(key, default):
        value = source.get(key)
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
    st.session_state.effect_live_opacity = _num("opacity", 0.55)
    st.session_state.effect_live_speed = _num("speed", 1.0)
    blend = str(source.get("blend_mode") or "normal")
    st.session_state.effect_live_blend_mode = blend if blend in BLEND_MODE_LABELS else "normal"
    if metadata.get("effect_type"):
        st.session_state.effect_type_choice = "auto"
    if source.get("key_color"):
        st.session_state.effect_key_color = str(source["key_color"])
    st.session_state.effect_chroma_similarity = _num("chroma_similarity", 0.18)
    st.session_state.effect_chroma_softness = _num("chroma_softness", 0.08)
    st.session_state.effect_despill = _num("despill", 0.35)
    st.session_state.effect_edge_feather = _num("edge_feather", 1.5)


def _auto_analyze_selected_effect(selected_effect: Path) -> dict:
    """Phân tích loại nền một lần cho mỗi file (kết quả lưu vào manifest)."""
    metadata = _selected_effect_metadata(selected_effect)
    if metadata.get("effect_type"):
        return metadata
    analyzed_marker = f"analyzed::{selected_effect.name}"
    if st.session_state.get("effect_analyze_marker") == analyzed_marker:
        return metadata
    try:
        with st.spinner("Đang phân tích loại nền của video hiệu ứng..."):
            step3_effect_provider.analyze_effect_type(selected_effect)
        metadata = _selected_effect_metadata(selected_effect)
    except Exception as exc:
        st.session_state.effect_analyze_error = str(exc)
    st.session_state.effect_analyze_marker = analyzed_marker
    return metadata


def _render_effect_candidate_card(candidate: dict, key_prefix: str, index: int) -> None:
    """Card ứng viên: thumbnail, điểm AI, thông số, nguồn/license và nút chọn."""
    is_local = candidate.get("origin") == "local" or bool(candidate.get("file_name"))
    with st.container(border=True):
        thumb = str(candidate.get("thumbnail_url") or "")
        if thumb:
            info_col, thumb_col = st.columns([0.62, 0.38])
            with thumb_col:
                st.image(thumb, use_container_width=True)
        else:
            info_col = st.container()
        with info_col:
            if is_local:
                name = _effect_display_name(Path(str(candidate.get("file_name") or "effect")))
            else:
                name = ", ".join(candidate.get("tags", [])[:4]) or f"Video #{candidate.get('id')}"
            score = candidate.get("ai_score")
            score_text = f" · AI {score}/100" if score is not None else ""
            st.markdown(f"**{name}**{score_text}")
            duration = candidate.get("duration") or candidate.get("duration_seconds")
            size_mb = float(candidate.get("file_size") or 0) / 1024 / 1024
            meta_bits = []
            if duration:
                meta_bits.append(f"{float(duration):.0f} giây")
            if candidate.get("height"):
                meta_bits.append(f"{candidate.get('width') or '?'}×{candidate.get('height')}")
            if size_mb:
                meta_bits.append(f"{size_mb:.1f} MB")
            meta_bits.append("Local" if is_local else "Pixabay")
            st.caption(" · ".join(meta_bits))
            st.caption(str(candidate.get("license_name") or "Pixabay Content License"))
            if candidate.get("ai_reason"):
                st.caption(candidate["ai_reason"])
        source_col, pick_col = st.columns([1.0, 1.0])
        with source_col:
            page_url = candidate.get("page_url") or candidate.get("source_page_url")
            if page_url:
                st.link_button("Xem nguồn", page_url, use_container_width=True)
        with pick_col:
            if is_local:
                if st.button("Chọn", key=f"{key_prefix}_pick_{index}", type="primary", use_container_width=True):
                    local_path = config.EFFECTS_DIR / str(candidate.get("file_name"))
                    if local_path.is_file():
                        _select_effect_path(local_path)
                        st.rerun()
                    st.error("File hiệu ứng không còn trên ổ đĩa.")
            else:
                if st.button("Tải và chọn", key=f"{key_prefix}_dl_{index}", type="primary", use_container_width=True):
                    try:
                        with st.spinner("Đang tải và kiểm tra video..."):
                            downloaded = step3_effect_provider.download_pixabay_effect(candidate)
                        _select_effect_path(downloaded)
                        st.success(f"Đã lưu {downloaded.name}")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Tải hiệu ứng thất bại: {exc}")


def _render_suggest_tab(pixabay_api_key: str) -> None:
    """Tab Đề xuất: AI tạo hồ sơ, xếp hạng local trước, thiếu mới tìm Pixabay."""
    track = st.session_state.get("selected_track") or {}
    try:
        music_tags = track.get("style_tags") or step1_music_hunter.classify_music_styles(track)
    except Exception:
        music_tags = []
    if isinstance(music_tags, str):
        music_tags = [item.strip() for item in music_tags.split(",") if item.strip()]
    mood_text = ", ".join(music_tags[:4]) if music_tags else "chưa phân loại"
    st.caption(f"Nhạc: **{track.get('title', 'Chưa chọn')}** · Mood: {mood_text}")

    image_context = " ".join(filter(None, [
        str(st.session_state.get("image_prompt") or ""),
        str(st.session_state.get("dreamina_prompt_vi") or ""),
    ]))
    if st.button("Phân tích và đề xuất", type="primary", use_container_width=True):
        with st.spinner("AI đang tạo hồ sơ hiệu ứng..."):
            profile = step3_effect_provider.build_ai_effect_profile(track, list(music_tags or []), image_context)
        st.session_state.ai_effect_profile = profile
        _apply_ai_effect_settings(profile)
        with st.spinner("Đang xếp hạng thư viện local và tìm bổ sung..."):
            st.session_state.effect_recommendations = step3_effect_provider.recommend_effects(
                profile, pixabay_api_key
            )
        st.rerun()

    ai_profile = st.session_state.get("ai_effect_profile") or {}
    if ai_profile:
        source_label = "AI" if ai_profile.get("source") == "ai" else "Mapping local dự phòng"
        effect_type_label = EFFECT_TYPE_LABELS.get(str(ai_profile.get("effect_type") or ""), "?")
        st.caption(f"Nguồn đề xuất: {source_label} · Loại asset: {effect_type_label}")
        st.write(ai_profile.get("reason") or "Đã tạo hồ sơ hiệu ứng.")
        st.caption("Query: " + " · ".join(ai_profile.get("queries") or []))
        if ai_profile.get("error"):
            st.warning("AI online chưa khả dụng, đang dùng mapping local. " + str(ai_profile["error"]))

    recommendations = st.session_state.get("effect_recommendations") or {}
    merged = list(recommendations.get("local") or []) + list(recommendations.get("online") or [])
    if recommendations:
        note_bits = [f"{len(recommendations.get('local') or [])} local"]
        if recommendations.get("used_pixabay"):
            note_bits.append(f"{len(recommendations.get('online') or [])} Pixabay")
        else:
            note_bits.append("đủ kết quả local, không gọi Pixabay")
        if recommendations.get("online_error"):
            st.warning(f"Pixabay lỗi: {recommendations['online_error']}")
        st.caption("Ứng viên: " + " · ".join(note_bits) + " · chỉ tải video bạn bấm chọn.")
    if merged:
        for index, candidate in enumerate(merged[:3]):
            _render_effect_candidate_card(candidate, "suggest_top", index)
        if len(merged) > 3:
            with st.expander(f"Xem thêm {len(merged) - 3} kết quả"):
                for index, candidate in enumerate(merged[3:12]):
                    _render_effect_candidate_card(candidate, "suggest_more", index + 3)
    elif recommendations:
        st.info("Chưa có ứng viên phù hợp. Thử tìm thủ công trong tab Thư viện.")


def _render_adjust_tab(selected_effect: Path | None, metadata: dict) -> None:
    """Tab Điều chỉnh: loại hiệu ứng, opacity/tốc độ, chế độ ghép và chroma key."""
    if not selected_effect:
        st.info("Chọn hiệu ứng ở tab Đề xuất hoặc Thư viện trước.")
        return

    detected_type = str(metadata.get("effect_type") or "")
    confidence = metadata.get("detection_confidence")
    if detected_type:
        confidence_text = f" (tin cậy {float(confidence):.0%})" if confidence else ""
        st.caption(
            f"Phân tích: **{EFFECT_TYPE_LABELS.get(detected_type, detected_type)}**{confidence_text}"
            + (f" · nền {metadata.get('detected_background')}" if metadata.get("detected_background") else "")
        )
    else:
        if st.button("Phân tích loại nền video này", use_container_width=True):
            try:
                with st.spinner("Đang phân tích khung hình..."):
                    step3_effect_provider.analyze_effect_type(selected_effect)
                st.rerun()
            except Exception as exc:
                st.error(f"Không phân tích được: {exc}")

    type_keys = list(EFFECT_TYPE_LABELS.keys())
    current_choice = str(st.session_state.get("effect_type_choice", "auto"))
    if current_choice not in type_keys:
        current_choice = "auto"
    choice = st.selectbox(
        "Loại hiệu ứng",
        type_keys,
        index=type_keys.index(current_choice),
        format_func=lambda value: EFFECT_TYPE_LABELS[value],
    )
    st.session_state.effect_type_choice = choice
    resolved_type = _resolved_effect_type(metadata)

    # Cảnh báo chọn sai chế độ với video phông xanh.
    if detected_type == "chroma_key" and resolved_type != "chroma_key":
        st.warning(
            "Video có dấu hiệu phông xanh. Chế độ hiện tại có thể để lại màu xanh. "
            "Hãy chuyển sang Phông xanh - Chroma key."
        )

    st.session_state.effect_live_opacity = st.slider(
        "Độ mạnh hiệu ứng (opacity)", 0.0, 1.0,
        float(st.session_state.get("effect_live_opacity", 0.55)), 0.05,
    )
    speed_col, blend_col = st.columns(2)
    with speed_col:
        current_speed = max(0.5, min(float(st.session_state.get("effect_live_speed", 1.0)), 1.5))
        st.session_state.effect_live_speed = st.slider("Tốc độ", 0.5, 1.5, current_speed, 0.05)
    with blend_col:
        blend_keys = list(BLEND_MODE_LABELS.keys())
        current_blend = str(st.session_state.get("effect_live_blend_mode", "normal"))
        if current_blend not in blend_keys:
            current_blend = "normal"
        blend_disabled = resolved_type in ("chroma_key", "alpha")
        blend = st.selectbox(
            "Chế độ hòa trộn",
            blend_keys,
            index=blend_keys.index(current_blend),
            format_func=lambda value: BLEND_MODE_LABELS[value],
            disabled=blend_disabled,
            help="Chroma key và video alpha luôn ghép theo kênh alpha nên không cần blend.",
        )
        st.session_state.effect_live_blend_mode = blend

    if resolved_type == "chroma_key":
        st.markdown("##### Thông số chroma key")
        st.session_state.effect_key_color = st.color_picker(
            "Màu phông", value=str(st.session_state.get("effect_key_color", "#00FF00")),
        )
        chroma_col_a, chroma_col_b = st.columns(2)
        with chroma_col_a:
            st.session_state.effect_chroma_similarity = st.slider(
                "Similarity", 0.05, 0.50, float(st.session_state.get("effect_chroma_similarity", 0.18)), 0.01,
                help="Cao hơn xóa nhiều màu hơn nhưng dễ ăn vào chủ thể.",
            )
            st.session_state.effect_despill = st.slider(
                "Despill", 0.0, 1.0, float(st.session_state.get("effect_despill", 0.35)), 0.05,
                help="Khử viền ám xanh trên mép chủ thể.",
            )
        with chroma_col_b:
            st.session_state.effect_chroma_softness = st.slider(
                "Softness", 0.0, 0.40, float(st.session_state.get("effect_chroma_softness", 0.08)), 0.01,
                help="Mép alpha chuyển mềm thay vì cắt cứng.",
            )
            st.session_state.effect_edge_feather = st.slider(
                "Feather viền (px)", 0.0, 5.0, float(st.session_state.get("effect_edge_feather", 1.5)), 0.1,
                help="Chỉ áp trong FFmpeg preview/render; Live Preview chưa hỗ trợ.",
            )
        matte_col, quality_col = st.columns(2)
        with matte_col:
            st.session_state.effect_show_matte = st.toggle(
                "Xem matte", value=bool(st.session_state.get("effect_show_matte", False)),
                help="Hiện mặt nạ đen trắng để kiểm tra vùng bị xóa.",
            )
        with quality_col:
            quality_label = st.radio(
                "Chất lượng Live Preview",
                ["Nhanh 640×360", "Rõ 960×540"],
                index=1 if st.session_state.get("effect_preview_quality") == "sharp" else 0,
                horizontal=True,
            )
            st.session_state.effect_preview_quality = "sharp" if "960" in quality_label else "fast"

    if st.button("Khôi phục thông số đề xuất", use_container_width=True):
        _apply_recommended_composite(metadata)
        st.rerun()


def _render_library_tab(effects: list[Path], selected_effect: Path | None, pixabay_api_key: str, key_source: str, manifest_summary: dict | None, image_value) -> None:
    """Tab Thư viện: chọn local, tìm Pixabay thủ công, quản trị manifest, phân tích cảnh."""
    if effects:
        current_name = Path(st.session_state.effect_path).name if st.session_state.get("effect_path") else effects[0].name
        current_index = next((i for i, item in enumerate(effects) if item.name == current_name), 0)
        selected_name = st.selectbox(
            "Hiệu ứng trong thư viện",
            options=[item.name for item in effects],
            index=current_index,
            format_func=lambda value: _effect_display_name(Path(value)),
        )
        chosen = next(item for item in effects if item.name == selected_name)
        if st.session_state.get("effect_path") != str(chosen.resolve()):
            _select_effect_path(chosen)
            st.rerun()
    else:
        st.warning("Chưa có hiệu ứng trong thư viện.")
    if st.button("Tạo bộ hiệu ứng mẫu (mưa, tuyết, bụi...)", use_container_width=True):
        with st.spinner("Đang tạo hiệu ứng mẫu..."):
            step3_effect_provider.create_builtin_effect_pack()
        st.rerun()

    st.markdown("##### Tìm thủ công trên Pixabay")
    if key_source == "input":
        st.text_input(
            "Pixabay API key", type="password", key="pixabay_api_key_input",
            help="Lấy key miễn phí tại pixabay.com/api/docs. Ưu tiên đặt PIXABAY_API_KEY trong .env; key không được lưu vào app state hay manifest.",
        )
        pixabay_api_key = str(st.session_state.get("pixabay_api_key_input") or "").strip()
    else:
        st.caption("Pixabay API: **Đã kết nối** (" + (".env" if key_source == "env" else "st.secrets") + ")")

    track = st.session_state.get("selected_track") or {}
    default_effect_query = suggest_effect_query_from_track(track)
    search_query = st.text_input(
        "Từ khóa hiệu ứng", value=default_effect_query, key="pixabay_effect_query",
        help="Nói rõ loại asset: rain green screen, smoke black background overlay...",
    )
    if st.button("Tìm hiệu ứng Pixabay", use_container_width=True):
        try:
            with st.spinner("Đang tìm metadata Pixabay..."):
                st.session_state.manual_pixabay_results = _search_pixabay_effects_cached(
                    search_query.strip(), pixabay_api_key
                )
            if not st.session_state.manual_pixabay_results:
                st.info("Pixabay không trả về kết quả phù hợp.")
        except Exception as exc:
            st.session_state.manual_pixabay_results = []
            st.error(f"Không tìm được hiệu ứng: {exc}")
    manual_results = st.session_state.get("manual_pixabay_results") or []
    for result_index, candidate in enumerate(manual_results[:6]):
        _render_effect_candidate_card(candidate, "manual", result_index)

    with st.expander("Quản lý manifest (nâng cao)", expanded=False):
        if manifest_summary:
            st.caption(
                f"Manifest: {manifest_summary['ready']} sẵn sàng · "
                f"{manifest_summary['missing']} thất lạc · "
                f"{manifest_summary['duplicate_hashes']} trùng nội dung"
            )
        records = step3_effect_provider.list_effect_records(include_missing=True)
        for record in records:
            record_status = "Sẵn sàng" if record.get("status") == "ready" else "Thiếu file"
            provider = str(record.get("provider") or "local").title()
            license_name = str(record.get("license_name") or "Chưa xác định")
            type_label = EFFECT_TYPE_LABELS.get(str(record.get("effect_type") or ""), "chưa phân tích")
            st.caption(f"{record.get('file_name')} · {provider} · {record_status} · {type_label} · {license_name}")
        manifest_action_col, cleanup_action_col = st.columns(2)
        with manifest_action_col:
            if st.button("Quét lại manifest", use_container_width=True):
                try:
                    step3_effect_provider.sync_effect_manifest(calculate_hashes=True)
                    st.success("Đã đồng bộ lại manifest.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Không đồng bộ được manifest: {exc}")
        with cleanup_action_col:
            if st.button("Dọn mục thất lạc", use_container_width=True):
                removed = step3_effect_provider.remove_missing_manifest_entries()
                st.success(f"Đã dọn {removed} mục metadata thất lạc.")
                st.rerun()
        if st.button("Phân tích loại nền toàn thư viện", use_container_width=True):
            analyzed = 0
            with st.spinner("Đang phân tích các video hiệu ứng..."):
                for record in step3_effect_provider.list_effect_records():
                    file_path = config.EFFECTS_DIR / str(record.get("file_name"))
                    if record.get("effect_type") or not file_path.is_file():
                        continue
                    try:
                        step3_effect_provider.analyze_effect_type(file_path)
                        analyzed += 1
                    except Exception:
                        continue
            st.success(f"Đã phân tích {analyzed} video.")
            st.rerun()

    with st.expander("AI phân tích cảnh (nâng cao)", expanded=False):
        scene_manifest = st.session_state.get("scene_layers_manifest")
        scene_image_key = str(Path(image_value).resolve())
        scene_ready = bool(
            scene_manifest
            and st.session_state.get("scene_layers_image") == scene_image_key
            and _path_exists((scene_manifest or {}).get("mask_preview_path"))
        )
        force_analysis = st.checkbox("Phân tích lại từ đầu", value=False)
        if st.button("Phân tích ảnh", use_container_width=True):
            try:
                from core.image.scene_layer_processor import SceneLayerProcessor
                environment = SceneLayerProcessor.inspect_environment()
                missing = [name for name in ("torch", "transformers") if not environment.get(name)]
                if missing:
                    raise RuntimeError("Thiếu thư viện AI: " + ", ".join(missing))
                with st.spinner("Đang phân tích các lớp cảnh..."):
                    manifest = SceneLayerProcessor.analyze_scene(
                        Path(image_value),
                        st.session_state.get("project_id", "lofi_default_prj"),
                        force_recreate=force_analysis,
                        model_id=getattr(config, "SCENE_SEGMENTATION_MODEL", None),
                        min_component_ratio=float(getattr(config, "SCENE_MASK_MIN_COMPONENT_RATIO", 0.00035)),
                        feather_radius=int(getattr(config, "SCENE_MASK_FEATHER_RADIUS", 2)),
                    )
                st.session_state.scene_layers_manifest = manifest
                st.session_state.scene_layers_image = scene_image_key
                st.session_state.scene_analysis_error = None
                save_persisted_app_state()
                st.rerun()
            except Exception as exc:
                st.session_state.scene_analysis_error = str(exc)
                st.error(f"Phân tích thất bại: {exc}")
        if scene_ready:
            coverage = scene_manifest.get("coverage") or {}
            st.image(str(scene_manifest["mask_preview_path"]), use_container_width=True)
            st.caption(
                f"Lá gần {float(coverage.get('leaves_near', 0))*100:.1f}% · "
                f"Lá giữa {float(coverage.get('leaves_mid', 0))*100:.1f}% · "
                f"Kiến trúc {float(coverage.get('architecture', 0))*100:.1f}% · "
                f"Bầu trời {float(coverage.get('sky', 0))*100:.1f}%"
            )
        elif st.session_state.get("scene_analysis_error"):
            st.warning(st.session_state.scene_analysis_error)


def _render_text_tab() -> None:
    from core.text import provider as text_effect_provider
    from core.text.effect_manifest import save_text_profile
    
    project_id = st.session_state.get("project_id", "lofi_default_prj")
    track = st.session_state.get("selected_track") or {}
    
    # Initialize text_profile in session state if not present
    if "text_profile" not in st.session_state or st.session_state["text_profile"] is None:
        from core.text.effect_manifest import load_text_profile
        loaded = load_text_profile(project_id)
        if loaded:
            st.session_state["text_profile"] = text_effect_provider.normalize_text_profile(loaded)
        else:
            st.session_state["text_profile"] = text_effect_provider.default_text_profile()
            
    p = st.session_state["text_profile"]
    
    enabled = st.checkbox("Bật chữ động hiển thị trên video", value=p.get("enabled", True), key="text_enabled_cb")
    p["enabled"] = enabled
    
    if enabled:
        st.markdown("---")
        col1, col2 = st.columns(2)
        with col1:
            content_type = st.selectbox(
                "Nội dung hiển thị",
                options=list(text_effect_provider.CONTENT_TYPES.keys()),
                format_func=lambda k: text_effect_provider.CONTENT_TYPES[k],
                index=list(text_effect_provider.CONTENT_TYPES.keys()).index(p.get("content_type", "track_title"))
            )
            p["content_type"] = content_type
            
            # Default content logic based on content type
            default_val = ""
            if content_type == "track_title":
                default_val = track.get("title", "")
            elif content_type == "artist":
                default_val = track.get("artist", "")
            elif content_type == "topic":
                default_val = track.get("vibe", "") or "Chill Lo-Fi Beat"
            elif content_type == "short_desc":
                default_val = "Thư giãn đầu óc với bản nhạc này..."
            elif content_type == "intro_message":
                default_val = "Chào mừng bạn đến với kênh lofi chill."
            elif content_type == "series":
                default_val = "Lo-Fi Collection"
            else:
                default_val = p.get("content", "")
                
            content_input_val = p.get("content") or default_val
            
            content = st.text_area("Nội dung chi tiết", value=content_input_val, max_chars=200, key="text_content_input")
            p["content"] = content
            
        with col2:
            st.caption("🪄 Tự động đề xuất bằng AI")
            if st.button("AI gợi ý phong cách & hiệu ứng", use_container_width=True):
                with st.spinner("AI đang gợi ý..."):
                    music_tags = track.get("style_tags") or []
                    if isinstance(music_tags, str):
                        music_tags = [item.strip() for item in music_tags.split(",") if item.strip()]
                    image_context = st.session_state.get("image_prompt", "")
                    
                    ai_profile = text_effect_provider.build_ai_text_profile(
                        track=track,
                        music_tags=list(music_tags),
                        image_context=image_context,
                        content=content,
                        current=p
                    )
                    st.session_state["text_profile"] = ai_profile
                    save_text_profile(project_id, ai_profile)
                    st.session_state.effect_preview_path = None
                    st.session_state.effect_preview_key = None
                    st.success("Đã áp dụng gợi ý từ AI!")
                    st.rerun()
                    
        st.markdown("##### 🎨 Kiểu dáng & Định vị")
        col3, col4, col5 = st.columns(3)
        with col3:
            preset = st.selectbox(
                "Preset kiểu chữ",
                options=text_effect_provider.STYLE_PRESETS,
                index=text_effect_provider.STYLE_PRESETS.index(p.get("preset", "minimal"))
            )
            p["preset"] = preset
            
            font_style = st.selectbox(
                "Kiểu font chữ",
                options=text_effect_provider.FONT_STYLES,
                index=text_effect_provider.FONT_STYLES.index(p.get("font_style", "sans")),
                help="sans (Segoe UI), serif (Times New Roman), display (Arial Black)"
            )
            p["font_style"] = font_style
            
        with col4:
            font_size = st.slider("Cỡ chữ", min_value=16, max_value=200, value=int(p.get("font_size", 72)))
            p["font_size"] = font_size
            
            bold = st.checkbox("In đậm chữ", value=p.get("bold", True))
            p["bold"] = bold
            
        with col5:
            from core.text.effect_renderer import POSITIONS
            position = st.selectbox(
                "Vị trí hiển thị",
                options=list(POSITIONS.keys()),
                index=list(POSITIONS.keys()).index(p.get("position", "bottom_center"))
            )
            p["position"] = position
            
        col6, col7, col8 = st.columns(3)
        with col6:
            text_color = st.color_picker("Màu chữ", value=p.get("text_color", "#FFFFFF"))
            p["text_color"] = text_color
        with col7:
            outline_color = st.color_picker("Màu viền chữ", value=p.get("outline_color", "#101820"))
            p["outline_color"] = outline_color
        with col8:
            outline_width = st.slider("Độ dày viền", min_value=0.0, max_value=8.0, value=float(p.get("outline_width", 2.0)), step=0.5)
            p["outline_width"] = outline_width
            
        st.markdown("##### ⏱️ Hiệu ứng xuất hiện & thời gian")
        col9, col10 = st.columns(2)
        with col9:
            from core.text.effect_renderer import INTRO_EFFECTS, HOLD_EFFECTS
            intro_effect = st.selectbox(
                "Hiệu ứng xuất hiện (Intro)",
                options=INTRO_EFFECTS,
                index=INTRO_EFFECTS.index(p.get("intro_effect", "fade"))
            )
            p["intro_effect"] = intro_effect
            
            intro_duration = st.slider(
                "Thời gian xuất hiện (s)",
                min_value=0.0, max_value=5.0,
                value=float(p.get("intro_duration", 0.8)), step=0.1
            )
            p["intro_duration"] = intro_duration
            
            hold_effect = st.selectbox(
                "Hiệu ứng trong lúc hiển thị",
                options=HOLD_EFFECTS,
                index=HOLD_EFFECTS.index(p.get("hold_effect", "soft_glow"))
            )
            p["hold_effect"] = hold_effect
            
        with col10:
            from core.text.effect_renderer import OUTRO_EFFECTS
            outro_effect = st.selectbox(
                "Hiệu ứng biến mất (Outro)",
                options=OUTRO_EFFECTS,
                index=OUTRO_EFFECTS.index(p.get("outro_effect", "fade"))
            )
            p["outro_effect"] = outro_effect
            
            outro_duration = st.slider(
                "Thời gian biến mất (s)",
                min_value=0.0, max_value=5.0,
                value=float(p.get("outro_duration", 1.0)), step=0.1
            )
            p["outro_duration"] = outro_duration
            
        col11, col12 = st.columns(2)
        with col11:
            start_seconds = st.number_input(
                "Thời điểm xuất hiện (giây thứ)",
                min_value=0.0, value=float(p.get("start_seconds", 0.0)), step=1.0
            )
            p["start_seconds"] = start_seconds
        with col12:
            end_seconds_raw = p.get("end_seconds")
            end_seconds_val = float(end_seconds_raw) if end_seconds_raw not in (None, "") else 0.0
            use_end_limit = st.checkbox("Đặt thời điểm biến mất", value=(end_seconds_raw not in (None, "")))
            if use_end_limit:
                end_seconds = st.number_input(
                    "Thời điểm biến mất (giây thứ)",
                    min_value=start_seconds + 0.1,
                    value=max(start_seconds + 1.0, end_seconds_val),
                    step=1.0
                )
                p["end_seconds"] = end_seconds
            else:
                p["end_seconds"] = None
                st.caption("Chạy hết thời lượng video.")
                
    st.session_state["text_profile"] = p
    save_text_profile(project_id, p)


def render_effect_wizard_step():
    """Bước hiệu ứng: 3 tab Đề xuất / Điều chỉnh / Thư viện, preview cố định bên phải."""
    st.markdown("## Hiệu ứng")
    image_value = st.session_state.get("image_path")
    if not _path_exists(image_value):
        st.warning("Hãy chọn ảnh nền trước.")
        if st.button("← Quay lại ảnh nền", use_container_width=True):
            _go_to_step(3)
        st.stop()

    control_col, preview_col = st.columns([0.9, 1.1], gap="medium")

    with control_col:
        scene_manifest = st.session_state.get("scene_layers_manifest") or {}
        parallax_ready = bool(
            scene_manifest.get("layer_paths")
            or scene_manifest.get("layers")
            or scene_manifest.get("render_layers")
        )
        current_motion = st.session_state.get("motion_mode", "smooth_zoom")
        motion_col, toggle_col = st.columns(2)
        with motion_col:
            motion_label = st.radio(
                "Chuyển động ảnh",
                ["Đung đưa nhẹ", "Parallax nhiều lớp"],
                index=1 if current_motion == "parallax" else 0,
                key="effect_motion_mode",
            )
        requested_motion = "parallax" if motion_label == "Parallax nhiều lớp" else "smooth_zoom"
        if requested_motion == "parallax" and not parallax_ready:
            st.session_state.motion_mode = "smooth_zoom"
            st.warning("Parallax chưa có layer tách nền. Preview đang dùng Đung đưa nhẹ.")
        else:
            st.session_state.motion_mode = requested_motion
        with toggle_col:
            mode = st.radio(
                "Hiệu ứng phủ",
                ["Không hiệu ứng", "Dùng hiệu ứng"],
                index=1 if st.session_state.get("effect_enabled", False) else 0,
            )
        enabled = mode == "Dùng hiệu ứng"
        st.session_state.effect_enabled = enabled

        selected_effect = None
        metadata: dict = {}
        if not enabled:
            st.session_state.effect_path = str(_ensure_effect_off_video().resolve())
            st.session_state.effect_preview_path = None
            st.session_state.effect_preview_key = None
            st.info("Ảnh nền được giữ nguyên, không phủ hiệu ứng.")
        else:
            try:
                manifest_summary = step3_effect_provider.sync_effect_manifest()
            except Exception as manifest_error:
                manifest_summary = None
                st.warning(f"Chưa đồng bộ được manifest hiệu ứng: {manifest_error}")
            effects = (
                step3_effect_provider.list_effect_videos()
                if hasattr(step3_effect_provider, "list_effect_videos")
                else sorted(config.EFFECTS_DIR.glob("*.mp4"))
            )
            effects = [item for item in effects if item.name != "effect_off.mp4"]

            if not effects:
                st.warning("Chưa có hiệu ứng trong thư viện.")
                if st.button("Tạo thư viện hiệu ứng mẫu", type="primary", use_container_width=True):
                    with st.spinner("Đang tạo hiệu ứng mẫu..."):
                        step3_effect_provider.create_builtin_effect_pack()
                    st.rerun()
            else:
                current_name = Path(st.session_state.effect_path).name if st.session_state.get("effect_path") else effects[0].name
                selected_effect = next((item for item in effects if item.name == current_name), effects[0])
                selected_path = str(selected_effect.resolve())
                if st.session_state.get("effect_path") != selected_path:
                    st.session_state.effect_path = selected_path
                    st.session_state.effect_preview_path = None
                    st.session_state.effect_preview_key = None
                metadata = _auto_analyze_selected_effect(selected_effect)

                pixabay_api_key, key_source = _resolve_pixabay_api_key()
                tab_suggest, tab_adjust, tab_library, tab_text = st.tabs(["✨ Đề xuất", "🎛️ Điều chỉnh", "📚 Thư viện", "📝 Chữ động"])
                with tab_suggest:
                    _render_suggest_tab(pixabay_api_key)
                with tab_adjust:
                    _render_adjust_tab(selected_effect, metadata)
                with tab_library:
                    _render_library_tab(effects, selected_effect, pixabay_api_key, key_source, manifest_summary, image_value)
                with tab_text:
                    _render_text_tab()

        if st.button("← Quay lại phụ đề", use_container_width=True):
            _go_to_step(3)

    with preview_col:
        st.markdown("<div class='sticky-effect-preview-anchor'></div>", unsafe_allow_html=True)
        if not enabled:
            st.image(str(image_value), use_container_width=True)
            st.caption("Xem trước không hiệu ứng")
        elif selected_effect and selected_effect.exists():
            import hashlib
            from core.effects.compositor import effect_settings_cache_key
            image_path = Path(image_value)
            effect_settings = _current_effect_settings(metadata)
            st.markdown("#### Live Preview")
            try:
                effect_live_preview(
                    image_path,
                    selected_effect,
                    opacity=effect_settings["opacity"],
                    speed=effect_settings["speed"],
                    blend_mode=effect_settings["blend_mode"] if effect_settings["blend_mode"] != "normal" else ("normal" if effect_settings["effect_type"] in ("alpha", "normal") else "screen"),
                    motion_mode=st.session_state.get("motion_mode", "smooth_zoom"),
                    effect_type=effect_settings["effect_type"],
                    key_color=effect_settings["key_color"],
                    chroma_similarity=effect_settings["chroma_similarity"],
                    chroma_softness=effect_settings["chroma_softness"],
                    despill=effect_settings["despill"],
                    show_matte=bool(st.session_state.get("effect_show_matte", False)),
                    quality=str(st.session_state.get("effect_preview_quality", "fast")),
                    text_profile=st.session_state.get("text_profile"),
                )
            except Exception as exc:
                st.warning(f"Live Preview không khả dụng: {exc}")
                st.image(str(image_path), use_container_width=True)

            st.caption(
                f"{_effect_display_name(selected_effect)} · "
                f"{EFFECT_TYPE_LABELS.get(effect_settings['effect_type'], '?')} · "
                f"opacity {effect_settings['opacity']:.0%} · speed {effect_settings['speed']:.2g}x"
            )
            source_metadata = step3_effect_provider.get_effect_metadata(selected_effect)
            provider_name = str(source_metadata.get("provider") or "built-in").title()
            license_name = str(source_metadata.get("license_name") or "Chưa có metadata giấy phép")
            st.caption(f"Nguồn: {provider_name} · {selected_effect.stem} · {license_name}")
            if source_metadata.get("source_page_url"):
                st.link_button("Xem trang nguồn hiệu ứng", source_metadata["source_page_url"], use_container_width=True)
            st.divider()
            st.markdown("#### Kiểm tra chất lượng FFmpeg")
            st.caption("Cùng filter với render cuối. Chỉ render khi bấm nút.")
            from core.text.provider import text_profile_cache_key
            from core.text.ass_renderer import get_subtitle_manifest_path
            sub_mtime = 0.0
            try:
                sub_path = get_subtitle_manifest_path(st.session_state.get("project_id", "lofi_default_prj"))
                if sub_path.is_file():
                    sub_mtime = sub_path.stat().st_mtime
            except Exception:
                pass

            preview_key = (
                f"motion-v10-compositor|{st.session_state.get('motion_mode', 'smooth_zoom')}|"
                f"{image_path.resolve()}|{selected_effect.resolve()}|"
                f"{effect_settings_cache_key(effect_settings)}|"
                f"{text_profile_cache_key(st.session_state.get('text_profile'))}|"
                f"{sub_mtime}"
            )
            cached = st.session_state.get("effect_preview_path")
            ready = bool(
                st.session_state.get("effect_preview_key") == preview_key
                and cached and Path(cached).exists()
            )
            if st.button("Tạo preview FFmpeg 10 giây", use_container_width=True):
                preview_path = config.TEMP_IMAGE_DIR / f"effect_preview_{hashlib.sha256(preview_key.encode()).hexdigest()[:12]}.mp4"
                try:
                    with st.spinner("Đang tạo preview chất lượng render..."):
                        result = step4_render.build_effect_preview(
                            image_path, selected_effect, preview_path, duration=10,
                            motion_mode=st.session_state.get("motion_mode", "smooth_zoom"),
                            effect_settings=effect_settings,
                            text_profile=st.session_state.get("text_profile"),
                            project_id=st.session_state.get("project_id"),
                        )
                    result_path = Path(result).resolve()
                    if not result_path.is_file() or result_path.stat().st_size < 1024:
                        raise RuntimeError("File preview không tồn tại hoặc quá nhỏ.")
                    st.session_state.effect_preview_path = str(result_path)
                    st.session_state.effect_preview_key = preview_key
                    cached = st.session_state.effect_preview_path
                    ready = True
                except Exception as exc:
                    st.session_state.effect_preview_path = None
                    st.session_state.effect_preview_key = None
                    st.error(f"Không tạo được preview FFmpeg: {exc}")
            if ready and cached and Path(cached).is_file():
                st.video(str(cached), autoplay=False, loop=True, muted=True)
                st.caption(f"FFmpeg 10 giây · {Path(cached).stat().st_size / 1024 / 1024:.1f} MB")
        else:
            st.markdown(
                "<div style='min-height:320px;border:1px dashed #343a55;border-radius:12px;"
                "display:flex;align-items:center;justify-content:center;color:#7f879f;'>"
                "Chọn hiệu ứng để xem preview</div>",
                unsafe_allow_html=True,
            )

        if st.button("Tiếp tục render →", type="primary", use_container_width=True):
            try:
                save_persisted_app_state()
            except Exception:
                pass
            _go_to_step(5)

def _path_exists(value) -> bool:
    try:
        return bool(value and Path(value).exists())
    except Exception:
        return False


def _format_eta(seconds: float | None) -> str:
    if seconds is None or seconds < 0 or seconds == float("inf"):
        return "Đang ước tính"
    seconds = int(round(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours} giờ {minutes:02d} phút"
    if minutes:
        return f"{minutes} phút {secs:02d} giây"
    return f"{secs} giây"


def _render_preflight(track: dict, image_path, effect_path, output_dir) -> list[str]:
    """Kiểm tra lỗi phổ biến trước khi gọi renderer."""
    import shutil
    issues = []
    audio_path = config.INPUT_AUDIO_DIR / f"{track.get('track_id', '')}.m4a"
    if shutil.which("ffmpeg") is None:
        issues.append("Không tìm thấy FFmpeg trong PATH.")
    if not _path_exists(image_path):
        issues.append("Ảnh nền không tồn tại.")
    if not _path_exists(effect_path):
        issues.append("File hiệu ứng không tồn tại.")
    try:
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        test_path = Path(output_dir) / ".write_test"
        test_path.write_text("ok", encoding="utf-8")
        test_path.unlink(missing_ok=True)
    except Exception as exc:
        issues.append(f"Không ghi được thư mục output: {exc}")
    return issues


def render_final_wizard_step():
    """Trang render một màn hình, giữ điều khiển và tiến độ trong cùng khu vực."""
    import time

    st.markdown("## Render video")
    track = st.session_state.get("selected_track")
    image_path = st.session_state.get("image_path")
    effect_enabled = bool(st.session_state.get("effect_enabled", False))

    if not track or not _path_exists(image_path):
        missing = "nhạc" if not track else "ảnh nền"
        st.error(f"Chưa có {missing}.")
        if st.button("← Quay lại", use_container_width=True):
            _go_to_step(2 if not track else 3)
        st.stop()

    if not effect_enabled:
        st.session_state.effect_path = str(_ensure_effect_off_video().resolve())
    effect_path = st.session_state.get("effect_path")
    audio_file = config.INPUT_AUDIO_DIR / f"{track['track_id']}.m4a"

    st.caption(
        f"Nhạc ✓  ·  Ảnh ✓  ·  Hiệu ứng {'bật' if effect_enabled else 'tắt'}  ·  "
        f"Renderer {getattr(step4_render, 'RENDERER_VERSION', 'legacy')}"
    )

    control_col, preview_col = st.columns([0.82, 1.18], gap="large")

    with control_col:
        settings = st.container(border=True)
        with settings:
            duration_sel = st.selectbox(
                "Thời lượng",
                list(DURATION_OPTIONS.keys()),
                index=0,
                label_visibility="collapsed",
            )
            target_duration = DURATION_OPTIONS[duration_sel]

            encoder_mode = st.segmented_control(
                "Bộ mã hóa",
                options=["Tự động", "GPU NVENC", "CPU ổn định"],
                default="Tự động",
                label_visibility="collapsed",
                help="Tự động dò GPU thật bằng test encode: máy có GPU dùng NVENC, không có dùng CPU libx264.",
            )
            motion_mode = st.session_state.get("motion_mode", "smooth_zoom")
            if encoder_mode == "CPU ổn định":
                selected_encoder = "libx264"
            elif encoder_mode == "GPU NVENC":
                selected_encoder = getattr(config, "NVENC_CODEC", "h264_nvenc")
            else:
                selected_encoder = "auto"

            if selected_encoder == "auto":
                if not st.session_state.get("detected_encoder"):
                    with st.spinner("Đang dò GPU..."):
                        st.session_state.detected_encoder = step4_render.detect_best_encoder()
                detected = st.session_state.detected_encoder
                encoder_label = f"tự động → {'GPU ' + detected if detected != 'libx264' else 'CPU libx264'}"
            else:
                encoder_label = selected_encoder

            st.markdown(f"**{track.get('title', 'Untitled')}**")
            st.caption(f"{track.get('author', 'Unknown')} · {target_duration} giây · {encoder_label}")

            with st.expander("Thư mục lưu và kỹ thuật", expanded=False):
                output_dir = st.text_input("Thư mục lưu", value=st.session_state.output_dir)
                st.session_state.output_dir = output_dir
                st.caption(
                    f"{getattr(config, 'VIDEO_FPS', 24)} FPS · "
                    f"Video {getattr(config, 'VIDEO_BITRATE', '2800k')} · "
                    f"Audio {getattr(config, 'AUDIO_BITRATE', '192k')}"
                )

            # output_dir vẫn có giá trị khi expander đóng.
            output_dir = st.session_state.output_dir
            issues = _render_preflight(track, image_path, effect_path, output_dir)
            if issues:
                st.error(" · ".join(issues))
            else:
                st.caption("Tất cả nguyên liệu đã sẵn sàng.")

            render_button = st.button(
                "Bắt đầu render",
                type="primary",
                use_container_width=True,
                disabled=bool(issues),
            )
            if st.button("← Hiệu ứng", use_container_width=True):
                _go_to_step(5)

        progress_box = st.container(border=True)
        with progress_box:
            stage_text = st.empty()
            stage_text.markdown("**Chưa bắt đầu**")
            timeline = st.progress(0, text="0%")
            metric_cols = st.columns(2)
            elapsed_metric = metric_cols[0].empty()
            eta_metric = metric_cols[1].empty()
            elapsed_metric.metric("Đã chạy", "0 giây")
            eta_metric.metric("Còn lại", "Chưa tính")
            status_note = st.empty()

    with preview_col:
        st.markdown("<div class='sticky-render-preview-anchor'></div>", unsafe_allow_html=True)
        preview_slot = st.empty()
        final_path = st.session_state.get("final_video_path")
        if final_path and Path(final_path).exists():
            preview_slot.video(str(final_path))
            st.success("Render hoàn tất")
            with st.expander("Vị trí file", expanded=False):
                st.code(str(final_path), language=None)
            if st.button("Tiếp tục upload YouTube →", type="primary", use_container_width=True):
                try:
                    save_persisted_app_state()
                except Exception:
                    pass
                _go_to_step(7)
        elif st.session_state.get("effect_preview_path") and Path(st.session_state.effect_preview_path).exists():
            preview_slot.video(str(st.session_state.effect_preview_path), autoplay=True, loop=True, muted=True)
            st.caption("Preview đầu ra")
        else:
            preview_slot.image(str(image_path), use_container_width=True)
            st.caption("Khung hình đầu ra")

    if render_button:
        st.session_state.final_video_path = None
        started_at = time.monotonic()
        last_percent = 0.0
        eta_smoothed = None
        last_eta_update = started_at

        def update_progress(percent, stage="Đang xử lý", renderer_eta=None):
            nonlocal last_percent, eta_smoothed, last_eta_update
            percent = max(last_percent, min(float(percent), 1.0))
            last_percent = percent
            now = time.monotonic()
            elapsed = now - started_at

            # FFmpeg gửi tiến độ media thật mỗi ~0.35 giây. ETA tổng được tính theo
            # tốc độ hoàn thành thực tế, sau đó làm mượt EMA để tránh nhảy số.
            raw_eta = (elapsed / percent) * (1.0 - percent) if percent >= 0.05 else None
            if renderer_eta is not None and 0.05 <= percent <= 0.70:
                # ETA segment thật chỉ là phần dựng hình; cộng phần hậu kỳ ước lượng từ tiến độ tổng.
                raw_eta = max(float(renderer_eta), 0.0) + max(elapsed * 0.18, 2.0)
            if raw_eta is not None and now - last_eta_update >= 0.3:
                eta_smoothed = raw_eta if eta_smoothed is None else 0.22 * raw_eta + 0.78 * eta_smoothed
                last_eta_update = now

            timeline.progress(int(percent * 100), text=f"{int(percent * 100)}%")
            stage_text.markdown(f"**{stage}**")
            elapsed_metric.metric("Đã chạy", _format_eta(elapsed))
            eta_metric.metric("Còn lại", _format_eta(eta_smoothed))

        try:
            stage_text.markdown("**Chuẩn bị nguyên liệu**")
            timeline.progress(0, text="Đang chuẩn bị")
            if not audio_file.exists():
                stage_text.markdown("**Đang tải nhạc**")
                step1_music_hunter.download_track(
                    track,
                    project_id=st.session_state.get("project_id"),
                )
            if not audio_file.exists():
                raise FileNotFoundError(f"Không tạo được file nhạc: {audio_file}")

            config.VIDEO_DURATION_SECONDS = target_duration
            config.OUTPUT_DIR = Path(output_dir)
            config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

            final_video = step4_render.run_step4(
                project_id=st.session_state.get("project_id", "lofi_default_prj"),
                audio_path=audio_file,
                image_path=Path(image_path),
                effect_path=Path(effect_path),
                segment_duration=min(60.0, float(target_duration)),
                encoder=selected_encoder,
                progress_callback=update_progress,
                vibe_mode="clean",
                motion_mode=motion_mode,
                effect_settings=_current_effect_settings() if effect_enabled else None,
                text_profile=st.session_state.get("text_profile"),
            )
            final_video = Path(final_video)
            if not final_video.exists() or final_video.stat().st_size < 1024:
                raise RuntimeError("Renderer kết thúc nhưng video đầu ra không hợp lệ.")

            st.session_state.final_video_path = str(final_video.resolve())
            update_progress(1.0, "Hoàn thành")
            save_persisted_app_state()
            st.rerun()
        except Exception as exc:
            elapsed = time.monotonic() - started_at
            stage_text.markdown("**Render thất bại**")
            elapsed_metric.metric("Đã chạy", _format_eta(elapsed))
            eta_metric.metric("Còn lại", "Đã dừng")
            status_note.error(str(exc))
            with settings.expander("Chi tiết lỗi", expanded=False):
                st.code(
                    f"Audio: {audio_file}\nImage: {image_path}\nEffect: {effect_path}\n"
                    f"Output: {output_dir}\nDuration: {target_duration}s\n"
                    f"Renderer: {getattr(step4_render, '__file__', 'unknown')}\nError: {exc}",
                    language="text",
                )

def _video_duration_seconds(video_path) -> float:
    """Đọc thời lượng video cuối; lỗi thì dùng cấu hình mặc định."""
    try:
        from core.media.probe import MediaProbe
        return float(MediaProbe.probe_media(Path(video_path))["duration_seconds"])
    except Exception:
        return float(getattr(config, "VIDEO_DURATION_SECONDS", 3600))


def render_upload_wizard_step():
    """Bước 6: AI viết caption/hashtag, chỉnh tay rồi upload YouTube có tiến độ."""
    import step5_uploader
    from core.text.caption_writer import generate_caption

    st.markdown("## Upload YouTube")
    final_path = st.session_state.get("final_video_path")
    if not final_path or not Path(final_path).exists():
        st.error("Chưa có video render hoàn tất. Hãy render video ở bước trước.")
        if st.button("← Quay lại render", use_container_width=True):
            _go_to_step(6)
        st.stop()

    track = st.session_state.get("selected_track") or {}
    track_id = str(track.get("track_id") or Path(final_path).stem)

    caption_col, upload_col = st.columns([1.05, 0.95], gap="large")

    with caption_col:
        st.markdown("#### Caption và hashtag")
        language_label = st.radio(
            "Ngôn ngữ",
            ["Tiếng Việt", "English"],
            index=0 if st.session_state.get("upload_language", "vi") == "vi" else 1,
            horizontal=True,
        )
        st.session_state.upload_language = "vi" if language_label == "Tiếng Việt" else "en"

        if st.button("Tạo caption + hashtag bằng AI", type="primary", use_container_width=True):
            try:
                music_tags = track.get("style_tags") or []
                if isinstance(music_tags, str):
                    music_tags = [item.strip() for item in music_tags.split(",") if item.strip()]
                credit_text = ""
                try:
                    credit_text = step5_uploader.store.build_credit_text(track_id)
                except Exception:
                    pass
                with st.spinner("AI đang viết caption..."):
                    caption = generate_caption(
                        track,
                        list(music_tags),
                        duration_seconds=_video_duration_seconds(final_path),
                        language=st.session_state.upload_language,
                        credit_text=credit_text,
                        api_url=str(st.session_state.get("prompt_api_url") or getattr(config, "PROMPT_API_URL", "")),
                        api_key=str(st.session_state.get("prompt_api_key") or getattr(config, "PROMPT_API_KEY", "")),
                        model=str(st.session_state.get("prompt_api_model") or getattr(config, "PROMPT_API_MODEL", "openai")),
                        timeout=int(getattr(config, "PROMPT_API_TIMEOUT", 40)),
                        channel_profile=str(getattr(config, "CAPTION_CHANNEL_PROFILE", "")),
                    )
                st.session_state.upload_title = caption["title"]
                st.session_state.upload_description = caption["description"]
                st.session_state.upload_hashtags = " ".join(caption["hashtags"])
                st.session_state.upload_tags_text = ", ".join(caption["tags"])
                if caption.get("error"):
                    st.warning(f"AI online lỗi, đang dùng caption dự phòng: {caption['error']}")
                elif caption.get("source") == "fallback":
                    st.info("AI đang tắt hoặc chưa cấu hình; dùng caption mẫu tự sinh.")
                else:
                    st.success("Đã tạo caption bằng AI. Kiểm tra và chỉnh lại trước khi upload.")
                save_persisted_app_state()
                st.rerun()
            except Exception as exc:
                st.error(f"Không tạo được caption: {exc}")

        title_value = st.text_input(
            f"Tiêu đề ({len(st.session_state.get('upload_title') or '')}/100 ký tự)",
            value=st.session_state.get("upload_title") or "",
            max_chars=100,
        )
        st.session_state.upload_title = title_value
        description_value = st.text_area(
            "Mô tả video",
            value=st.session_state.get("upload_description") or "",
            height=220,
            help="Credit nhạc và hashtag đã được chèn tự động khi tạo bằng AI.",
        )
        st.session_state.upload_description = description_value
        hashtags_value = st.text_input(
            "Hashtag (cách nhau bằng dấu cách)",
            value=st.session_state.get("upload_hashtags") or "",
            help="YouTube chỉ đọc tối đa 15 hashtag đầu tiên.",
        )
        st.session_state.upload_hashtags = hashtags_value
        tags_value = st.text_area(
            "Tags tìm kiếm (cách nhau bằng dấu phẩy)",
            value=st.session_state.get("upload_tags_text") or "",
            height=80,
            help="Tối đa ~500 ký tự tổng theo giới hạn YouTube.",
        )
        st.session_state.upload_tags_text = tags_value

    with upload_col:
        st.markdown("#### Video và đăng tải")
        st.video(str(final_path))
        size_mb = Path(final_path).stat().st_size / 1024 / 1024
        st.caption(f"{Path(final_path).name} · {size_mb:.0f} MB")

        prerequisites = step5_uploader.get_upload_prerequisites()
        missing = []
        if not prerequisites["google_libs"]:
            missing.append(f"Thiếu thư viện Google API. Chạy: `{prerequisites['install_hint']}`")
        if not prerequisites["client_secret"]:
            missing.append(
                "Thiếu `client_secret.json` tại `" + prerequisites["client_secret_path"] + "`. "
                "Tạo OAuth client ID (Desktop) trong Google Cloud Console, bật YouTube Data API v3 rồi tải file JSON về đường dẫn trên."
            )
        for issue in missing:
            st.warning(issue)
        if prerequisites["token"]:
            st.caption("YouTube OAuth: **Đã có token đăng nhập**")
        elif not missing:
            st.caption("Lần upload đầu sẽ mở trình duyệt để đăng nhập YouTube.")

        privacy_options = {
            "Riêng tư + tự đặt lịch": ("private", "auto"),
            "Riêng tư + chọn giờ đăng": ("private", "manual"),
            "Riêng tư (không đặt lịch)": ("private", "none"),
            "Không công khai (unlisted)": ("unlisted", "none"),
            "Công khai ngay": ("public", "none"),
        }
        privacy_labels = list(privacy_options.keys())
        current_privacy = st.session_state.get("upload_privacy_choice", privacy_labels[0])
        if current_privacy not in privacy_options:
            current_privacy = privacy_labels[0]
        privacy_choice = st.selectbox("Chế độ đăng", privacy_labels, index=privacy_labels.index(current_privacy))
        st.session_state.upload_privacy_choice = privacy_choice
        privacy_value, schedule_mode = privacy_options[privacy_choice]

        publish_at = None
        if schedule_mode == "manual":
            from datetime import datetime, timedelta, time as dt_time
            default_time = datetime.now() + timedelta(hours=3)
            schedule_date = st.date_input("Ngày đăng", value=default_time.date())
            schedule_time = st.time_input("Giờ đăng", value=dt_time(default_time.hour, 0))
            publish_at = datetime.combine(schedule_date, schedule_time)
            if publish_at <= datetime.now():
                st.warning("Giờ đăng phải ở tương lai.")
        elif schedule_mode == "auto":
            st.caption(f"Tự chọn khung giờ traffic cao: {', '.join(str(h) + 'h' for h in config.SCHEDULE_HOURS)}.")

        upload_disabled = bool(missing) or not (st.session_state.get("upload_title") or "").strip()
        if not (st.session_state.get("upload_title") or "").strip():
            st.caption("Nhập tiêu đề hoặc bấm tạo caption bằng AI trước khi upload.")

        if st.session_state.get("upload_result_video_id"):
            video_id = st.session_state.upload_result_video_id
            st.success("Đã upload thành công!")
            st.link_button("Mở video trên YouTube Studio", f"https://studio.youtube.com/video/{video_id}/edit", use_container_width=True)
            st.code(f"https://youtu.be/{video_id}", language=None)

        if st.button("Upload lên YouTube", type="primary", use_container_width=True, disabled=upload_disabled):
            hashtags = [item for item in (st.session_state.get("upload_hashtags") or "").split() if item.strip()]
            description = (st.session_state.get("upload_description") or "").strip()
            hashtag_line = " ".join(hashtags)
            if hashtag_line and hashtag_line not in description:
                description = f"{description}\n\n{hashtag_line}"
            tags = [item.strip() for item in (st.session_state.get("upload_tags_text") or "").split(",") if item.strip()]
            progress_bar = st.progress(0, text="Chuẩn bị upload...")
            def _on_progress(percent: float):
                progress_bar.progress(min(int(percent * 100), 100), text=f"Đang upload {int(percent * 100)}%")
            try:
                if schedule_mode == "manual" and publish_at is not None:
                    from datetime import datetime as _dt
                    if publish_at <= _dt.now():
                        raise ValueError("Giờ đăng đã ở quá khứ, hãy chọn lại.")
                video_id = step5_uploader.upload_video(
                    video_path=Path(final_path),
                    track_id=track_id,
                    video_index=1,
                    title=(st.session_state.get("upload_title") or "").strip(),
                    description=description,
                    tags=tags,
                    privacy=privacy_value if not (privacy_value == "private" and schedule_mode == "auto") else None,
                    publish_at=publish_at,
                    progress_callback=_on_progress,
                )
                st.session_state.upload_result_video_id = video_id
                save_persisted_app_state()
                st.rerun()
            except Exception as exc:
                progress_bar.empty()
                st.error(f"Upload thất bại: {exc}")

        if st.button("← Quay lại render", use_container_width=True):
            _go_to_step(6)


def render_sd_setup_step():
    """Bước 1: kiểm tra hệ thống và hướng dẫn cài/bật Stable Diffusion Local."""
    st.markdown("## Kiểm tra hệ thống")

    left, right = st.columns([1, 1])

    with left:
        st.markdown("### Cấu hình máy")
        import shutil
        ffmpeg_ok = shutil.which("ffmpeg") is not None
        st.write(f"{'✅' if ffmpeg_ok else '❌'} FFmpeg: {'Sẵn sàng' if ffmpeg_ok else 'Chưa thấy trong PATH'}")

        if st.button("🔍 Kiểm tra cấu hình máy", use_container_width=True):
            with st.spinner("Đang kiểm tra CPU/RAM/GPU..."):
                try:
                    import system_check
                    st.session_state.system_check_result = system_check.run_check(verbose=False)
                except Exception as e:
                    st.session_state.system_check_error = str(e)

        if st.session_state.get("system_check_result"):
            result = st.session_state.system_check_result
            cpu = result.get("cpu", {})
            ram = result.get("ram", {})
            gpu = result.get("gpu")
            rec = result.get("recommendation", {})

            st.success("Đã kiểm tra cấu hình máy.")
            st.write(f"CPU: {cpu.get('cpu_cores', '?')} nhân / {cpu.get('cpu_threads', '?')} luồng")
            st.write(f"RAM: {ram.get('ram_total_gb', '?')}GB tổng, còn {ram.get('ram_available_gb', '?')}GB")
            if gpu:
                st.write(f"GPU: {gpu.get('name')} - VRAM {gpu.get('vram_total_mb')}MB")
            else:
                st.warning("Không phát hiện GPU NVIDIA.")

            if rec.get("can_run_sd_local"):
                st.info(f"Đề xuất SD Local: {rec.get('checkpoint')} | {rec.get('resolution')} | {' '.join(rec.get('flags', [])) or 'không cần flag'}")
            else:
                st.warning(rec.get("reason", "Không có đề xuất."))

        if st.session_state.get("system_check_error"):
            st.warning(f"Không chạy được system_check.py: {st.session_state.system_check_error}")

        st.markdown("### Kết nối SD Local")
        sd_url = st.text_input("SD Local API URL:", value=st.session_state.sd_api_url)
        st.session_state.sd_api_url = sd_url
        config.SD_LOCAL_API_URL = sd_url

        if st.button("🔌 Kiểm tra SD Local đang bật chưa", use_container_width=True):
            with st.spinner("Đang kiểm tra API SD Local..."):
                try:
                    import requests
                    response = requests.get(f"{sd_url}/sdapi/v1/options", timeout=5)
                    if response.status_code == 200:
                        data = response.json()
                        model_name = data.get("sd_model_checkpoint", "Không rõ")
                        st.session_state.sd_local_status = f"✅ SD Local đang chạy. Model: {model_name}"
                    else:
                        st.session_state.sd_local_status = f"❌ SD Local trả lỗi HTTP {response.status_code}"
                except Exception as e:
                    st.session_state.sd_local_status = f"❌ Chưa kết nối được SD Local: {e}"

        if st.session_state.get("sd_local_status"):
            if st.session_state.sd_local_status.startswith("✅"):
                st.success(st.session_state.sd_local_status)
            else:
                st.error(st.session_state.sd_local_status)

    with right:
        st.markdown("### Quản lý SD Local")

        from core.image.sd_manager import SDInstaller, SDProcessManager, SDHealthChecker

        # Chọn chế độ
        sd_mode_wiz = st.radio(
            "Chế độ SD Local:",
            ["📁 Dùng bản đã cài sẵn", "🚀 Để App tự cài đặt"],
            index=0 if st.session_state.get("sd_mode", "existing") == "existing" else 1,
            horizontal=True,
            key="sd_mode_wizard_radio"
        )
        st.session_state.sd_mode = "existing" if "Dùng" in sd_mode_wiz else "app_managed"

        st.markdown("---")

        if st.session_state.sd_mode == "existing":
            # --- Chế độ 1: Trỏ đến bản đã cài ---
            existing_path = st.text_input(
                "Đường dẫn thư mục AUTOMATIC1111:",
                value=st.session_state.get("sd_install_dir", ""),
                placeholder="Ví dụ: D:/stable-diffusion-webui",
                key="sd_exist_path_wiz",
                help="Thư mục chứa webui-user.bat hoặc launch.py"
            )

            webui_found = False
            if existing_path:
                p = Path(existing_path)
                if p.exists():
                    webui_found = (p / "launch.py").exists() or (p / "webui-user.bat").exists() or (p / "webui.py").exists()
                    if webui_found:
                        st.success(f"✅ Phát hiện AUTOMATIC1111 tại `{existing_path}`")
                    else:
                        st.warning("⚠️ Thư mục tồn tại nhưng không tìm thấy cấu trúc AUTOMATIC1111.")
                else:
                    st.error(f"❌ Đường dẫn không tồn tại: `{existing_path}`")

            col_start, col_stop = st.columns(2)
            with col_start:
                if st.button("🟢 Bật Stable Diffusion", use_container_width=True, disabled=not webui_found, key="sd_wiz_start_exist"):
                    with st.spinner("Đang mở console Stable Diffusion..."):
                        try:
                            SDProcessManager.start_existing_process(Path(existing_path))
                            st.success("Đang khởi động Stable Diffusion! Cửa sổ console mới đã được mở. Hãy đợi 1-2 phút rồi bấm Kiểm tra kết nối.")
                        except Exception as start_err:
                            st.error(f"Lỗi khởi động: {start_err}")
            with col_stop:
                if st.button("🔴 Tắt Stable Diffusion", use_container_width=True, key="sd_wiz_stop_exist"):
                    with st.spinner("Đang dừng tiến trình Stable Diffusion..."):
                        try:
                            killed = SDProcessManager.kill_process_by_port(7860)
                            if killed:
                                st.success("Đã đóng tiến trình Stable Diffusion đang chiếm cổng 7860!")
                            else:
                                st.warning("Không tìm thấy tiến trình nào đang chạy trên cổng 7860.")
                        except Exception as stop_err:
                            st.error(f"Lỗi khi đóng: {stop_err}")

            with st.expander("ℹ️ Cách bật API flag trong webui-user.bat"):
                st.code("set COMMANDLINE_ARGS=--api --medvram", language="bat")
                st.caption("Mở file webui-user.bat → tìm dòng COMMANDLINE_ARGS → thêm --api → lưu → chạy lại.")

        else:
            # --- Chế độ 2: App tự cài ---
            install_path_wiz = st.text_input(
                "Thư mục đích cài đặt SD:",
                value=st.session_state.get("sd_install_dir", "D:/AI/LofiStudioAI"),
                key="sd_install_path_wiz",
                help="Cần ≥10GB trống. App tự tạo thư mục nếu chưa có."
            )

            state_file_wiz = Path(install_path_wiz) / "install_state.json"
            is_installed_wiz = False
            if state_file_wiz.exists():
                try:
                    with open(state_file_wiz, "r", encoding="utf-8") as sf:
                        _s = json.load(sf)
                        is_installed_wiz = _s.get("installed", False)
                except Exception:
                    pass

            if is_installed_wiz:
                st.success(f"✅ Đã cài đặt SD tại `{install_path_wiz}`")
                
                col_start_m, col_stop_m = st.columns(2)
                with col_start_m:
                    if st.button("🟢 Bật SD (App-managed)", use_container_width=True, key="sd_wiz_start_managed"):
                        with st.spinner("Đang khởi động Stable Diffusion..."):
                            try:
                                SDProcessManager.start_process(Path(install_path_wiz))
                                st.success("Đang khởi động Stable Diffusion chạy ngầm! Đợi 1-2 phút rồi kiểm tra lại kết nối.")
                            except Exception as start_err:
                                st.error(f"Khởi động lỗi: {start_err}")
                with col_stop_m:
                    if st.button("🔴 Tắt SD (App-managed)", use_container_width=True, key="sd_wiz_stop_managed"):
                        with st.spinner("Đang tắt Stable Diffusion..."):
                            try:
                                SDProcessManager.stop_process(Path(install_path_wiz))
                                SDProcessManager.kill_process_by_port(7860)
                                st.success("Đã tắt tiến trình Stable Diffusion!")
                            except Exception as stop_err:
                                st.error(f"Tắt lỗi: {stop_err}")
            else:
                st.info("ℹ️ Chưa cài. Bấm nút bên dưới để bắt đầu.")

            if not is_installed_wiz:
                if st.button("🚀 Bắt đầu cài đặt tự động", use_container_width=True, key="sd_wiz_install"):
                    pb = st.progress(0.0)
                    st_msg = st.empty()
                    def _prog(pct, msg):
                        pb.progress(float(pct))
                        st_msg.markdown(f"**{msg}** `{int(float(pct)*100)}%`")
                    try:
                        ok = SDInstaller.install(Path(install_path_wiz), progress_callback=_prog)
                        if ok:
                            st.session_state.sd_install_dir = install_path_wiz
                            st.success("🎉 Cài đặt thành công!")
                            st.rerun()
                    except Exception as e:
                        st.error(f"❌ Cài đặt thất bại: {e}")

    st.markdown("---")
    col_back, col_next = st.columns([1, 1])
    with col_back:
        if st.button("🔄 Làm mới bước này", use_container_width=True):
            st.rerun()
    with col_next:
        if st.button("➡️ Tiếp tục chọn nhạc", use_container_width=True, type="primary"):
            _go_to_step(2)


render_local_sidebar()
try:
    save_persisted_app_state()
except Exception as state_error:
    st.sidebar.caption(f"Chưa lưu được tiến trình: {state_error}")


# --- PAGE ROUTING ---
# Tên ứng dụng và tiến trình đã có ở sidebar, không lặp lại trong nội dung.
if st.session_state.get("current_step") == 1:
    render_sd_setup_step()
    st.stop()


if st.session_state.get("current_step") == 2:
    render_music_wizard_step()
    st.stop()


if st.session_state.get("current_step") == 3:
    render_image_wizard_step()
    st.stop()

if st.session_state.get("current_step") == 4:
    render_effect_wizard_step()
    st.stop()

if st.session_state.get("current_step") == 5:
    render_subtitle_wizard_step_simple()
    st.stop()

if st.session_state.get("current_step") == 6:
    render_final_wizard_step()
    st.stop()

if st.session_state.get("current_step") == 7:
    render_upload_wizard_step()
    st.stop()
