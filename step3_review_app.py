"""
Bước 3 - Giao diện điều khiển và duyệt quy trình tự động hóa Lo-Fi.
Chạy bằng: streamlit run step3_review_app.py
"""
import random
from pathlib import Path
import streamlit as st

import config
import step1_music_hunter
import step2_image_provider
import step4_render
POLLINATIONS_LABEL = "Pollinations AI (Online, Miễn phí)"

AI_HORDE_LABEL = "AI Horde / Stable Horde (Miễn phí cộng đồng)"

HF_LABEL = "Hugging Face Inference (Free tier)"

SD_LOCAL_LABEL = "Stable Diffusion Local (Automatic1111)"

HOT_GENRES = {
    "☕ Coffee Shop Lofi": "scsearch5:lofi coffee shop copyright free",
    "🌧️ Rainy Night Beats": "scsearch5:lofi rain copyright free",
    "🌸 Anime Aesthetic": "scsearch5:lofi japanese aesthetic free",
    "🎮 Gaming Chill": "scsearch5:lofi gaming copyright free",
    "🎵 NCS Chill Beats": "scsearch5:NoCopyrightSounds lofi chill",
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
        </style>
        """,
        unsafe_allow_html=True,
    )

def init_session_state() -> None:
    """Khởi tạo trạng thái dùng chung cho app."""
    defaults = {
        "query": "scsearch5:NoCopyrightSounds lofi",
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
    }

    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value

    if not st.session_state.candidates:
        try:
            st.session_state.candidates = step1_music_hunter.fetch_candidate_tracks(
                query=st.session_state.query,
                limit=5,
            )
        except Exception:
            st.session_state.candidates = []

    if st.session_state.effect_path is None:
        try:
            st.session_state.effect_path = step2_image_provider.pick_effect_video()
        except Exception:
            st.session_state.effect_path = None

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
    image_ok = bool(st.session_state.get("image_path") and Path(st.session_state.image_path).exists())
    effect_ok = bool(st.session_state.get("effect_path") and Path(st.session_state.effect_path).exists())
    has_track = bool(st.session_state.get("selected_track"))

    with st.sidebar:
        st.markdown("<h2 style='text-align: center; color: #a78bfa; margin-bottom: 0px;'>🎧 Lo-Fi Studio</h2>", unsafe_allow_html=True)
        st.caption("<p style='text-align: center;'>Trình biên tập tự động hóa v4.5</p>", unsafe_allow_html=True)
        
        # 1. Project Management Selector
        st.subheader("📁 Dự Án")
        proj_id = st.text_input("Mã Dự án (Project ID):", value=st.session_state.get("project_id", "lofi_default_prj"), label_visibility="collapsed")
        if "project_id" not in st.session_state or proj_id != st.session_state["project_id"]:
            st.session_state["project_id"] = proj_id
            load_project_to_session_state(proj_id)
            st.rerun()
            
        st.divider()
        
        # 2. Step Selector Dropdown (Gọn gàng không bị tràn thanh cuộn)
        st.subheader("🎛️ Chọn Bước")
        step_names = list(WIZARD_STEPS.values())
        selected_step_name = st.selectbox(
            "Chọn bước:",
            step_names,
            index=st.session_state.current_step - 1,
            label_visibility="collapsed"
        )
        new_step = step_names.index(selected_step_name) + 1
        if new_step != st.session_state.current_step:
            st.session_state.current_step = new_step
            st.rerun()
            
        st.divider()
        
        # 3. Compact Status Dashboard
        st.subheader("📊 Trạng thái nguyên liệu")
        col1, col2 = st.columns(2)
        with col1:
            st.markdown(f"{'✅' if has_track else '❌'} Nhạc")
            st.markdown(f"{'✅' if image_ok else '❌'} Ảnh nền")
        with col2:
            st.markdown(f"{'✅' if effect_ok else '❌'} Hiệu ứng")
            st.markdown(f"{'✅' if st.session_state.get('output_dir') else '❌'} Output")
            
        st.divider()
        st.caption(f"📁 Output:\n`{st.session_state.output_dir}`")
        if st.button("🗑️ Reset Quy Trình", use_container_width=True):
            st.session_state.clear()
            st.rerun()


render_local_sidebar()


def build_image_prompt_from_track(track: dict) -> str:
    """Tạo prompt ảnh local dựa vào tên bài nhạc đang chọn."""
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

    for keywords, mapped_scene in keyword_scenes:
        if any(word in text for word in keywords):
            scene = mapped_scene
            break

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



def _select_track(track: dict, go_next: bool = False):
    st.session_state.selected_track = track
    if go_next:
        st.session_state.current_step = 3
    st.rerun()


def render_music_wizard_step():
    """Màn hình chọn nhạc mới cho UX wizard."""
    st.markdown("## 🎵 Bước 2 - Chọn nhạc")
    st.caption("Chọn bài nhạc trước, sau đó app sẽ dùng tên bài để gợi ý prompt ảnh nền.")

    if st.session_state.selected_track:
        track = st.session_state.selected_track
        st.success(f"Đã chọn: {track.get('title', 'Untitled')} - {track.get('author', 'Unknown')}")

    st.markdown("### 1) Chọn nhanh theo chủ đề")
    genre_items = list(HOT_GENRES.items())
    cols = st.columns(len(genre_items))
    for idx, (genre_name, genre_query) in enumerate(genre_items):
        with cols[idx]:
            if st.button(genre_name, key=f"wiz_genre_{idx}", use_container_width=True):
                st.session_state.query = genre_query
                with st.spinner(f"Đang tìm nhạc: {genre_name}..."):
                    try:
                        st.session_state.candidates = step1_music_hunter.fetch_candidate_tracks(query=genre_query, limit=8)
                    except Exception as e:
                        st.session_state.candidates = []
                        st.error(f"Không tìm được nhạc: {e}")
                st.rerun()

    st.markdown("---")
    st.markdown("### 2) Tìm kiếm thủ công")
    col_query, col_search = st.columns([3, 1])
    with col_query:
        query_input = st.text_input(
            "Từ khóa tìm nhạc:",
            value=st.session_state.query,
            placeholder="Ví dụ: lofi rain copyright free"
        )
    with col_search:
        st.write(" ")
        st.write(" ")
        if st.button("🔍 Tìm nhạc", use_container_width=True):
            st.session_state.query = query_input
            with st.spinner("Đang tìm bài hát..."):
                try:
                    st.session_state.candidates = step1_music_hunter.fetch_candidate_tracks(query=query_input, limit=8)
                except Exception as e:
                    st.session_state.candidates = []
                    st.error(f"Không tìm được nhạc: {e}")
            st.rerun()

    st.markdown("---")
    st.markdown("### 3) Dán URL trực tiếp")
    col_url, col_url_btn = st.columns([3, 1])
    with col_url:
        direct_url = st.text_input(
            "URL YouTube hoặc SoundCloud:",
            placeholder="https://soundcloud.com/... hoặc https://youtube.com/watch?v=..."
        )
    with col_url_btn:
        st.write(" ")
        st.write(" ")
        if st.button("📥 Tải & chọn", use_container_width=True):
            if not direct_url.strip():
                st.warning("Bạn chưa nhập URL.")
            else:
                with st.spinner("Đang phân tích và tải nhạc..."):
                    try:
                        track_info = step1_music_hunter.fetch_track_metadata_by_url(direct_url.strip())
                        step1_music_hunter.download_track(track_info, project_id=st.session_state.get("project_id"))
                        st.session_state.selected_track = track_info
                        if not any(t.get("track_id") == track_info.get("track_id") for t in st.session_state.candidates):
                            st.session_state.candidates.insert(0, track_info)
                        st.success(f"Đã tải và chọn: {track_info.get('title', 'Untitled')}")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Lỗi tải nhạc từ URL: {e}")

    st.markdown("---")
    st.markdown("### 4) Danh sách bài nhạc")
    candidates = st.session_state.get("candidates", [])
    if not candidates:
        st.info("Chưa có kết quả. Hãy chọn chủ đề hoặc bấm Tìm nhạc.")
    else:
        for idx, track in enumerate(candidates):
            is_selected = st.session_state.selected_track and st.session_state.selected_track.get("track_id") == track.get("track_id")
            card_color = "#1e2238" if is_selected else "#161925"
            views = track.get("views", 0)
            likes = track.get("likes", 0)
            stats = f"👁️ {format_num(views)} | ❤️ {format_num(likes)}" if (views or likes) else "Chưa có thống kê"

            st.markdown(
                f"""
                <div style="background:{card_color}; padding:14px; border-radius:10px; border:1px solid #23283f; margin-bottom:8px;">
                    <b>{track.get('title', 'Untitled')}</b><br>
                    <span style="color:#8b92b6; font-size:13px;">{track.get('author', 'Unknown')} | {track.get('source', 'Unknown')} | {stats}</span>
                </div>
                """,
                unsafe_allow_html=True,
            )
            col_select, col_audio, col_next = st.columns([1, 2, 1])
            with col_select:
                if st.button("✅ Chọn", key=f"wiz_select_{idx}", use_container_width=True):
                    _select_track(track, go_next=False)
            with col_audio:
                audio_file = config.INPUT_AUDIO_DIR / f"{track.get('track_id')}.m4a"
                if audio_file.exists():
                    st.audio(str(audio_file))
                else:
                    if st.button("📥 Tải nghe thử", key=f"wiz_download_{idx}", use_container_width=True):
                        with st.spinner("Đang tải nhạc..."):
                            try:
                                step1_music_hunter.download_track(track, project_id=st.session_state.get("project_id"))
                                st.success("Tải nhạc thành công.")
                                st.rerun()
                            except Exception as e:
                                st.error(f"Tải nhạc lỗi: {e}")
            with col_next:
                if st.button("➡️ Chọn & tiếp", key=f"wiz_select_next_{idx}", use_container_width=True):
                    _select_track(track, go_next=True)

    st.markdown("---")
    nav_back, nav_next = st.columns([1, 1])
    with nav_back:
        if st.button("⬅️ Quay lại kiểm tra hệ thống", use_container_width=True):
            _go_to_step(1)
    with nav_next:
        disabled = not bool(st.session_state.selected_track)
        if st.button("➡️ Tiếp tục tạo ảnh", use_container_width=True, type="primary", disabled=disabled):
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


def render_image_wizard_step():
    """Màn hình tạo ảnh nền mới cho UX wizard."""
    st.markdown("## 🎨 Bước 3 - Tạo ảnh nền")
    st.caption("Tạo ảnh theo bài nhạc đã chọn, hoặc tự viết prompt thủ công.")

    is_disabled = not bool(st.session_state.selected_track)
    if is_disabled:
        st.warning("🔒 Vui lòng chọn bài nhạc tại Bước 2 trước khi tạo ảnh nền (Chọn nhạc để viết prompt đúng vibe hơn).")
        track = {}
    else:
        track = st.session_state.selected_track
        st.success(f"Đang lấy vibe từ bài: {track.get('title', 'Untitled')} - {track.get('author', 'Unknown')}")

    left, right = st.columns([1, 1])
    with left:
        st.markdown("### 1) Prompt ảnh")
        col_prompt_1, col_prompt_2 = st.columns([1, 1])
        with col_prompt_1:
            if st.button("🔄 Viết prompt theo bài nhạc", use_container_width=True, disabled=is_disabled):
                st.session_state.image_prompt = build_image_prompt_from_track(track)
                st.success("Đã viết prompt mới theo bài nhạc.")
                st.rerun()
        with col_prompt_2:
            if st.button("🎲 Gợi ý prompt ngẫu nhiên", use_container_width=True, disabled=is_disabled):
                st.session_state.image_prompt = random.choice(config.IMAGE_PROMPTS)
                st.rerun()

        prompt_text = st.text_area(
            "Prompt đang dùng:",
            value=st.session_state.image_prompt,
            height=160,
            disabled=is_disabled,
            help="Có thể sửa trực tiếp prompt trước khi tạo ảnh. Nên dùng tiếng Anh để ảnh ra ổn hơn."
        )
        st.session_state.image_prompt = prompt_text

        st.markdown("### 2) Nguồn tạo ảnh")
        provider_options = [POLLINATIONS_LABEL, AI_HORDE_LABEL, HF_LABEL, SD_LOCAL_LABEL]
        current_provider = st.session_state.image_provider if st.session_state.image_provider in provider_options else POLLINATIONS_LABEL
        provider_choice = st.selectbox(
            "Chọn nguồn tạo ảnh:",
            provider_options,
            index=provider_options.index(current_provider),
            disabled=is_disabled
        )
        st.session_state.image_provider = provider_choice

        if provider_choice == POLLINATIONS_LABEL:
            st.caption("Pollinations dễ dùng nhưng có thể bị giới hạn 429 nếu tạo nhiều ảnh liên tục.")
            st.session_state.pollinations_key = st.text_input(
                "Pollinations API key (không bắt buộc):",
                value=st.session_state.pollinations_key,
                type="password",
                disabled=is_disabled
            )
            st.markdown("🔑 Lấy API key tại: [enter.pollinations.ai](https://enter.pollinations.ai)")
        elif provider_choice == AI_HORDE_LABEL:
            st.caption("AI Horde là mạng GPU cộng đồng miễn phí. Có thể chờ lâu nếu đông người dùng.")
            st.session_state.ai_horde_key = st.text_input(
                "AI Horde API key:",
                value=st.session_state.get("ai_horde_key", "0000000000"),
                type="password",
                disabled=is_disabled,
                help="Để 0000000000 để dùng ẩn danh miễn phí. Có key riêng thì ưu tiên cao hơn."
            )
            st.markdown("🔑 Có thể dùng key ẩn danh `0000000000` hoặc lấy key tại [stablehorde.net](https://stablehorde.net/)")
        elif provider_choice == HF_LABEL:
            st.caption("Hugging Face có free tier cho thử nghiệm nhẹ. Cần token Hugging Face.")
            st.session_state.hf_token = st.text_input(
                "Hugging Face token:",
                value=st.session_state.get("hf_token", ""),
                type="password",
                disabled=is_disabled
            )
            st.session_state.hf_model_id = st.text_input(
                "Model ID:",
                value=st.session_state.get("hf_model_id", "stabilityai/stable-diffusion-xl-base-1.0"),
                disabled=is_disabled
            )
            st.markdown("🔑 Tạo token tại: [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens)")
        else:
            st.caption("SD Local không bị giới hạn online, nhưng cần mở WebUI/ComfyUI local trước.")
            st.session_state.sd_api_url = st.text_input("SD Local API URL:", value=st.session_state.sd_api_url, disabled=is_disabled)
            st.session_state.sd_checkpoint = st.text_input("Checkpoint model:", value=st.session_state.sd_checkpoint, disabled=is_disabled)

        st.markdown("### 3) Hoặc kéo thả ảnh local")
        uploaded_bg = st.file_uploader(
            "Kéo thả ảnh nền của bạn vào đây:",
            type=["png", "jpg", "jpeg", "webp"],
            disabled=is_disabled,
            help="Nếu upload ảnh ở đây, app sẽ dùng ảnh này thay cho ảnh AI."
        )
        if uploaded_bg is not None:
            try:
                config.TEMP_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
                ext = Path(uploaded_bg.name).suffix.lower() or ".png"
                local_bg_path = config.TEMP_IMAGE_DIR / f"uploaded_bg_{random.randint(1000, 9999)}{ext}"
                local_bg_path.write_bytes(uploaded_bg.getbuffer())
                st.session_state.image_path = local_bg_path
                st.success(f"Đã dùng ảnh local: {uploaded_bg.name}")
                st.rerun()
            except Exception as e:
                st.error(f"Không lưu được ảnh upload: {e}")

        st.markdown("### 4) Tạo ảnh bằng AI")
        c1, c2 = st.columns([1, 1])
        with c1:
            if st.button("🎨 Tạo ảnh nền", type="primary", use_container_width=True, disabled=is_disabled):
                with st.spinner("Đang tạo ảnh nền..."):
                    image_path, note = _generate_image_with_fallback(prompt_text, provider_choice)
                    if image_path:
                        st.session_state.image_path = image_path
                        st.success("Đã tạo ảnh nền.")
                        if note:
                            st.warning(note)
                        st.rerun()
                    else:
                        st.error(note)
        with c2:
            if st.button("♻️ Tạo lại ảnh khác", use_container_width=True, disabled=is_disabled):
                with st.spinner("Đang tạo lại ảnh..."):
                    image_path, note = _generate_image_with_fallback(prompt_text, provider_choice)
                    if image_path:
                        st.session_state.image_path = image_path
                        st.success("Đã tạo lại ảnh.")
                        if note:
                            st.warning(note)
                        st.rerun()
                    else:
                        st.error(note)

    with right:
        st.markdown("### Preview ảnh nền")
        if st.session_state.image_path and Path(st.session_state.image_path).exists():
            st.image(str(st.session_state.image_path), caption="Ảnh nền hiện tại", use_container_width=True)
            st.code(str(st.session_state.image_path), language="text")
        else:
            st.info("Chưa có ảnh nền. Bấm 'Tạo ảnh nền' ở bên trái.")

    st.markdown("---")
    nav_back, nav_next = st.columns([1, 1])
    with nav_back:
        if st.button("⬅️ Quay lại chọn nhạc", use_container_width=True):
            _go_to_step(2)
    with nav_next:
        disabled = not bool(st.session_state.image_path and Path(st.session_state.image_path).exists())
        if st.button("➡️ Tiếp tục chọn hiệu ứng", use_container_width=True, type="primary", disabled=disabled):
            _go_to_step(4)


def render_effect_wizard_step():
    """Màn hình chọn hiệu ứng mới cho UX wizard."""
    st.markdown("## ✨ Bước 4 - Chọn hiệu ứng")
    st.caption("Chọn hiệu ứng, tải thêm hiệu ứng online/local, rồi xem thử trực tiếp trên ảnh nền hiện tại.")

    is_disabled = not bool(st.session_state.image_path and Path(st.session_state.image_path).exists())
    if is_disabled:
        st.warning("🔒 Vui lòng tạo/chọn ảnh nền tại Bước 3 trước khi chọn hiệu ứng (Cần ảnh nền để xem thử hiệu ứng).")

    left, right = st.columns([1, 1])
    with left:
        st.markdown("### 1) Hiệu ứng local")
        effects = step2_image_provider.list_effect_videos() if hasattr(step2_image_provider, "list_effect_videos") else sorted(config.EFFECTS_DIR.glob("*.mp4"))
        effect_names = [e.name for e in effects]
        if effect_names:
            current_effect_name = Path(st.session_state.effect_path).name if st.session_state.effect_path else effect_names[0]
            selected_index = effect_names.index(current_effect_name) if current_effect_name in effect_names else 0
            selected_name = st.selectbox("Chọn hiệu ứng:", effect_names, index=selected_index, disabled=is_disabled)
            st.session_state.effect_path = config.EFFECTS_DIR / selected_name
        else:
            st.warning("Chưa có file hiệu ứng .mp4 trong data/effects.")

        c1, c2 = st.columns([1, 1])
        with c1:
            if st.button("✨ Tạo hiệu ứng mẫu", use_container_width=True, disabled=is_disabled):
                try:
                    step2_image_provider.create_builtin_effect_pack()
                    st.success("Đã tạo thêm hiệu ứng mẫu.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Không tạo được hiệu ứng mẫu: {e}")
        with c2:
            if st.button("🔄 Làm mới danh sách", use_container_width=True, disabled=is_disabled):
                st.rerun()

        st.markdown("---")
        st.markdown("### 2) Tải hiệu ứng online từ Pexels")
        pexels_key = st.text_input(
            "Pexels API key:",
            value=st.session_state.get("pexels_api_key", ""),
            type="password",
            disabled=is_disabled,
            help="Key chỉ dùng để tải video hiệu ứng về data/effects.",
        )
        st.session_state.pexels_api_key = pexels_key
        effect_options = [
            "rain overlay",
            "dust particles overlay",
            "film grain overlay",
            "snow overlay",
            "light leak overlay",
            "bokeh overlay",
            "smoke overlay",
            "cinematic particles",
        ]
        suggested_effect = suggest_effect_query_from_track(st.session_state.get("selected_track"))
        suggested_index = effect_options.index(suggested_effect) if suggested_effect in effect_options else 0
        if st.session_state.get("selected_track"):
            track = st.session_state.selected_track
            st.info(
                f"🎵 Gợi ý theo bài đang chọn: `{track.get('title', 'Untitled')}` → `{suggested_effect}`"
            )
        effect_query = st.selectbox(
            "Từ khóa hiệu ứng:",
            effect_options,
            index=suggested_index,
            disabled=is_disabled
        )
        custom_query = st.text_input("Hoặc nhập từ khóa khác:", value="", disabled=is_disabled)
        final_query = custom_query.strip() or effect_query
        if st.button("⬇️ Tải hiệu ứng online", use_container_width=True, disabled=is_disabled):
            with st.spinner("Đang tải hiệu ứng online..."):
                try:
                    downloaded = step2_image_provider.download_pexels_effect(final_query, st.session_state.pexels_api_key)
                    st.session_state.effect_path = downloaded
                    st.success(f"Đã tải hiệu ứng: {downloaded.name}")
                    st.rerun()
                except Exception as e:
                    st.error(f"Không tải được hiệu ứng online: {e}")

        st.markdown("---")
        st.markdown("### 3) Preview hiệu ứng")
        preview_disabled = not bool(st.session_state.effect_path and Path(st.session_state.effect_path).exists())
        if st.button("👁️ Xem thử hiệu ứng trên ảnh hiện tại", type="primary", use_container_width=True, disabled=preview_disabled):
            with st.spinner("Đang tạo preview 5 giây..."):
                try:
                    preview_path = config.TEMP_IMAGE_DIR / "effect_preview.mp4"
                    st.session_state.effect_preview_path = step4_render.build_effect_preview(
                        background_image=Path(st.session_state.image_path),
                        effect_video=Path(st.session_state.effect_path),
                        out_path=preview_path,
                        duration=5,
                    )
                    st.success("Đã tạo preview hiệu ứng.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Lỗi tạo preview hiệu ứng: {e}")

    with right:
        st.markdown("### Ảnh nền hiện tại")
        st.image(str(st.session_state.image_path), caption="Ảnh nền", use_container_width=True)
        if st.session_state.get("effect_path"):
            st.info(f"Hiệu ứng đang chọn: {Path(st.session_state.effect_path).name}")
        if st.session_state.get("effect_preview_path") and Path(st.session_state.effect_preview_path).exists():
            st.markdown("### Preview 5 giây")
            st.video(str(st.session_state.effect_preview_path))
        else:
            st.caption("Chưa có preview. Bấm nút xem thử ở bên trái.")

    st.markdown("---")
    nav_back, nav_next = st.columns([1, 1])
    with nav_back:
        if st.button("⬅️ Quay lại tạo ảnh", use_container_width=True):
            _go_to_step(3)
    with nav_next:
        disabled = not bool(st.session_state.effect_path and Path(st.session_state.effect_path).exists())
        if st.button("➡️ Tiếp tục render", use_container_width=True, type="primary", disabled=disabled):
            _go_to_step(5)


def _path_exists(value) -> bool:
    try:
        return bool(value and Path(value).exists())
    except Exception:
        return False


def render_final_wizard_step():
    """Màn hình render video mới cho UX wizard."""
    st.markdown("## 🚀 Bước 5 - Render video")
    st.caption("Kiểm tra đủ nguyên liệu, chọn thời lượng, rồi render video hoàn chỉnh.")

    has_track = bool(st.session_state.get("selected_track"))
    has_image = _path_exists(st.session_state.get("image_path"))
    has_effect = _path_exists(st.session_state.get("effect_path"))
    output_dir_ok = bool(st.session_state.get("output_dir"))

    st.markdown("### Checklist trước khi render")
    col_a, col_b, col_c, col_d = st.columns(4)
    col_a.metric("Nhạc", "OK" if has_track else "Thiếu")
    col_b.metric("Ảnh nền", "OK" if has_image else "Thiếu")
    col_c.metric("Hiệu ứng", "OK" if has_effect else "Thiếu")
    col_d.metric("Output", "OK" if output_dir_ok else "Thiếu")

    if not has_track:
        st.error("Chưa chọn bài nhạc.")
        if st.button("⬅️ Quay lại chọn nhạc", use_container_width=True):
            _go_to_step(2)
        st.stop()

    if not has_image:
        st.error("Chưa có ảnh nền.")
        if st.button("⬅️ Quay lại tạo ảnh", use_container_width=True):
            _go_to_step(3)
        st.stop()

    if not has_effect:
        st.error("Chưa chọn hiệu ứng.")
        if st.button("⬅️ Quay lại chọn hiệu ứng", use_container_width=True):
            _go_to_step(4)
        st.stop()

    track = st.session_state.selected_track
    audio_file = config.INPUT_AUDIO_DIR / f"{track['track_id']}.m4a"

    st.markdown("---")
    left, right = st.columns([1, 1])

    with left:
        st.markdown("### Thông tin render")
        st.info(f"🎵 Nhạc: {track.get('title', 'Untitled')} - {track.get('author', 'Unknown')}")
        st.code(f"Ảnh nền: {st.session_state.image_path}\nHiệu ứng: {st.session_state.effect_path}\nOutput: {st.session_state.output_dir}", language="text")

        duration_sel = st.radio(
            "Chọn thời lượng video:",
            list(DURATION_OPTIONS.keys()),
            index=0,
        )
        target_duration = DURATION_OPTIONS[duration_sel]

        if st.button("🚀 Render video", type="primary", use_container_width=True):
            try:
                if not audio_file.exists():
                    with st.spinner("Đang tải file nhạc trước khi render..."):
                        step1_music_hunter.download_track(track, project_id=st.session_state.get("project_id"))

                config.VIDEO_DURATION_SECONDS = target_duration
                config.OUTPUT_DIR = Path(st.session_state.output_dir)
                config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

                status_block = st.info("🎬 Đang render video...")
                progress_bar = st.progress(0.0)
                progress_status = st.empty()

                def update_progress(percent):
                    progress_bar.progress(percent)
                    progress_status.markdown(f"**Tiến độ:** `{int(percent * 100)}%`")

                p_id = st.session_state.get("project_id", "lofi_default_prj")
                final_video = step4_render.run_step4(
                    project_id=p_id,
                    audio_path=audio_file,
                    image_path=Path(st.session_state.image_path),
                    effect_path=Path(st.session_state.effect_path),
                    progress_callback=update_progress,
                )

                st.session_state.final_video_path = final_video
                progress_bar.progress(1.0)
                progress_status.markdown("**Tiến độ:** `100%` - Hoàn thành")
                status_block.success("🎉 Render video thành công.")
                st.balloons()
                st.rerun()
            except Exception as e:
                st.error(f"Render lỗi: {e}")

    with right:
        st.markdown("### Preview kết quả")
        if st.session_state.get("final_video_path") and Path(st.session_state.final_video_path).exists():
            st.video(str(st.session_state.final_video_path))
            st.success(f"Đã lưu video: {st.session_state.final_video_path}")
        else:
            st.image(str(st.session_state.image_path), caption="Ảnh nền sẽ dùng để render", use_container_width=True)
            if st.session_state.get("effect_preview_path") and Path(st.session_state.effect_preview_path).exists():
                st.markdown("Preview hiệu ứng đã tạo:")
                st.video(str(st.session_state.effect_preview_path))
            else:
                st.caption("Chưa có video render. Sau khi render xong, video sẽ hiện ở đây.")

    st.markdown("---")
    nav_back, nav_restart = st.columns([1, 1])
    with nav_back:
        if st.button("⬅️ Quay lại chọn hiệu ứng", use_container_width=True):
            _go_to_step(4)
    with nav_restart:
        if st.button("🔁 Render lại", use_container_width=True):
            if "final_video_path" in st.session_state:
                del st.session_state.final_video_path
            st.rerun()


def render_sd_setup_step():
    """Bước 1: kiểm tra hệ thống và hướng dẫn cài/bật Stable Diffusion Local."""
    st.markdown("## ⚙️ Bước 1 - Kiểm tra hệ thống & Stable Diffusion Local")
    st.caption("Bước này giúp biết máy có đủ điều kiện chạy SD Local không và hướng dẫn bật API để app tạo ảnh không phụ thuộc Pollinations.")

    left, right = st.columns([1, 1])

    with left:
        st.markdown("### 1) Kiểm tra nhanh")
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

        st.markdown("### 2) Kiểm tra SD Local API")
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
        st.markdown("### 3) Cài Stable Diffusion Local")
        st.info("App không tự cài Stable Diffusion. Bạn cài Automatic1111 riêng, rồi app kết nối qua API local.")

        st.markdown("**Cần cài trên Windows:**")
        st.markdown("- Python 3.10.6")
        st.markdown("- Git")
        st.markdown("- Automatic1111 Stable Diffusion WebUI")
        st.markdown("- Model SD 1.5 hoặc model anime/lofi nhẹ")

        st.markdown("**Lệnh tải Automatic1111:**")
        st.code("git clone https://github.com/AUTOMATIC1111/stable-diffusion-webui.git", language="bash")

        st.markdown("**Chạy lần đầu:**")
        st.code("cd stable-diffusion-webui\nwebui-user.bat", language="bat")

        st.markdown("**Bật API cho app này:**")
        st.code("webui-user.bat --api --medvram", language="bat")

        st.markdown("**Nếu máy yếu / VRAM thấp:**")
        st.code("webui-user.bat --api --lowvram", language="bat")

        st.warning("Khi SD Local chạy đúng, trình duyệt thường mở WebUI ở http://127.0.0.1:7860. App này sẽ gọi API cùng địa chỉ đó.")

    st.markdown("---")
    col_back, col_next = st.columns([1, 1])
    with col_back:
        if st.button("🔄 Làm mới bước này", use_container_width=True):
            st.rerun()
    with col_next:
        if st.button("➡️ Tiếp tục chọn nhạc", use_container_width=True, type="primary"):
            _go_to_step(2)


# --- UI HEADER ---
st.title("🎧 Lo-Fi Studio: Trình Biên Tập Tự Động Hóa")
st.markdown("Hệ thống trực quan giúp cấu hình thư mục, tìm kiếm nhạc SoundCloud, sinh ảnh nền bằng AI và tiến hành render video hoàn chỉnh.")
render_wizard_header()
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


# Tạo 4 Tab các bước trực quan mới
tab1, tab2, tab3, tab4 = st.tabs([
    "⚙️ BƯỚC 1: CẤU HÌNH HỆ THỐNG",
    "🎵 BƯỚC 2: SĂN NHẠC SOUNDCLOUD",
    "🎨 BƯỚC 3: SINH ẢNH NỀN AI",
    "🚀 BƯỚC 4: DỰNG VIDEO LOCAL"
])

# --- TAB 1: GENERAL CONFIGURATION ---
with tab1:
    st.subheader("⚙️ Cấu hình hệ thống & Nguồn lưu trữ")
    
    # 1. Output directory setting
    st.markdown("### 📁 Thư mục lưu trữ")
    output_dir_input = st.text_input(
        "Đường dẫn thư mục lưu video thành phẩm (Output Directory):",
        value=st.session_state.output_dir,
        help="Nơi lưu trữ các file video lofi (.mp4) sau khi render."
    )
    # Check if folder exists or warn
    out_path_obj = Path(output_dir_input)
    if not out_path_obj.exists():
        st.warning(f"Thư mục '{output_dir_input}' hiện chưa tồn tại. Hệ thống sẽ tự động tạo thư mục này khi chạy.")
    
    # Apply to session state
    st.session_state.output_dir = output_dir_input
    
    # 2. Image API Provider setting
    st.markdown("### 🎨 Cấu hình nhà cung cấp tạo ảnh AI (Image API)")
    image_provider_choice = st.selectbox(
        "Chọn nhà cung cấp tạo ảnh AI:",
        [POLLINATIONS_LABEL, AI_HORDE_LABEL, HF_LABEL, SD_LOCAL_LABEL],
        index=0 if st.session_state.image_provider == POLLINATIONS_LABEL else 1
    )
    st.session_state.image_provider = image_provider_choice
    
    if image_provider_choice == POLLINATIONS_LABEL:
        st.info("💡 Pollinations AI là dịch vụ trực tuyến miễn phí, không yêu cầu cài đặt phần cứng mạnh mẽ.")
        pollinations_key_input = st.text_input(
            "API Key (Tùy chọn - để trống để dùng bản free):",
            value=st.session_state.pollinations_key,
            type="password"
        )
        st.session_state.pollinations_key = pollinations_key_input
    else:
        st.info("💡 Stable Diffusion Local sử dụng tài nguyên phần cứng máy tính của bạn thông qua Automatic1111 WebUI.")
        sd_url_input = st.text_input(
            "Địa chỉ local API URL:",
            value=st.session_state.sd_api_url
        )
        sd_checkpoint_input = st.text_input(
            "Checkpoint Model SD:",
            value=st.session_state.sd_checkpoint
        )
        st.session_state.sd_api_url = sd_url_input
        st.session_state.sd_checkpoint = sd_checkpoint_input
        
    st.success("Cấu hình hệ thống đã được cập nhật tự động.")
    
    # 3. API Status & Limits Check
    st.markdown("---")
    st.markdown("### ⚡ Kiểm tra kết nối & Giới hạn API")
    if st.button("🔍 Kiểm tra trạng thái API tạo ảnh", use_container_width=True):
        with st.spinner("Đang kết nối thử nghiệm..."):
            try:
                import requests
                if "Pollinations" in image_provider_choice:
                    response = requests.head("https://image.pollinations.ai", timeout=5)
                    if response.status_code < 400:
                        st.success("🟢 **Pollinations AI Online (Sẵn sàng)**")
                        st.info("📊 **Số lượt tạo còn lại:** `∞ Không giới hạn (Dịch vụ trực tuyến miễn phí)`")
                    else:
                        st.error(f"🔴 **Lỗi kết nối Pollinations AI:** HTTP {response.status_code}")
                else:
                    response = requests.get(f"{st.session_state.sd_api_url}/sdapi/v1/options", timeout=3)
                    if response.status_code == 200:
                        data = response.json()
                        model_name = data.get("sd_model_checkpoint", "Không rõ")
                        st.success("🟢 **Stable Diffusion Local Online (Sẵn sàng)**")
                        st.info(f"📊 **Số lượt tạo còn lại:** `∞ Không giới hạn (Chạy trên tài nguyên GPU cá nhân)`\n\n🎯 **Checkpoint Model đang chạy:** `{model_name}`")
                    else:
                        st.error(f"🔴 **Lỗi phản hồi từ SD Local:** HTTP {response.status_code}")
            except Exception as e:
                st.error(f"🔴 **Không thể kết nối đến API:** {e}\n\nVui lòng kiểm tra lại cấu hình kết nối hoặc kết nối mạng.")

    # 4. Stable Diffusion Local Manager & Automated Installer (G3-G4)
    st.markdown("---")
    st.markdown("### 🛠️ Trình quản lý & Cài đặt Stable Diffusion Local")
    
    from core.sd_installer import SDInstaller
    from core.sd_process_manager import SDProcessManager
    from core.sd_health import SDHealthChecker

    # --- Lựa chọn chế độ hoạt động ---
    sd_mode_choice = st.radio(
        "Chọn chế độ Stable Diffusion:",
        options=["📁 Trỏ đến bản đã cài (khuyến nghị)", "🚀 Để App tự động tải & cài đặt"],
        index=0 if st.session_state.sd_mode == "existing" else 1,
        horizontal=True,
        help="Nếu bạn đã có AUTOMATIC1111 WebUI trên máy, chọn 'Trỏ đến bản đã cài'. Nếu chưa có, để App tự cài vào thư mục riêng."
    )
    st.session_state.sd_mode = "existing" if "Trỏ" in sd_mode_choice else "app_managed"

    st.markdown("---")

    # ================================================
    # CHẾ ĐỘ 1: TRỎ ĐẾN BẢN ĐÃ CÀI SẴN
    # ================================================
    if st.session_state.sd_mode == "existing":
        st.markdown("#### 📁 Trỏ đến thư mục AUTOMATIC1111 đã cài sẵn trên máy")
        st.caption(
            "Nhập đường dẫn đến thư mục **gốc** của bản AUTOMATIC1111 WebUI mà bạn đã cài. "
            "App sẽ đọc thông tin, kết nối và dùng server đó để tạo ảnh — không thay đổi bất cứ file nào."
        )

        existing_path = st.text_input(
            "Đường dẫn thư mục AUTOMATIC1111:",
            value=st.session_state.sd_install_dir or "",
            placeholder="Ví dụ: D:/stable-diffusion-webui  hoặc  C:/AI/automatic1111",
            help="Thư mục chứa file webui-user.bat hoặc launch.py của AUTOMATIC1111."
        )

        # Phát hiện tự động cấu trúc thư mục
        webui_found = False
        webui_path_obj = Path(existing_path) if existing_path else None
        if webui_path_obj and webui_path_obj.exists():
            # Kiểm tra file đặc trưng của A1111
            has_launch = (webui_path_obj / "launch.py").exists()
            has_bat = (webui_path_obj / "webui-user.bat").exists()
            has_webui = (webui_path_obj / "webui.py").exists()
            webui_found = has_launch or has_bat or has_webui

            if webui_found:
                st.success(f"✅ Phát hiện cài đặt AUTOMATIC1111 hợp lệ tại `{existing_path}`")
                detected_items = []
                if has_bat: detected_items.append("`webui-user.bat`")
                if has_launch: detected_items.append("`launch.py`")
                if has_webui: detected_items.append("`webui.py`")
                st.caption(f"Tìm thấy: {', '.join(detected_items)}")
            else:
                st.warning("⚠️ Thư mục tồn tại nhưng không phát hiện cấu trúc AUTOMATIC1111. Hãy kiểm tra lại đường dẫn.")
        elif existing_path:
            st.error(f"❌ Đường dẫn `{existing_path}` không tồn tại.")

        # Lưu đường dẫn vào session state và config khi bấm xác nhận
        col_confirm, col_test = st.columns(2)
        with col_confirm:
            if st.button("💾 Lưu đường dẫn & Áp dụng", use_container_width=True, disabled=not webui_found):
                st.session_state.sd_install_dir = existing_path
                # Cập nhật sd_api_url nếu muốn thử kết nối mặc định
                st.session_state.sd_api_url = "http://127.0.0.1:7860"
                st.success("✅ Đã lưu đường dẫn! App sẽ kết nối tới server SD tại cổng 7860 khi bạn khởi động WebUI.")
                st.info(
                    "**Bước tiếp theo:** Hãy tự khởi động server AUTOMATIC1111 theo cách thông thường "
                    "(chạy `webui-user.bat`), sau đó quay lại đây bấm **Kiểm tra kết nối** bên dưới."
                )
        with col_test:
            if st.button("🔗 Kiểm tra kết nối SD Server", use_container_width=True):
                import requests as _req
                api_url = st.session_state.sd_api_url
                try:
                    resp = _req.get(f"{api_url}/sdapi/v1/options", timeout=5)
                    if resp.status_code == 200:
                        model_name = resp.json().get("sd_model_checkpoint", "Không rõ")
                        st.success(f"🟢 **SD Server đang chạy!** Model đang dùng: `{model_name}`")
                    else:
                        st.error(f"🔴 Server phản hồi lỗi HTTP {resp.status_code}")
                except Exception as conn_err:
                    st.error(f"🔴 Không kết nối được: {conn_err}\n\nHãy đảm bảo AUTOMATIC1111 đang chạy và API đã bật (`--api` flag).")

        # Hướng dẫn bật API flag
        with st.expander("ℹ️ Cách bật API flag trong AUTOMATIC1111", expanded=False):
            st.markdown("""
**Bước 1:** Mở file `webui-user.bat` trong thư mục AUTOMATIC1111 bằng Notepad.  
**Bước 2:** Tìm dòng bắt đầu bằng `set COMMANDLINE_ARGS=` và thêm `--api` vào.  

Ví dụ:
```batch
set COMMANDLINE_ARGS=--api --medvram
```
**Bước 3:** Lưu file và chạy lại `webui-user.bat`.  
**Bước 4:** Sau khi khởi động xong, quay lại đây bấm **Kiểm tra kết nối**.
            """)

    # ================================================
    # CHẾ ĐỘ 2: APP TỰ QUẢN LÝ CÀI ĐẶT
    # ================================================
    else:
        st.markdown("#### 🚀 Cài đặt tự động Stable Diffusion WebUI (do App quản lý)")
        st.caption("App sẽ tải mã nguồn AUTOMATIC1111 v1.6.0 (commit chính thức), tạo môi trường Python riêng và quản lý tiến trình — không ảnh hưởng Python hệ thống.")

        install_path_input = st.text_input(
            "Thư mục đích cài đặt SD:",
            value=st.session_state.sd_install_dir or "D:/AI/LofiStudioAI",
            help="App sẽ tạo thư mục này nếu chưa tồn tại. Cần ít nhất 10GB dung lượng trống."
        )

        # Kiểm tra trạng thái cài đặt hiện hữu
        state_file_path = Path(install_path_input) / "install_state.json"
        is_app_installed = False
        install_state_data = {}
        if state_file_path.exists():
            try:
                with open(state_file_path, "r", encoding="utf-8") as sf:
                    install_state_data = json.load(sf)
                    is_app_installed = install_state_data.get("installed", False)
            except Exception:
                pass

        if is_app_installed:
            ver = install_state_data.get("version", "?")
            st.success(f"✅ Phát hiện bản cài đặt App-managed ({ver}) tại `{install_path_input}`")
        else:
            st.info("ℹ️ Chưa có bản cài đặt do App quản lý. Bấm nút bên dưới để bắt đầu.")

        # Preflight Check trước khi cài
        with st.expander("🩺 Kiểm tra phần cứng trước khi cài đặt", expanded=not is_app_installed):
            if st.button("Chạy kiểm tra phần cứng", use_container_width=True, key="btn_preflight_managed"):
                with st.spinner("Đang kiểm tra hệ thống..."):
                    preflight = SDInstaller.run_preflight(Path(install_path_input))
                pc1, pc2, pc3, pc4, pc5 = st.columns(5)
                check_map = {
                    "os_check": (pc1, "OS"),
                    "gpu_check": (pc2, "GPU/VRAM"),
                    "ram_check": (pc3, "RAM"),
                    "disk_check": (pc4, "Ổ đĩa"),
                    "write_permission": (pc5, "Quyền ghi")
                }
                for key, (col, label) in check_map.items():
                    status = preflight.get(key, "not_applicable")
                    with col:
                        if status == "passed":
                            st.success(f"✅ {label}")
                        elif status == "warning":
                            st.warning(f"⚠️ {label}")
                        else:
                            st.error(f"❌ {label}")

                if preflight["overall"] == "passed":
                    st.success("Hệ thống đủ điều kiện cài đặt tự động!")
                elif preflight["overall"] == "warning":
                    st.warning("Hệ thống đạt tối thiểu. Có thể gặp vấn đề về hiệu năng.")
                else:
                    st.error("Hệ thống không đủ điều kiện. Vui lòng xem chi tiết lỗi bên dưới.")

                for err in preflight["errors"]:
                    st.error(f"- {err}")
                for wrn in preflight["warnings"]:
                    st.warning(f"- {wrn}")

        if st.button("🚀 Bắt đầu tải & cài đặt tự động", use_container_width=True, key="btn_install_managed"):
            progress_bar = st.progress(0.0)
            status_text = st.empty()

            def update_progress(percent, msg):
                progress_bar.progress(float(percent))
                status_text.markdown(f"**Trạng thái:** {msg} `({int(float(percent)*100)}%)`")

            try:
                success = SDInstaller.install(Path(install_path_input), progress_callback=update_progress)
                if success:
                    st.session_state.sd_install_dir = install_path_input
                    st.success("🎉 Cài đặt Stable Diffusion Local WebUI thành công!")
                    st.rerun()
            except Exception as e:
                st.error(f"❌ Cài đặt thất bại: {e}")

        # Bảng điều khiển server (chỉ hiện khi đã cài xong)
        if is_app_installed:
            st.markdown("---")
            st.markdown("#### 🎛️ Bảng điều khiển Server Stable Diffusion")
            st.caption("Khởi động hoặc dừng server WebUI trên địa chỉ loopback 127.0.0.1 (bảo mật).")

            server_running = install_state_data.get("running", False)
            server_pid = install_state_data.get("process_identity")
            server_port = install_state_data.get("configured_port", 7860)

            if server_running:
                st.success(f"🟢 **Server SD đang hoạt động** — PID: `{server_pid}` | Port: `{server_port}`")
            else:
                st.info("⚪ **Server SD đang tắt**")

            c_start, c_stop, c_health = st.columns(3)
            with c_start:
                if st.button("🚀 Khởi động Server", use_container_width=True, disabled=server_running, key="btn_start_srv"):
                    try:
                        pid = SDProcessManager.start_process(Path(install_path_input), port=7860)
                        st.session_state.sd_install_dir = install_path_input
                        st.success(f"Khởi động thành công! PID: {pid}")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Lỗi khởi động: {e}")
            with c_stop:
                if st.button("🛑 Dừng Server", use_container_width=True, disabled=not server_running, key="btn_stop_srv"):
                    try:
                        SDProcessManager.stop_process(Path(install_path_input))
                        st.success("Đã dừng server thành công.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Lỗi dừng: {e}")
            with c_health:
                if st.button("🩺 Health Check", use_container_width=True, key="btn_health_managed"):
                    with st.spinner("Đang kiểm tra sức khỏe API..."):
                        rpt_path = Path(install_path_input) / "sd_health_report.json"
                        report = SDHealthChecker.run_health_check(f"http://127.0.0.1:{server_port}", rpt_path)
                        if report["generation_check"] == "passed":
                            st.success("🟢 Health check PASSED — API, Model và render test OK!")
                        else:
                            st.error(f"🔴 Health check FAILED: {report.get('error_detail', 'Unknown')}")

            # Log viewer
            log_file = Path(install_path_input) / "logs" / "webui.log"
            if log_file.exists():
                st.markdown("##### 📄 Logs thời gian thực (100 dòng cuối)")
                try:
                    with open(log_file, "r", encoding="utf-8", errors="ignore") as lf:
                        lines = lf.readlines()[-100:]
                    st.text_area("WebUI Logs", value="".join(lines), height=220, label_visibility="collapsed")
                except Exception as e:
                    st.error(f"Không đọc được log: {e}")

# --- TAB 2: MUSIC HUNTER ---
with tab2:
    st.subheader("🔍 Tìm kiếm âm nhạc miễn phí bản quyền")
    
    # Đề xuất các thể loại Lo-Fi Hot
    st.markdown("### 🔥 Đề xuất thể loại Lo-Fi Hot nhất hiện nay")
    cols_genres = st.columns(len(HOT_GENRES))
    for idx, (genre_name, genre_query) in enumerate(HOT_GENRES.items()):
        with cols_genres[idx]:
            if st.button(genre_name, key=f"genre_{idx}", use_container_width=True):
                st.session_state.query = genre_query
                with st.spinner(f"Đang tìm nhạc '{genre_name}'..."):
                    st.session_state.candidates = step1_music_hunter.fetch_candidate_tracks(query=genre_query, limit=5)
                st.rerun()
                
    st.write(" ")
    col_q, col_btn = st.columns([3, 1])
    with col_q:
        query_input = st.text_input(
            "Tự nhập từ khóa tìm kiếm SoundCloud:",
            value=st.session_state.query
        )
    with col_btn:
        st.write(" ")
        st.write(" ")
        if st.button("🔍 Tìm nhạc tùy chỉnh", use_container_width=True):
            st.session_state.query = query_input
            with st.spinner("Đang tìm bài hát..."):
                st.session_state.candidates = step1_music_hunter.fetch_candidate_tracks(query=query_input, limit=5)
            st.rerun()
            
    # Bổ sung nhập link nhạc trực tiếp
    st.markdown("---")
    st.markdown("### 📥 Dán trực tiếp URL bài hát (YouTube / SoundCloud)")
    col_url, col_url_btn = st.columns([3, 1])
    with col_url:
        direct_url = st.text_input("Nhập URL bài hát cụ thể:", placeholder="https://soundcloud.com/... hoặc https://youtube.com/watch?v=...")
    with col_url_btn:
        st.write(" ")
        st.write(" ")
        if st.button("📥 Tải & Chọn bài này", use_container_width=True):
            if direct_url:
                with st.spinner("Đang phân tích thông tin và tải nhạc..."):
                    try:
                        track_info = step1_music_hunter.fetch_track_metadata_by_url(direct_url)
                        step1_music_hunter.download_track(track_info, project_id=st.session_state.get("project_id"))
                        st.session_state.selected_track = track_info
                        # Thêm vào danh sách ứng viên làm nổi bật
                        if not any(t["track_id"] == track_info["track_id"] for t in st.session_state.candidates):
                            st.session_state.candidates.insert(0, track_info)
                        st.success(f"Tải và chọn thành công bài: {track_info['title']}")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Lỗi tải nhạc từ URL: {e}")
            else:
                st.warning("Vui lòng nhập đường dẫn URL.")
            
    if st.session_state.candidates:
        st.write("---")
        st.write("### 🎶 Danh sách bài nhạc tìm thấy (Được sắp xếp theo lượt nghe)")
        
        for idx, track in enumerate(st.session_state.candidates):
            is_selected = st.session_state.selected_track and st.session_state.selected_track["track_id"] == track["track_id"]
            bg_color = "#1e2238" if is_selected else "#161925"
            
            views = track.get("views", 0)
            likes = track.get("likes", 0)
            stats_str = f" | 👁️ {format_num(views)} lượt nghe | ❤️ {format_num(likes)} thích" if (views or likes) else ""
            
            st.markdown(
                f'<div style="background-color: {bg_color}; padding: 15px; border-radius: 8px; margin-bottom: 10px; border: 1px solid #23283f;">'
                f'<strong>{track["title"]}</strong><br>'
                f'<span style="color: #8b92b6; font-size: 13px;">Tác giả: {track["author"]} | Nguồn: {track["source"]}{stats_str}</span>'
                f'</div>',
                unsafe_allow_html=True
            )
            
            col_sel, col_play = st.columns([1, 3])
            with col_sel:
                if st.button("👉 Chọn bài này", key=f"sel_{idx}", use_container_width=True):
                    st.session_state.selected_track = track
                    st.success(f"Đã chọn bài nhạc: {track['title']}")
                    st.rerun()
            with col_play:
                audio_file = config.INPUT_AUDIO_DIR / f"{track['track_id']}.m4a"
                if audio_file.exists():
                    st.audio(str(audio_file))
                else:
                    if st.button("📥 Tải về để nghe thử", key=f"dl_{idx}"):
                        with st.spinner("Đang tải..."):
                            try:
                                step1_music_hunter.download_track(track, project_id=st.session_state.get("project_id"))
                                st.success("Tải nhạc thành công!")
                                st.rerun()
                            except Exception as e:
                                st.error(f"Tải nhạc lỗi: {e}")
    else:
        st.info("Chưa có kết quả tìm kiếm. Hãy nhấn nút 'Tìm nhạc' ở trên.")

# --- TAB 3: IMAGE PROVIDER ---
with tab3:
    st.subheader("🎨 Thiết kế ảnh nền AI cho Video")

    st.markdown("### 🎵 Prompt theo bài nhạc")
    if st.session_state.selected_track:
        track = st.session_state.selected_track
        st.info(f"Đang lấy cảm hứng từ bài: **{track.get('title', 'Untitled')}** - {track.get('author', 'Unknown')}")
        if st.button("🔄 Đổi prompt theo bài nhạc", use_container_width=True):
            st.session_state.image_prompt = build_image_prompt_from_track(track)
            st.success("Đã viết prompt mới theo bài nhạc đang chọn.")
            st.rerun()
    else:
        st.warning("Chưa chọn bài nhạc. Hãy chọn bài ở Bước 2 để app tự viết prompt đúng vibe hơn.")

    prompt_select = st.selectbox(
        "Ý tưởng chủ đề gợi ý:",
        ["Tự viết Prompt tùy chỉnh"] + config.IMAGE_PROMPTS
    )
    
    if prompt_select == "Tự viết Prompt tùy chỉnh":
        prompt_text = st.text_area("Nhập mô tả cảnh bằng tiếng Anh:", value=st.session_state.image_prompt)
    else:
        prompt_text = st.text_area("Mô tả cảnh đang chọn:", value=prompt_select)
        
    st.session_state.image_prompt = prompt_text
    
    col_ctrl, col_show = st.columns([1, 1])
    with col_ctrl:
        # Cấu hình dynamic các API key/đường dẫn trước khi gọi
        config.POLLINATIONS_API_KEY = st.session_state.pollinations_key
        config.SD_LOCAL_API_URL = st.session_state.sd_api_url
        config.SD_LOCAL_CHECKPOINT = st.session_state.sd_checkpoint
        
        provider_name = st.session_state.image_provider
        provider_label = "Pollinations" if "Pollinations" in provider_name else "SD Local"
        
        if st.button(f"🎨 Tạo ảnh bằng AI ({provider_label})", type="primary", use_container_width=True):
            with st.spinner("AI đang vẽ tranh..."):
                try:
                    config.TEMP_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
                    out_path = config.TEMP_IMAGE_DIR / f"bg_{random.randint(1000, 9999)}.png"
                    
                    if "Pollinations" in provider_name:
                        provider = step2_image_provider.PollinationsProvider()
                    else:
                        provider = step2_image_provider.SDLocalProvider()
                        
                    st.session_state.image_path = provider.generate(prompt_text, out_path)
                    st.success("Đã sinh ảnh thành công!")
                except Exception as e:
                    error_text = str(e)
                    is_pollinations_rate_limit = "Pollinations" in provider_name and ("429" in error_text or "Too Many Requests" in error_text)

                    if is_pollinations_rate_limit:
                        st.warning("Pollinations đang bị giới hạn lượt tạo ảnh. App sẽ thử chuyển sang SD Local trên máy bạn.")
                        try:
                            fallback_path = config.TEMP_IMAGE_DIR / f"bg_sd_{random.randint(1000, 9999)}.png"
                            fallback_provider = step2_image_provider.SDLocalProvider()
                            st.session_state.image_path = fallback_provider.generate(prompt_text, fallback_path)
                            st.success("Đã tạo ảnh bằng SD Local thay cho Pollinations.")
                        except Exception as sd_error:
                            st.error(
                                "Pollinations đang quá tải hoặc giới hạn lượt tạo ảnh. "
                                "SD Local cũng chưa chạy được. Hãy mở Stable Diffusion WebUI/ComfyUI local rồi thử lại, "
                                "hoặc đợi vài phút rồi tạo lại bằng Pollinations."
                            )
                            st.caption(_short_error_message(sd_error, "SD Local"))
                    else:
                        st.error(_short_error_message(e, provider_name))
                    
        st.write("---")
        st.write("### 🎬 Chọn hiệu ứng động")
        effects = list(config.EFFECTS_DIR.glob("*.mp4"))
        effect_names = [e.name for e in effects]
        if effect_names:
            selected_eff = st.selectbox("Hiệu ứng hạt bụi bay / mưa rơi:", effect_names, index=0)
            st.session_state.effect_path = config.EFFECTS_DIR / selected_eff
        else:
            st.warning("Không tìm thấy hiệu ứng video trong data/effects. Hệ thống tự động dùng hiệu ứng trống mặc định.")
            
    with col_show:
        if st.session_state.image_path and Path(st.session_state.image_path).exists():
            st.image(str(st.session_state.image_path), caption="Ảnh nền thiết kế hiện tại", use_container_width=True)
        else:
            st.info("Chưa có hình ảnh. Vui lòng bấm nút 'Tạo ảnh bằng AI' bên trái.")

# --- TAB 4: RENDER VIDEO ---
with tab4:
    st.subheader("🚀 Bắt đầu sản xuất Video Local")
    
    # Kiểm tra dữ liệu hợp lệ
    ready = True
    if not st.session_state.selected_track:
        st.error("❌ Chưa chọn bài hát (Xem lại Bước 2)")
        ready = False
    if not st.session_state.image_path or not Path(st.session_state.image_path).exists():
        st.error("❌ Chưa chuẩn bị ảnh nền AI (Xem lại Bước 3)")
        ready = False
        
    if ready:
        st.success("✅ Đầy đủ nguyên liệu! Mọi thông số hợp lệ để bắt đầu biên tập.")
        track = st.session_state.selected_track
        audio_file = config.INPUT_AUDIO_DIR / f"{track['track_id']}.m4a"
        
        col_duration, col_run = st.columns([1, 1])
        with col_duration:
            duration_sel = st.radio(
                "Lựa chọn thời lượng video:",
                list(DURATION_OPTIONS.keys()),
                index=0
            )
            target_duration = DURATION_OPTIONS[duration_sel]
            
        with col_run:
            st.write(" ")
            st.write(" ")
            if st.button("🚀 RENDER VIDEO LOCAL", type="primary", use_container_width=True):
                # Download audio if missing
                if not audio_file.exists():
                    with st.spinner("Đang chuẩn bị âm thanh..."):
                        step1_music_hunter.download_track(track, project_id=st.session_state.get("project_id"))
                
                # Áp dụng cấu hình và tạo thư mục lưu nếu cần
                config.VIDEO_DURATION_SECONDS = target_duration
                config.OUTPUT_DIR = Path(st.session_state.output_dir)
                config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
                
                status_block = st.info("🎬 Bắt đầu biên tập video...")
                
                # Khởi tạo thanh tiến độ
                progress_bar = st.progress(0.0)
                progress_status = st.empty()
                
                def update_progress(percent):
                    progress_bar.progress(percent)
                    progress_status.markdown(f"**Tiến độ xử lý:** `{int(percent * 100)}%` (Audio: 0-30%, Video: 30-100%)")
                
                try:
                    p_id = st.session_state.get("project_id", "lofi_default_prj")
                    
                    # Dựng video bằng FFmpeg
                    status_block.info("🎥 Đang tiến hành ghép nhạc và render video qua FFmpeg. Vui lòng đợi...")
                    final_video = step4_render.run_step4(
                        project_id=p_id,
                        audio_path=audio_file,
                        image_path=Path(st.session_state.image_path),
                        effect_path=Path(st.session_state.effect_path),
                        progress_callback=update_progress
                    )
                    
                    progress_bar.progress(1.0)
                    progress_status.markdown("**Tiến độ xử lý:** `100%` - Hoàn thành!")
                    status_block.success("🎉 Dựng video thành công!")
                    st.info(f"💾 Video thành phẩm của bạn đã lưu tại: `{final_video}`")
                    
                    st.markdown("### 📺 Nghe thử & Xem thử Video thành phẩm:")
                    st.video(str(final_video))
                    
                    st.balloons()
                         
                except Exception as e:
                    status_block.error(f"❌ Có lỗi phát sinh trong lúc biên tập: {e}")
