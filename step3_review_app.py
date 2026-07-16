"""
AI FILE NOTE - STEP 3: STREAMLIT REVIEW DASHBOARD

Chức năng chính:
- Giao diện wizard 5 bước: kiểm tra hệ thống, chọn nhạc, tạo ảnh, chọn hiệu ứng, render.
- Điều phối step1_music_hunter, step2_image_provider và step4_render.
- Quản lý Streamlit session_state, lưu/khôi phục tiến trình tại data/review_app_state.json.
- Hiển thị preview nhạc, ảnh, hiệu ứng, trend và tiến độ render/ETA.
- Quản lý kết nối/cài đặt Stable Diffusion Local từ giao diện.

Cách chạy chuẩn:
- py -3.10 -m streamlit run step3_review_app.py

Đầu vào chính:
- Thao tác người dùng, API key tùy chọn, project_id và các asset đã chọn.

Đầu ra chính:
- Trạng thái dự án, asset được duyệt và video cuối do step4_render tạo.

Luồng phụ thuộc:
- Bước 2 gọi step1_music_hunter để tìm/tải/phân tích nhạc.
- Bước 3 gọi step2_image_provider hoặc nhận ảnh Dreamina upload.
- Bước 4 tạo/chọn overlay và gọi step4_render.build_effect_preview().
- Bước 5 gọi step4_render.run_step4() để render video cuối.

Lưu ý khi sửa:
- Đây là file điều phối/UI, không chuyển thuật toán xử lý nặng vào đây nếu đã có module core/step riêng.
- Giữ tên các khóa PERSISTED_STATE_KEYS và session_state đang được dùng giữa các bước.
- Sau thao tác làm thay đổi bước hoặc asset, lưu state trước khi st.rerun() khi cần.
- Không đổi chữ ký hàm public của step1/step2/step4 nếu chưa sửa đồng bộ nơi gọi.
"""
import json
import random
from pathlib import Path
import streamlit as st

import config
import step1_music_hunter
import step2_image_provider
import step4_render
import importlib

# Streamlit có thể giữ module cũ trong bộ nhớ sau khi chép đè file.
# Reload bắt buộc để giao diện luôn chạy đúng renderer đang nằm cạnh file app.
step4_render = importlib.reload(step4_render)
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
PERSISTED_STATE_KEYS = (
    "project_id", "query", "candidates", "selected_track", "image_prompt",
    "image_path", "image_source", "effect_path", "effect_preview_path",
    "effect_preview_key", "final_video_path", "output_dir", "image_provider",
    "sd_api_url", "sd_checkpoint", "sd_install_dir", "sd_mode",
    "current_step", "vibe_mode", "effect_enabled", "motion_mode", "previews_dict",
    "scene_layers_manifest", "scene_layers_image", "scene_analysis_error",
    "dreamina_zoom_percent", "dreamina_prompt", "dreamina_prompt_vi", "dreamina_prompt_track_id", "dreamina_processed_key",
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
    defaults = {
        "query": "lofi chill copyright free",
        "candidates": [],
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
        "current_step": 1,
        "vibe_mode": "clean",
        "effect_enabled": False,
        "motion_mode": "smooth_zoom",
        "scene_layers_manifest": None,
        "scene_layers_image": None,
        "scene_analysis_error": None,
        "previews_dict": None
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


def build_cross_platform_trend_compat(youtube: dict | None, tiktok: dict | None) -> dict:
    builder = getattr(step1_music_hunter, "build_cross_platform_trend", None)
    if callable(builder):
        return builder(youtube, tiktok)
    sources = []
    if youtube and youtube.get("trend_score") is not None:
        sources.append(("YouTube", float(youtube["trend_score"]), 0.55))
    if tiktok and tiktok.get("trend_score") is not None:
        sources.append(("TikTok", float(tiktok["trend_score"]), 0.45))
    if not sources:
        return {"score": None, "label": "Chưa đủ dữ liệu", "confidence": "Thấp", "sources": []}
    total_weight = sum(weight for _, _, weight in sources)
    score = round(sum(value * weight for _, value, weight in sources) / total_weight)
    label = "Bắt trend mạnh" if score >= 80 else "Có tiềm năng" if score >= 60 else "Theo dõi thêm" if score >= 40 else "Tín hiệu yếu"
    return {"score": score, "label": label, "confidence": "Cao" if len(sources) >= 2 else "Trung bình", "sources": [name for name, _, _ in sources]}


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

if st.session_state.get("current_step", 1) > 5:
    st.session_state.current_step = 5

if "current_step" not in st.session_state:
    st.session_state.current_step = 1


WIZARD_STEPS = {
    1: "⚙️ Kiểm tra hệ thống",
    2: "🎵 Chọn nhạc",
    3: "🎨 Tạo ảnh nền",
    4: "✨ Chọn hiệu ứng",
    5: "🚀 Render video",
}


def render_wizard_header():
    current = st.session_state.get("current_step", 1)
    labels = []
    for step, title in WIZARD_STEPS.items():
        prefix = "🟣" if step == current else "⚪"
        labels.append(f"{prefix} {step}. {title}")
    st.caption("  →  ".join(labels))


def load_project_to_session_state(project_id: str):
    """Đồng bộ cấu hình từ database SQLite vào session state."""
    from core.project_manager import ProjectManager
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

def render_local_sidebar():
    """Thanh tiến trình tối giản, khóa các bước chưa đủ điều kiện."""
    def path_ok(value) -> bool:
        try:
            return bool(value and Path(value).exists())
        except Exception:
            return False

    has_track = bool(st.session_state.get("selected_track"))
    has_image = path_ok(st.session_state.get("image_path"))
    current = int(st.session_state.get("current_step", 1))
    unlocked_step = 3 if has_track else 2
    if has_image:
        unlocked_step = 5

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
                clear_persisted_app_state()
                st.session_state.clear()
                st.rerun()

def build_image_prompt_from_track(track: dict, variant: int = 0) -> str:
    """
    Tạo prompt ảnh local (heuristic) dựa vào tên bài nhạc đang chọn.
    `variant` > 0: xoay vòng sang bối cảnh khác cùng mood (dùng khi bấm lại nút).
    """
    title = (track or {}).get("title") or "lofi chill music"
    author = (track or {}).get("author") or "unknown artist"
    text = f"{title} {author}".lower()

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

    all_scenes = [s for _, s in keyword_scenes]
    matched_index = None
    for i, (keywords, mapped_scene) in enumerate(keyword_scenes):
        if any(word in text for word in keywords):
            scene = mapped_scene
            matched_index = i
            break

    # Bấm lại nút -> xoay vòng sang bối cảnh khác (bắt đầu từ cảnh khớp keyword)
    if variant > 0:
        start = matched_index if matched_index is not None else 0
        scene = all_scenes[(start + variant) % len(all_scenes)]

    if any(word in text for word in ("sad", "alone", "lonely", "blue")):
        mood = "quiet reflective mood, gentle melancholic atmosphere"
        colors = "deep blue, soft purple, warm amber accents"
    elif any(word in text for word in ("happy", "cute", "sweet")):
        mood = "gentle happy mood, cozy and cute atmosphere"
        colors = "pastel pink, cream, soft yellow palette"
    elif any(word in text for word in ("dark", "deep", "late")):
        mood = "late night calm mood, cinematic low light atmosphere"
        colors = "dark navy, violet, warm lamp glow"

    return (
        f"{scene}, inspired by the music title '{title}', {mood}, {colors}, "
        "lofi anime background, cinematic wide shot, 16:9 widescreen composition, "
        "detailed environment, soft lighting, no text, no logo, no watermark"
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

    try:
        from utils.helpers import generate_prompt_from_track
        prompt = generate_prompt_from_track(track, avoid=history)
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
        use_ai = bool(st.session_state.get("prompt_api_key"))
        if force and use_ai:
            prompt_en = generate_track_prompt(track)
        else:
            prompt_en = build_image_prompt_from_track(track, variant=variant)
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
    st.session_state.current_step = 3
    st.rerun()

def render_music_wizard_step():
    """Một luồng tìm nhạc duy nhất: từ khóa, chủ đề hoặc URL."""
    st.markdown("<div class='step-eyebrow'>BƯỚC 02</div>", unsafe_allow_html=True)
    st.markdown("## Chọn nhạc")

    with st.form("music_search_form"):
        col_input, col_button = st.columns([5, 1])
        with col_input:
            music_input = st.text_input(
                "Tìm nhạc",
                value=st.session_state.get("query", ""),
                placeholder="Nhập từ khóa hoặc dán URL...",
                label_visibility="collapsed",
            )
        with col_button:
            submitted = st.form_submit_button("Tìm", type="primary", use_container_width=True)

    st.caption("Gợi ý nhanh")
    genre_items = list(HOT_GENRES.items())
    genre_cols = st.columns(len(genre_items))
    selected_genre_query = None
    for idx, (genre_name, genre_query) in enumerate(genre_items):
        short_name = genre_name.split(" ", 1)[-1].replace(" Lofi", "").replace(" Beats", "")
        with genre_cols[idx]:
            if st.button(short_name, key=f"quick_genre_{idx}", use_container_width=True):
                selected_genre_query = genre_query

    search_value = selected_genre_query or (music_input.strip() if submitted else None)
    if search_value:
        is_url = search_value.lower().startswith(("http://", "https://"))
        if is_url:
            with st.spinner("Đang đọc URL và chuẩn bị bài nhạc..."):
                try:
                    track_info = step1_music_hunter.fetch_track_metadata_by_url(search_value)
                    step1_music_hunter.download_track(track_info, project_id=st.session_state.get("project_id"))
                    st.session_state.selected_track = track_info
                    prepare_dreamina_prompt(track_info)
                    st.session_state.current_step = 3
                    st.session_state.query = search_value
                    if not any(t.get("track_id") == track_info.get("track_id") for t in st.session_state.candidates):
                        st.session_state.candidates.insert(0, track_info)
                    st.rerun()
                except Exception as e:
                    st.error(f"Không xử lý được URL: {e}")
        else:
            st.session_state.query = search_value
            with st.spinner("Đang tìm nhạc..."):
                try:
                    st.session_state.candidates = step1_music_hunter.fetch_candidate_tracks(query=search_value, limit=8)
                except Exception as e:
                    st.session_state.candidates = []
                    st.error(f"Không tìm được nhạc: {e}")
            st.rerun()

    with st.expander("Nguồn dữ liệu xu hướng", expanded=False):
        st.caption("TikTok Research API cần tài khoản đã được TikTok phê duyệt. Thông tin đăng nhập chỉ giữ trong phiên hiện tại.")
        cfg_a, cfg_b = st.columns(2)
        with cfg_a:
            st.session_state.tiktok_client_key = st.text_input(
                "TikTok Client Key", value=st.session_state.get("tiktok_client_key", ""), type="password"
            )
        with cfg_b:
            st.session_state.tiktok_client_secret = st.text_input(
                "TikTok Client Secret", value=st.session_state.get("tiktok_client_secret", ""), type="password"
            )
        st.info("Facebook / Instagram chưa thể kết nối trực tiếp trong app local. Meta Content Library API yêu cầu quyền nghiên cứu và môi trường bảo mật của Meta hoặc SOMAR.")

    selected = st.session_state.get("selected_track")
    if selected:
        st.markdown(
            f"<div class='selected-track'><span>ĐÃ CHỌN</span><b>{selected.get('title', 'Untitled')}</b>"
            f"<small>{selected.get('author', 'Unknown')}</small></div>",
            unsafe_allow_html=True,
        )

    candidates = st.session_state.get("candidates", [])
    result_title, result_count = st.columns([4, 1])
    result_title.markdown("### Kết quả")
    result_count.caption(f"{len(candidates)} bài" if candidates else "")

    if not candidates:
        st.info("Nhập từ khóa, dán URL hoặc chọn một gợi ý để bắt đầu.")
    else:
        for idx, track in enumerate(candidates):
            is_selected = bool(selected and selected.get("track_id") == track.get("track_id"))
            st.markdown(
                f"<div class='track-row{' track-row-selected' if is_selected else ''}'>"
                f"<div><b>{track.get('title', 'Untitled')}</b>"
                f"<span>{track.get('author', 'Unknown')} · {track.get('source', 'Unknown')}</span></div>"
                f"<em>{'Đã chọn' if is_selected else ''}</em></div>",
                unsafe_allow_html=True,
            )
            source_text = str(track.get("source", "")).lower()
            url_text = str(track.get("url", "")).lower()
            is_youtube = "youtube" in source_text or "youtu.be" in url_text or "youtube.com" in url_text
            analysis_key = f"trend_analysis_{track.get('track_id')}"

            col_preview, col_analyze, col_pick = st.columns([3, 1, 1])
            with col_preview:
                render_track_preview(track, key_prefix=f"music_preview_{idx}")
            with col_analyze:
                if st.button(
                    "Phân tích",
                    key=f"music_analyze_{idx}",
                    use_container_width=True,
                    disabled=not is_youtube,
                    help="Lưu số liệu YouTube hiện tại để so sánh tăng trưởng ở các lần kiểm tra sau.",
                ):
                    with st.spinner("Đang cập nhật số liệu YouTube..."):
                        try:
                            analysis = step1_music_hunter.capture_youtube_trend_snapshot(track)
                            st.session_state[analysis_key] = analysis
                            refreshed_track = analysis.get("track") or track
                            st.session_state.candidates[idx] = refreshed_track
                            if is_selected:
                                st.session_state.selected_track = refreshed_track
                            st.rerun()
                        except Exception as e:
                            st.error(f"Không phân tích được bài này: {e}")
            with col_pick:
                if st.button(
                    "✓ Đã chọn" if is_selected else "Chọn",
                    key=f"music_select_{idx}",
                    use_container_width=True,
                    disabled=is_selected,
                ):
                    _select_track(track)

            analysis = st.session_state.get(analysis_key)
            if analysis:
                current_track = analysis.get("track") or track
                duration = int(current_track.get("duration") or 0)
                duration_text = f"{duration // 60}:{duration % 60:02d}" if duration else "Không rõ"
                upload_date = str(current_track.get("upload_date") or "")
                if len(upload_date) == 8:
                    upload_date = f"{upload_date[6:8]}/{upload_date[4:6]}/{upload_date[:4]}"
                with st.expander("Phân tích xu hướng YouTube", expanded=True):
                    metric_cols = st.columns(4)
                    metric_cols[0].metric("Lượt xem", format_num(current_track.get("views", 0)))
                    metric_cols[1].metric("Lượt thích", format_num(current_track.get("likes", 0)))
                    metric_cols[2].metric("Bình luận", format_num(current_track.get("comments", 0)))
                    metric_cols[3].metric("Điểm trend", analysis.get("trend_score") if analysis.get("trend_score") is not None else "Chờ")

                    growth = analysis.get("growth_percent")
                    if growth is None:
                        st.info("Đã lưu mốc đầu tiên. Hãy phân tích lại sau để hệ thống tính tốc độ tăng thực tế.")
                    else:
                        st.success(
                            f"{analysis.get('trend_label')} · +{growth:.2f}% lượt xem · "
                            f"+{format_num(analysis.get('views_delta', 0))} lượt xem trong {analysis.get('elapsed_hours', 0)} giờ"
                        )
                    st.caption(
                        f"Độ tin cậy: {analysis.get('confidence', 'Thấp')} · "
                        f"{analysis.get('snapshot_count', 0)} lần đo · "
                        f"Thời lượng: {duration_text} · Ngày đăng: {upload_date or 'Không rõ'} · "
                        f"Cập nhật UTC: {analysis.get('captured_at_utc', 'Không rõ')}"
                    )
                    st.caption("Điểm YouTube chỉ dựa trên các snapshot YouTube đã thu thập.")

            with st.expander("TikTok trend", expanded=False):
                music_id = st.text_input(
                    "TikTok music_id",
                    value=st.session_state.get(f"tiktok_music_id_{track.get('track_id')}", ""),
                    key=f"tiktok_music_id_input_{idx}",
                    placeholder="Ví dụ: 713...",
                    help="Mở trang âm thanh TikTok và lấy ID số của sound/music.",
                )
                st.session_state[f"tiktok_music_id_{track.get('track_id')}"] = music_id
                if st.button("Cập nhật TikTok", key=f"tiktok_analyze_{idx}", use_container_width=True):
                    with st.spinner("Đang truy vấn TikTok Research API..."):
                        try:
                            tk = step1_music_hunter.capture_tiktok_trend_snapshot(
                                track,
                                music_id,
                                st.session_state.get("tiktok_client_key", ""),
                                st.session_state.get("tiktok_client_secret", ""),
                                days=7,
                            )
                            st.session_state[f"tiktok_analysis_{track.get('track_id')}"] = tk
                            st.rerun()
                        except Exception as e:
                            st.error(f"Không cập nhật được TikTok: {e}")
                tk = st.session_state.get(f"tiktok_analysis_{track.get('track_id')}")
                if tk:
                    tk_cols = st.columns(4)
                    tk_cols[0].metric("Video dùng nhạc", format_num(tk.get("item_count", 0)))
                    tk_cols[1].metric("Tổng lượt xem", format_num(tk.get("views", 0)))
                    tk_cols[2].metric("Tổng tương tác", format_num(tk.get("likes", 0) + tk.get("comments", 0) + tk.get("shares", 0)))
                    tk_cols[3].metric("Điểm TikTok", tk.get("trend_score") if tk.get("trend_score") is not None else "Chờ")
                    if tk.get("growth_percent") is None:
                        st.info("Đã lưu mốc TikTok đầu tiên. Cập nhật lại sau để đo tốc độ tăng.")
                    else:
                        st.success(f"{tk.get('trend_label')} · +{tk.get('growth_percent', 0):.2f}% lượt xem · +{tk.get('items_delta', 0)} video mới")
                    st.caption(f"{tk.get('snapshot_count', 0)} lần đo · Độ tin cậy: {tk.get('confidence', 'Thấp')} · Khoảng truy vấn: {tk.get('period_days', 7)} ngày")

            yt = st.session_state.get(analysis_key)
            tk = st.session_state.get(f"tiktok_analysis_{track.get('track_id')}")
            cross = build_cross_platform_trend_compat(yt, tk)
            if cross.get("score") is not None:
                st.markdown(
                    f"<div class='selected-track'><span>ĐA NỀN TẢNG</span>"
                    f"<b>{cross.get('score')}/100 · {cross.get('label')}</b>"
                    f"<small>Nguồn: {', '.join(cross.get('sources', []))} · Độ tin cậy: {cross.get('confidence')}</small></div>",
                    unsafe_allow_html=True,
                )
            elif not is_youtube:
                st.caption("Phân tích xu hướng giai đoạn 1 hiện chỉ hỗ trợ YouTube.")

    st.divider()
    back, next_col = st.columns(2)
    with back:
        if st.button("← Quay lại", use_container_width=True):
            _go_to_step(1)
    with next_col:
        if st.button("Tiếp tục tạo ảnh →", type="primary", use_container_width=True, disabled=not bool(selected)):
            _go_to_step(3)

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
    from core.db import get_db_connection
    from core.schemas import validate_data_schema
    from core.project_manager import ProjectManager
    from core.cache_manager import CacheManager
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


def _prepare_uploaded_background(input_path: Path, output_path: Path, zoom_percent: int = 8) -> Path:
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
            with st.expander("AI viết prompt cho Dreamina", expanded=False):
                st.caption("Không bắt buộc. Để trống API key thì hệ thống dùng bộ prompt dựng sẵn.")
                prompt_provider = st.selectbox("Nhà cung cấp", ["Gemini", "API tương thích OpenAI"], key="prompt_provider_choice")
                st.session_state.prompt_api_key = st.text_input(
                    "API key",
                    value=st.session_state.get("prompt_api_key", ""),
                    type="password",
                    placeholder="Dán API key tại đây",
                )
                if prompt_provider == "Gemini":
                    st.session_state.prompt_api_model = st.selectbox(
                        "Model",
                        ["gemini-2.5-flash", "gemini-2.5-pro"],
                        index=0,
                    )
                    st.session_state.prompt_api_url = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
                else:
                    st.session_state.prompt_api_url = st.text_input(
                        "API URL",
                        value=st.session_state.get("prompt_api_url", ""),
                        placeholder="https://.../v1/chat/completions",
                    )
                    st.session_state.prompt_api_model = st.text_input(
                        "Model",
                        value=st.session_state.get("prompt_api_model", ""),
                    )
                status_text = "Sẵn sàng dùng AI" if st.session_state.prompt_api_key else "Đang dùng prompt dựng sẵn"
                st.caption(status_text)

            prompt_value = st.session_state.get("dreamina_prompt", "")
            action_copy, action_new = st.columns(2)
            with action_copy:
                render_copy_prompt_button(prompt_value)
            with action_new:
                if st.button("Tạo prompt mới", key="dreamina_regenerate_prompt", use_container_width=True):
                    prepare_dreamina_prompt(track, force=True)
                    st.toast("Đã tạo prompt mới.", icon="✅")
                    st.rerun()

            with st.expander("Xem hoặc chỉnh sửa prompt", expanded=False):
                edited_prompt = st.text_area(
                    "English prompt",
                    value=prompt_value,
                    height=120,
                    key=f"dreamina_prompt_editor_{st.session_state.get('dreamina_prompt_variant_' + str(track.get('track_id')), 0)}",
                )
                st.session_state.dreamina_prompt = edited_prompt
                st.session_state.image_prompt = edited_prompt
                st.caption("Mô tả tiếng Việt")
                current_variant = int(st.session_state.get("dreamina_prompt_variant_" + str(track.get("track_id")), 0))
                st.write(st.session_state.get("dreamina_prompt_vi") or describe_dreamina_prompt_vi(track, current_variant))

            uploaded_bg = st.file_uploader(
                "Tải ảnh từ Dreamina",
                type=["png", "jpg", "jpeg", "webp"],
                key="dreamina_background_upload",
                help="Sau khi tải ảnh, chọn mức phóng rồi bấm Áp dụng.",
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
            zoom_col, apply_col = st.columns([3, 2])
            with zoom_col:
                zoom_percent = st.slider(
                    "Phóng và cắt viền",
                    min_value=0,
                    max_value=20,
                    value=int(st.session_state.get("dreamina_zoom_percent", 8)),
                    step=1,
                    format="%d%%",
                    disabled=not has_upload,
                    help="0% giữ nguyên vùng ảnh; tăng % để phóng ảnh và cắt bớt mép.",
                )
                st.session_state.dreamina_zoom_percent = zoom_percent
            with apply_col:
                st.write("")
                apply_crop = st.button(
                    "Áp dụng phóng ảnh",
                    type="primary",
                    use_container_width=True,
                    disabled=not has_upload,
                )

            if has_upload:
                desired_key = f"{st.session_state.dreamina_upload_hash}:{zoom_percent}"
                applied_key = st.session_state.get("dreamina_processed_key")
                if applied_key == desired_key:
                    st.success(f"Đã áp dụng mức phóng {zoom_percent}% · ảnh đầu ra 1920×1080")
                    st.progress(zoom_percent / 20 if zoom_percent else 0.0, text=f"Mức phóng hiện tại: {zoom_percent}%")
                elif applied_key:
                    st.warning("Mức phóng đã thay đổi. Bấm Áp dụng phóng ảnh để cập nhật preview.")
                else:
                    st.info("Bấm Áp dụng phóng ảnh để tạo preview Full HD.")

            if apply_crop and has_upload:
                raw_path = None
                try:
                    import hashlib
                    upload_bytes = st.session_state.dreamina_upload_bytes
                    upload_hash = st.session_state.dreamina_upload_hash
                    original_name = st.session_state.get("dreamina_upload_name", "dreamina.png")
                    config.TEMP_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
                    ext = Path(original_name).suffix.lower() or ".png"
                    raw_path = config.TEMP_IMAGE_DIR / f"uploaded_bg_{upload_hash}{ext}"
                    final_path = config.TEMP_IMAGE_DIR / f"bg_full_hd_{upload_hash}_z{zoom_percent}.png"
                    raw_path.write_bytes(upload_bytes)
                    with st.spinner("Đang phóng, crop 16:9 và xuất Full HD..."):
                        _prepare_uploaded_background(raw_path, final_path, zoom_percent=zoom_percent)
                    if not final_path.exists() or final_path.stat().st_size < 1024:
                        raise RuntimeError("File ảnh sau xử lý không hợp lệ.")
                    st.session_state.image_path = str(final_path.resolve())
                    st.session_state.image_source = f"Dreamina · phóng {zoom_percent}% · 1920×1080"
                    st.session_state.dreamina_processed_key = f"{upload_hash}:{zoom_percent}"
                    save_persisted_app_state()
                    st.toast(f"Đã áp dụng phóng {zoom_percent}%.", icon="✅")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Không xử lý được ảnh: {exc}")
                finally:
                    if raw_path:
                        raw_path.unlink(missing_ok=True)

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
                if st.button("Tiếp tục chọn hiệu ứng →", type="primary", use_container_width=True):
                    _go_to_step(4)
        else:
            st.markdown(
                "<div style='min-height:320px;border:1px dashed #343a55;border-radius:12px;"
                "display:flex;align-items:center;justify-content:center;color:#7f879f;'>"
                "Ảnh xem trước sẽ xuất hiện tại đây</div>",
                unsafe_allow_html=True,
            )
            st.caption("Tải ảnh Dreamina hoặc tạo ảnh AI ở cột bên trái.")

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


def render_effect_wizard_step():
    """Chọn hiệu ứng theo bố cục điều khiển trái, preview phải."""
    st.markdown("## Hiệu ứng")
    image_value = st.session_state.get("image_path")
    if not _path_exists(image_value):
        st.warning("Hãy chọn ảnh nền trước.")
        if st.button("← Quay lại ảnh nền", use_container_width=True):
            _go_to_step(3)
        st.stop()

    control_col, preview_col = st.columns([0.78, 1.22], gap="medium")

    with control_col:
        st.markdown("#### Chuyển động ảnh")
        scene_manifest = st.session_state.get("scene_layers_manifest") or {}
        parallax_ready = bool(
            scene_manifest.get("layer_paths")
            or scene_manifest.get("layers")
            or scene_manifest.get("render_layers")
        )
        current_motion = st.session_state.get("motion_mode", "smooth_zoom")
        motion_label = st.radio(
            "Chuyển động ảnh",
            ["Đung đưa nhẹ", "Parallax nhiều lớp"],
            index=1 if current_motion == "parallax" else 0,
            horizontal=True,
            label_visibility="collapsed",
            key="effect_motion_mode",
        )
        requested_motion = "parallax" if motion_label == "Parallax nhiều lớp" else "smooth_zoom"
        if requested_motion == "parallax" and not parallax_ready:
            st.session_state.motion_mode = "smooth_zoom"
            st.warning("Parallax chưa có layer tách nền. Preview đang dùng Đung đưa nhẹ.")
        else:
            st.session_state.motion_mode = requested_motion
        motion_mode = st.session_state.motion_mode
        st.divider()
        st.markdown("#### Hiệu ứng phủ")
        mode = st.radio(
            "Chế độ",
            ["Không hiệu ứng", "Dùng hiệu ứng"],
            horizontal=True,
            index=1 if st.session_state.get("effect_enabled", False) else 0,
            label_visibility="collapsed",
        )
        enabled = mode == "Dùng hiệu ứng"
        st.session_state.effect_enabled = enabled

        selected_effect = None
        if not enabled:
            st.session_state.effect_path = str(_ensure_effect_off_video().resolve())
            st.session_state.effect_preview_path = None
            st.session_state.effect_preview_key = None
            st.info("Ảnh nền được giữ nguyên, không phủ hiệu ứng.")
        else:
            effects = (
                step2_image_provider.list_effect_videos()
                if hasattr(step2_image_provider, "list_effect_videos")
                else sorted(config.EFFECTS_DIR.glob("*.mp4"))
            )
            effects = [item for item in effects if item.name != "effect_off.mp4"]

            if not effects:
                st.warning("Chưa có hiệu ứng trong thư viện.")
                if st.button("Tạo thư viện hiệu ứng mẫu", type="primary", use_container_width=True):
                    with st.spinner("Đang tạo hiệu ứng mẫu..."):
                        step2_image_provider.create_builtin_effect_pack()
                    st.rerun()
            else:
                current_name = Path(st.session_state.effect_path).name if st.session_state.get("effect_path") else effects[0].name
                current_index = next((i for i, item in enumerate(effects) if item.name == current_name), 0)
                selected_name = st.selectbox(
                    "Hiệu ứng",
                    options=[item.name for item in effects],
                    index=current_index,
                    format_func=lambda value: _effect_display_name(Path(value)),
                )
                selected_effect = next(item for item in effects if item.name == selected_name)
                selected_path = str(selected_effect.resolve())
                if st.session_state.get("effect_path") != selected_path:
                    st.session_state.effect_path = selected_path
                    st.session_state.effect_preview_path = None
                    st.session_state.effect_preview_key = None

                st.caption(f"File: {selected_effect.name}")
                library_col, advanced_col = st.columns(2)
                with library_col:
                    if st.button("Làm mới thư viện", use_container_width=True):
                        step2_image_provider.create_builtin_effect_pack()
                        st.rerun()
                with advanced_col:
                    show_analysis = st.toggle("Phân tích cảnh", value=False, help="Tùy chọn nâng cao để tạo mask cảnh.")

                if show_analysis:
                    with st.expander("AI phân tích cảnh", expanded=True):
                        scene_manifest = st.session_state.get("scene_layers_manifest")
                        scene_image_key = str(Path(image_value).resolve())
                        scene_ready = bool(
                            scene_manifest
                            and st.session_state.get("scene_layers_image") == scene_image_key
                            and _path_exists(scene_manifest.get("mask_preview_path"))
                        )
                        force_analysis = st.checkbox("Phân tích lại từ đầu", value=False)
                        if st.button("Phân tích ảnh", use_container_width=True):
                            try:
                                from core.scene_layer_processor import SceneLayerProcessor
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

        if st.button("← Quay lại ảnh nền", use_container_width=True):
            _go_to_step(3)

    with preview_col:
        st.markdown("<div class='sticky-effect-preview-anchor'></div>", unsafe_allow_html=True)
        if not enabled:
            st.image(str(image_value), use_container_width=True)
            st.caption("Xem trước không hiệu ứng")
        elif selected_effect and selected_effect.exists():
            import hashlib
            image_path = Path(image_value)
            preview_key = f"motion-v9-bilinear-sway-10s|{st.session_state.get('motion_mode', 'smooth_zoom')}|{image_path.resolve()}|{selected_effect.resolve()}"
            cached = st.session_state.get("effect_preview_path")
            ready = bool(
                st.session_state.get("effect_preview_key") == preview_key
                and cached
                and Path(cached).exists()
            )
            if not ready:
                preview_path = config.TEMP_IMAGE_DIR / f"effect_preview_{hashlib.sha256(preview_key.encode()).hexdigest()[:12]}.mp4"
                try:
                    with st.spinner("Đang tạo preview 10 giây..."):
                        result = step4_render.build_effect_preview(
                            image_path, selected_effect, preview_path, duration=10,
                            motion_mode=st.session_state.get("motion_mode", "smooth_zoom"),
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
                    st.error(f"Không tạo được preview: {exc}")
                    with st.expander("Thông tin kiểm tra"):
                        st.code(
                            f"Ảnh: {image_path}\nHiệu ứng: {selected_effect}\n"
                            f"Renderer: {getattr(step4_render, '__file__', 'unknown')}\nLỗi: {exc}",
                            language="text",
                        )
            if ready and cached and Path(cached).is_file():
                st.video(str(cached), autoplay=False, loop=True, muted=True)
                st.caption(f"Preview 10 giây · {Path(cached).stat().st_size / 1024 / 1024:.1f} MB · bấm Play để xem")
            else:
                st.image(str(image_path), use_container_width=True)
            st.caption(f"{_effect_display_name(selected_effect)} · {'Đung đưa nhẹ' if st.session_state.get('motion_mode') == 'smooth_zoom' else 'Parallax'} · preview 10 giây")
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
                options=["Tự động", "CPU ổn định"],
                default="Tự động",
                label_visibility="collapsed",
                help="Tự động ưu tiên GPU; CPU ổn định dùng libx264.",
            )
            motion_mode = st.session_state.get("motion_mode", "smooth_zoom")
            selected_encoder = (
                "libx264"
                if encoder_mode == "CPU ổn định"
                else getattr(config, "NVENC_CODEC", "h264_nvenc")
            )

            st.markdown(f"**{track.get('title', 'Untitled')}**")
            st.caption(f"{track.get('author', 'Unknown')} · {target_duration} giây · {selected_encoder}")

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
                _go_to_step(4)

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

        from core.sd_manager import SDInstaller, SDProcessManager, SDHealthChecker

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
    render_final_wizard_step()
    st.stop()
