"""
AI FILE NOTE - CONFIG (CẤU HÌNH TRUNG TÂM)
Chức năng chính:
- Cấu hình chung cho toàn bộ pipeline, đọc từ biến môi trường (.env qua _load_dotenv) để không lộ API key khi đẩy code lên git.
- Định nghĩa đường dẫn thư mục làm việc (data/*, DB, cache, output...) và tự tạo thư mục khi import.
- Gom mọi hằng số/tham số: nguồn nhạc, thị trường & RPM YouTube, provider ảnh (Pollinations/AIHorde/HuggingFace/Cloudflare/SD local), LLM viết prompt, phân tích/tách lớp cảnh, thông số FFmpeg/render, upload YouTube, thư viện hiệu ứng Pixabay và chữ động.
Đầu vào chính:
- Biến môi trường trong file .env cùng thư mục (KEY=VALUE, hỗ trợ utf-8-sig bỏ BOM).
Đầu ra chính:
- Các biến module cấp cao (BASE_DIR, *_DIR, *_API_KEY, IMAGE_*, VIDEO_*, YOUTUBE_*, ...) và các thư mục được mkdir sẵn.
API được file khác sử dụng:
- Import trực tiếp `import config` rồi đọc thuộc tính; nhiều file dùng `getattr(config, ...)`. Một số biến bị ghi đè lúc chạy (VIDEO_DURATION_SECONDS, ENABLE_YOUTUBE_UPLOAD, SD_LOCAL_API_URL, PROMPT_API_*).
Phụ thuộc quan trọng:
- Chỉ dùng thư viện chuẩn (os, pathlib). Không import module nội bộ khác để tránh vòng lặp import.
Lưu ý khi sửa:
- File này có side effect khi import (tạo thư mục, nạp .env); giữ nhẹ và không nạp thư viện nặng.
- Nhiều giá trị được ép về khoảng an toàn qua max/min; giữ pattern này khi thêm biến từ env.
- Không hard-code API key; luôn đọc qua os.getenv với mặc định rỗng.
"""
import os
from pathlib import Path


def _load_dotenv(env_path: Path):
    """Nạp file .env dạng KEY=VALUE vào biến môi trường (không đè biến đã có)."""
    if not env_path.is_file():
        return
    # utf-8-sig: tự bỏ BOM nếu file được lưu từ Notepad/PowerShell Windows
    for line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv(Path(__file__).parent / ".env")

# --- Đường dẫn thư mục làm việc ---
BASE_DIR = Path(__file__).parent
INPUT_AUDIO_DIR = BASE_DIR / "data" / "input_audio"
CACHE_DIR = BASE_DIR / "data" / "cache"
TEMP_IMAGE_DIR = CACHE_DIR / "temp_image"
EFFECTS_DIR = BASE_DIR / "data" / "effects"       # video mưa, bụi, đĩa than (asset tĩnh)
OUTPUT_DIR = BASE_DIR / "data" / "output_final"
METADATA_DIR = CACHE_DIR / "metadata"      # log license nhạc, ảnh
DB_DIR = BASE_DIR / "data" / "database"
CONFIG_DIR = BASE_DIR / "data" / "config"

DB_PATH = DB_DIR / "lofi_automation.db"
TREND_DB_PATH = DB_DIR / "music_trends.sqlite3"
PROMPT_SETTINGS_FILE = CONFIG_DIR / "prompt_api_settings.json"
SETTINGS_FILE = CONFIG_DIR / "settings.json"

for d in [INPUT_AUDIO_DIR, CACHE_DIR, TEMP_IMAGE_DIR, EFFECTS_DIR, OUTPUT_DIR, METADATA_DIR, DB_DIR, CONFIG_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# --- API khám phá xu hướng nhạc (Bước 1) ---
# Đặt key thật trong .env, không ghi trực tiếp vào mã nguồn.
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "").strip()
YOUTUBE_TREND_REGION = os.getenv("YOUTUBE_TREND_REGION", "VN").strip().upper() or "VN"
YOUTUBE_TREND_MAX_RESULTS = max(5, min(int(os.getenv("YOUTUBE_TREND_MAX_RESULTS", "20")), 50))
LASTFM_API_KEY = os.getenv("LASTFM_API_KEY", "").strip()
LASTFM_CHART_LIMIT = max(10, min(int(os.getenv("LASTFM_CHART_LIMIT", "100")), 200))
YOUTUBE_TREND_DAILY_SCAN_LIMIT = max(1, min(int(os.getenv("YOUTUBE_TREND_DAILY_SCAN_LIMIT", "40")), 100))
YOUTUBE_TREND_CACHE_MINUTES = max(1, int(os.getenv("YOUTUBE_TREND_CACHE_MINUTES", "15")))
LASTFM_CHART_CACHE_MINUTES = max(5, int(os.getenv("LASTFM_CHART_CACHE_MINUTES", "60")))
TREND_CACHE_SCHEMA_VERSION = "2-license-status"
RPM_BENCHMARK_UPDATED = "2026-07"
RPM_BENCHMARK_NOTE = "Benchmark tham khảo cho nội dung âm nhạc; không phải dữ liệu YouTube Studio hay mức YouTube cam kết."

# Thị trường quét và RPM nhạc ước tính (USD / 1.000 lượt xem).
# Đây là benchmark tham khảo, không phải mức YouTube cam kết. RPM thật phải xem trong YouTube Studio.
YOUTUBE_MARKETS = {
    "CN": {"name": "Trung Quốc", "rpm": (0.30, 1.20), "note": "YouTube bị hạn chế tại Trung Quốc đại lục; kết quả theo vùng có thể ít."},
    "VN": {"name": "Việt Nam", "rpm": (0.12, 0.50)},
    "US": {"name": "Hoa Kỳ", "rpm": (1.50, 4.50)},
    "GB": {"name": "Vương quốc Anh", "rpm": (1.40, 4.20)},
    "AU": {"name": "Úc", "rpm": (1.60, 4.80)},
    "CA": {"name": "Canada", "rpm": (1.30, 4.00)},
    "DE": {"name": "Đức", "rpm": (1.20, 4.00)},
    "FR": {"name": "Pháp", "rpm": (0.80, 2.50)},
    "JP": {"name": "Nhật Bản", "rpm": (0.80, 2.50)},
    "KR": {"name": "Hàn Quốc", "rpm": (0.60, 2.00)},
    "SG": {"name": "Singapore", "rpm": (0.80, 2.80)},
    "HK": {"name": "Hong Kong", "rpm": (0.70, 2.50)},
    "TW": {"name": "Đài Loan", "rpm": (0.45, 1.50)},
    "TH": {"name": "Thái Lan", "rpm": (0.15, 0.80)},
    "MY": {"name": "Malaysia", "rpm": (0.20, 1.00)},
    "PH": {"name": "Philippines", "rpm": (0.10, 0.40)},
    "ID": {"name": "Indonesia", "rpm": (0.12, 0.60)},
    "IN": {"name": "Ấn Độ", "rpm": (0.10, 0.50)},
    "BR": {"name": "Brazil", "rpm": (0.20, 1.20)},
    "MX": {"name": "Mexico", "rpm": (0.25, 1.50)},
}


YOUTUBE_TREND_DEFAULT_MARKETS = ["VN", "HK", "TW", "CN"]
YOUTUBE_MARKET_PRESETS = {
    "Nhạc Việt": ["VN"],
    "Nhạc Trung": ["HK", "TW", "CN"],
    "Việt + Trung": ["VN", "HK", "TW", "CN"],
}
YOUTUBE_TREND_MAX_MARKETS_PER_SCAN = max(1, min(int(os.getenv("YOUTUBE_TREND_MAX_MARKETS_PER_SCAN", "4")), 4))

# Chỉ giữ 3 hướng nội dung: nhạc Việt, nhạc Trung và Lofi thư giãn.
LOFI_TREND_PRESETS = {
    "Nhạc Việt thư giãn": "Vietnamese relaxing music lofi instrumental",
    "Vietnamese Lofi": "Vietnamese lofi chill instrumental",
    "Nhạc Trung thư giãn": "Chinese relaxing music lofi instrumental",
    "Chinese Lofi": "Chinese lofi chill instrumental",
    "Cổ phong thư giãn": "Chinese traditional relaxing instrumental guzheng",
    "Lofi học tập": "lofi study beats instrumental",
    "Lofi thư giãn": "lofi chill beats instrumental",
    "Lofi ngủ": "soft lofi sleep instrumental",
    "Lofi mưa đêm": "rainy night lofi instrumental",
    "Lofi quán cà phê": "coffee shop lofi instrumental",
    "Lofi piano": "lofi piano relaxing instrumental",
    "Lofi ambient": "ambient lofi relaxing instrumental",
}

# Từ khóa bắt buộc để loại các thể loại ngoài phạm vi ngay sau khi quét.
MUSIC_FOCUS_KEYWORDS = (
    # Nhạc Trung được đăng lại theo kiểu mà người Việt thường tìm.
    "vietsub", "pinyin", "lyrics", "lyric", "nhạc trung", "nhac trung", "nhạc hoa", "nhac hoa",
    "douyin", "tiktok china", "中文", "华语", "華語", "國語", "国语", "古风", "古風",
    "china", "chinese", "mandarin", "c-pop", "cpop", "guzheng",
    # Nhạc Việt phổ biến, lyric, audio và MV.
    "nhạc việt", "nhac viet", "v-pop", "vpop", "vietnam", "vietnamese", "official mv",
    "official audio", "music video", "bản hợp xướng", "hop xuong",
    # Nhạc Lofi/thư giãn vẫn là nhóm riêng.
    "lofi", "lo-fi", "chill", "relax", "relaxing", "sleep", "study", "ambient", "piano",
    "rain", "coffee", "cafe", "instrumental",
)
MUSIC_EXCLUDED_KEYWORDS = (
    # Chỉ loại rõ nội dung ngoài ba phạm vi, không loại pop/ballad/remix/rap Việt hoặc Trung.
    "bollywood", "hindi", "punjabi", "tamil", "telugu", "bhojpuri",
    "kpop", "k-pop", "jpop", "j-pop", "thai pop", "indonesian pop",
)


# Các nguồn âm thanh có thể nghiên cứu để phối thành Lofi. Chỉ nên dùng khi license là CC BY
# hoặc người dùng có giấy phép riêng. Preset không tự cấp quyền remix.
LOFI_SOURCE_PRESETS = {
    "Jazz instrumental": "jazz instrumental creative commons",
    "Piano nhẹ": "soft piano instrumental creative commons",
    "Guitar jazz": "jazz guitar instrumental creative commons",
    "Ambient": "ambient instrumental creative commons",
    "Synthwave chậm": "slow synthwave instrumental creative commons",
    "Bossa Nova": "bossa nova instrumental creative commons",
    "Chillhop": "chillhop instrumental creative commons",
    "Boom bap chậm": "slow boom bap instrumental creative commons",
}

OTHER_TREND_PRESETS = {
    "Phonk": "phonk instrumental",
    "Indie Pop": "indie pop instrumental",
    "Hip Hop": "hip hop instrumental",
    "Nhạc game": "game soundtrack instrumental",
    "Ambient": "ambient instrumental",
    "Jazz": "jazz instrumental",
    "Piano": "piano instrumental",
    "Synthwave": "synthwave instrumental",
}


# --- Nguồn nhạc whitelist (Bước 1) ---
# Mỗi nguồn là 1 query yt-dlp: "scsearchN:" = SoundCloud, "ytsearchN:" = YouTube.
# Bộ săn nhạc duyệt lần lượt TẤT CẢ nguồn, gộp ứng viên và khử trùng lặp;
# một nguồn lỗi/timeout sẽ bị bỏ qua chứ không làm hỏng cả batch.
MUSIC_SEARCH_SOURCES = [
    {
        "name": "SoundCloud (NCS)",
        "query": "scsearch{limit}:NoCopyrightSounds lofi",
        "license": "NoCopyrightSounds License (Credit required)",
    },
    {
        "name": "SoundCloud (Free Lofi)",
        "query": "scsearch{limit}:lofi chill no copyright royalty free",
        "license": "No Copyright / Royalty Free (Credit recommended)",
    },
    {
        "name": "YouTube (NCS)",
        "query": "ytsearch{limit}:NCS lofi chill no copyright",
        "license": "NoCopyrightSounds License (Credit required)",
    },
    {
        "name": "YouTube (Free Lofi)",
        "query": "ytsearch{limit}:lofi hip hop no copyright free to use",
        "license": "No Copyright / Free To Use (Credit recommended)",
    },
]

# --- Provider ảnh (Bước 2) ---
POLLINATIONS_BASE_URL = "https://image.pollinations.ai/prompt"
POLLINATIONS_API_KEY = os.getenv("POLLINATIONS_API_KEY", "")  # để trống vẫn dùng được mức free

SD_LOCAL_API_URL = os.getenv("SD_LOCAL_API_URL", "http://127.0.0.1:7860")  # Automatic1111 mặc định
SD_LOCAL_CHECKPOINT = os.getenv("SD_LOCAL_CHECKPOINT", "sd_v1.5_anime.safetensors")

# Thứ tự ưu tiên provider ảnh, phân tách bằng dấu phẩy.
# Các provider khả dụng: pollinations, aihorde, huggingface, cloudflare, sdlocal
# Máy không có GPU: "pollinations,aihorde,sdlocal" (mặc định).
# Máy có GPU chạy SD local: đặt IMAGE_PROVIDER_ORDER=sdlocal,pollinations,aihorde trong .env
IMAGE_PROVIDER_ORDER = [
    p.strip().lower()
    for p in os.getenv("IMAGE_PROVIDER_ORDER", "pollinations,aihorde,sdlocal").split(",")
    if p.strip()
]

# AI Horde: key ẩn danh "0000000000" dùng được nhưng xếp hàng lâu;
# đăng ký key miễn phí tại stablehorde.net để được ưu tiên
AI_HORDE_API_KEY = os.getenv("AI_HORDE_API_KEY", "0000000000")
PIXABAY_API_KEY = os.getenv("PIXABAY_API_KEY", "").strip()

# Hugging Face Inference (cần token miễn phí từ huggingface.co/settings/tokens)
HUGGINGFACE_TOKEN = os.getenv("HUGGINGFACE_TOKEN", "")
HUGGINGFACE_MODEL = os.getenv("HUGGINGFACE_MODEL", "stabilityai/stable-diffusion-xl-base-1.0")

# LLM cho mọi tác vụ văn bản (prompt ảnh, hiệu ứng, chữ, caption, dịch) — OpenAI-compatible.
# Mặc định trỏ Gemini (endpoint OpenAI-compat) để KHỚP với UI review_app.
# Cần GEMINI/PROMPT_API_KEY để dùng Gemini; nếu THIẾU KEY, hàm gọi LLM tự bỏ qua Gemini và
# rơi xuống provider dự phòng (Pollinations miễn phí) — xem utils/helpers.call_llm_chat.
PROMPT_API_URL = os.getenv(
    "PROMPT_API_URL", "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
)
PROMPT_API_KEY = os.getenv("PROMPT_API_KEY", os.getenv("GEMINI_API_KEY", ""))
# Mặc định gemini-3.1-flash-lite: quota ngày cao nhất trong nhóm Flash free-tier
# (~15 RPM, ~500 RPD) -> hợp automation gọi LLM nhiều lần/video. Đổi sang gemini-3.5-flash
# / gemini-3-flash / gemini-2.5-flash (chất lượng cao hơn nhưng chỉ ~20 RPD) nếu cần.
PROMPT_API_MODEL = os.getenv("PROMPT_API_MODEL", "gemini-3.1-flash-lite")
PROMPT_API_TIMEOUT = max(10, int(os.getenv("PROMPT_API_TIMEOUT", "40")))

# Fallback: chuyển model rồi chuyển provider khi provider chính lỗi/hết quota/thiếu key.
LLM_FALLBACK_ENABLED = os.getenv("LLM_FALLBACK_ENABLED", "1").strip().lower() not in ("0", "false", "no")
# Model Gemini dự phòng (cùng URL/KEY, chỉ đổi model). gemini-2.5-flash chất lượng ổn,
# đã verify chạy thực tế. Để trống nếu không dùng.
PROMPT_API_FALLBACK_MODEL = os.getenv("PROMPT_API_FALLBACK_MODEL", "gemini-2.5-flash")
# Provider dự phòng khác (Pollinations miễn phí, không cần key).
PROMPT_API_FALLBACK_URL = os.getenv("PROMPT_API_FALLBACK_URL", "https://text.pollinations.ai/openai")
PROMPT_API_FALLBACK_KEY = os.getenv("PROMPT_API_FALLBACK_KEY", "")
PROMPT_API_FALLBACK_URL_MODEL = os.getenv("PROMPT_API_FALLBACK_URL_MODEL", "openai")
CAPTION_CHANNEL_PROFILE = os.getenv(
    "CAPTION_CHANNEL_PROFILE",
    "Warm, calm lofi channel. Helpful and natural, never clickbait or keyword-stuffed.",
)
PROMPT_RETRY_DELAY_SECONDS = max(0.2, min(float(os.getenv("PROMPT_RETRY_DELAY_SECONDS", "1.5")), 10.0))
PROMPT_MAX_SIMILARITY = max(0.40, min(float(os.getenv("PROMPT_MAX_SIMILARITY", "0.68")), 0.95))
PROMPT_MIN_QUALITY_SCORE = max(60, min(int(os.getenv("PROMPT_MIN_QUALITY_SCORE", "88")), 100))
PROMPT_PROFILE_DEFAULT = os.getenv("PROMPT_PROFILE_DEFAULT", "auto").strip().lower()
PROMPT_CACHE_ENABLED = os.getenv("PROMPT_CACHE_ENABLED", "1").strip().lower() not in ("0", "false", "no")
PROMPT_CACHE_MAX_ITEMS = max(8, min(int(os.getenv("PROMPT_CACHE_MAX_ITEMS", "128")), 1000))
PROMPT_DISK_CACHE_ENABLED = os.getenv("PROMPT_DISK_CACHE_ENABLED", "1").strip().lower() not in ("0", "false", "no")
PROMPT_CACHE_FILE = BASE_DIR / "data" / "cache" / "prompt_cache.json"

# "random": moi anh dung nhan vat moi. "brand": giu nhan vat thuong hieu.
IMAGE_CHARACTER_MODE = os.getenv("IMAGE_CHARACTER_MODE", "random").strip().lower()
IMAGE_BRAND_CHARACTER = os.getenv(
    "IMAGE_BRAND_CHARACTER",
    "adult fictional anime character with short dark-blue hair, amber eyes, small blue headphones and a navy outer layer",
).strip()

# Cloudflare Workers AI (free tier ~10k neurons/ngày, cần account ID + API token)
CLOUDFLARE_ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID", "")
CLOUDFLARE_API_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN", "")
CLOUDFLARE_IMAGE_MODEL = os.getenv("CLOUDFLARE_IMAGE_MODEL", "@cf/stabilityai/stable-diffusion-xl-base-1.0")

# Ảnh ngang chuẩn video. Pollinations dùng 1280x720, SD local dùng 1024x576 để nhẹ máy hơn.
IMAGE_WIDTH = int(os.getenv("IMAGE_WIDTH", "1280"))
IMAGE_HEIGHT = int(os.getenv("IMAGE_HEIGHT", "720"))
SD_LOCAL_WIDTH = int(os.getenv("SD_LOCAL_WIDTH", "1024"))
SD_LOCAL_HEIGHT = int(os.getenv("SD_LOCAL_HEIGHT", "576"))
SD_LOCAL_STEPS = int(os.getenv("SD_LOCAL_STEPS", "28"))
# CFG 7: bám prompt chặt hơn để bối cảnh ra đúng mô tả (ưu tiên đúng nội dung)
SD_LOCAL_CFG_SCALE = float(os.getenv("SD_LOCAL_CFG_SCALE", "7"))
# DPM++ 2M Karras cho chi tiết ổn định ở step thấp, phù hợp ảnh nền anime
SD_LOCAL_SAMPLER = os.getenv("SD_LOCAL_SAMPLER", "DPM++ 2M Karras")

# Style nền chung: nhấn mạnh bố cục có chiều sâu rõ ràng (3 lớp) để bước tách lớp
# Parallax 2.5D (ParallaxProcessor) cho kết quả chuyển động tự nhiên hơn.
IMAGE_BASE_STYLE = (
    "premium 2D anime illustration, cel-shaded anime character, hand-painted anime background, "
    "cinematic wide shot, 16:9 widescreen composition, layered foreground midground and background, "
    "strong sense of depth, soft cinematic lighting, detailed environment, natural anatomy, simple readable hands, "
    "single character, restrained palette, high quality, no text, no logo, no watermark, no photorealism, no 3d render"
)

IMAGE_NEGATIVE_PROMPT = (
    "text, words, letters, logo, watermark, signature, blurry, low quality, low resolution, "
    "bad anatomy, deformed face, extra fingers, missing fingers, fused fingers, distorted hands, duplicate body, multiple people, crowd, "
    "ugly, noisy, jpeg artifacts, cropped, frame, border, flat composition, fisheye, tilted horizon, "
    # Chặn ảnh tả thực - chỉ nhận tranh vẽ/anime
    "photorealistic, photo, realistic photograph, real photo, 3d render, cgi, live action, stock photo"
)

# Mỗi prompt mô tả tường minh 3 tầng cảnh (vật gần camera / chủ thể giữa / nền xa)
# để ảnh sinh ra có các mặt phẳng độ sâu tách bạch, phục vụ hiệu ứng Parallax.
IMAGE_PROMPTS = [
    "cozy anime bedroom, potted plant and curtain in near foreground, warm desk lamp and soft blanket in midground, rainy window with calm night city lights far outside, chill study vibe",
    "peaceful countryside anime landscape, wildflowers and grass blades in near foreground, small wooden house in midground, sunset sky with purple and orange clouds and distant mountains in far background, quiet warm mood",
    "messy study desk seen from inside room, coffee cup and sleeping cat in near foreground, open laptop and notebooks in midground, rainy night city bokeh far outside the window, warm cozy room",
    "retro lofi coffee shop interior, table with vintage record player in near foreground, cozy seats and warm counter lights in midground, rainy street with soft neon glow far outside the window, nostalgic warm lighting",
    "small attic studio, headphones and notebook on desk in near foreground, plants near window in midground, moonlit rooftops and soft rain in far background, relaxing creative vibe",
]

# --- AI phân tích và tách lớp cảnh ---

# SegFormer ADE20K dùng cho scene_layer_processor.py.
# Model được tải và cache tự động ở lần chạy đầu tiên.
SCENE_SEGMENTATION_MODEL = os.getenv(
    "SCENE_SEGMENTATION_MODEL",
    "nvidia/segformer-b2-finetuned-ade-512-512",
)

# Loại các vùng mask quá nhỏ theo tỷ lệ diện tích ảnh.
SCENE_MASK_MIN_COMPONENT_RATIO = float(
    os.getenv("SCENE_MASK_MIN_COMPONENT_RATIO", "0.00035")
)

# Làm mềm mép mask để lớp lá không bị răng cưa khi chuyển động.
SCENE_MASK_FEATHER_RADIUS = int(
    os.getenv("SCENE_MASK_FEATHER_RADIUS", "2")
)

# Vùng dự kiến cần fill quanh lá khi lá lay.
SCENE_FILL_EXPAND_PX = int(
    os.getenv("SCENE_FILL_EXPAND_PX", "16")
)

# Thông số chuyển động mặc định cho giai đoạn animation tiếp theo.
SCENE_LEAVES_NEAR_AMPLITUDE_PX = int(
    os.getenv("SCENE_LEAVES_NEAR_AMPLITUDE_PX", "7")
)
SCENE_LEAVES_MID_AMPLITUDE_PX = int(
    os.getenv("SCENE_LEAVES_MID_AMPLITUDE_PX", "4")
)
SCENE_CLOUD_SPEED_PX_PER_MINUTE = int(
    os.getenv("SCENE_CLOUD_SPEED_PX_PER_MINUTE", "12")
)

# --- FFmpeg / Render (Bước 4) ---
VIDEO_DURATION_SECONDS = 3600         # 1 tiếng
AUDIO_TEMPO_RATE = 0.88               # 0.85x - 0.9x
VIDEO_FPS = 24
VIDEO_BITRATE = "2800k"
AUDIO_BITRATE = "320k"
AUDIO_SAMPLE_RATE = 48000
NVENC_CODEC = os.getenv("NVENC_CODEC", "h264_nvenc")  # NVIDIA GPU; có thể đổi về libx264 trong .env nếu cần

# --- YouTube upload (Bước 5) ---
ENABLE_YOUTUBE_UPLOAD = False         # Tạm thời tắt để tập trung phát triển và render local
YOUTUBE_CLIENT_SECRETS_FILE = BASE_DIR / "secrets" / "client_secret.json"
YOUTUBE_TOKEN_FILE = BASE_DIR / "secrets" / "token.json"
YOUTUBE_ANALYTICS_TOKEN_FILE = BASE_DIR / "secrets" / "youtube_analytics_token.json"
YOUTUBE_ANALYTICS_SCOPES = [
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
    "https://www.googleapis.com/auth/yt-analytics-monetary.readonly",
]
UPLOAD_PRIVACY_INITIAL = "private"
DAILY_VIDEO_TARGET = 5                # giữ dưới 6 để an toàn quota
SCHEDULE_HOURS = [7, 12, 15, 19, 21]   # khung giờ traffic cao, rải lịch đăng

# --- Streamlit review app ---
REVIEW_APP_PORT = 8501

# --- Thư viện hiệu ứng online (Pixabay Video API) ---
PIXABAY_API_KEY = os.getenv("PIXABAY_API_KEY", "").strip()
EFFECT_SEARCH_CACHE_TTL = max(3600, int(os.getenv("EFFECT_SEARCH_CACHE_TTL", "86400")))
EFFECT_MAX_DOWNLOAD_MB = max(5, min(int(os.getenv("EFFECT_MAX_DOWNLOAD_MB", "30")), 200))
EFFECT_API_TIMEOUT = max(5, min(int(os.getenv("EFFECT_API_TIMEOUT", "20")), 120))

# --- AI đề xuất hiệu ứng ---
EFFECT_AI_ENABLED = os.getenv("EFFECT_AI_ENABLED", "1").strip().lower() not in ("0", "false", "no")
EFFECT_AI_MAX_QUERIES = max(1, min(int(os.getenv("EFFECT_AI_MAX_QUERIES", "3")), 3))
EFFECT_AI_MAX_RESULTS = max(3, min(int(os.getenv("EFFECT_AI_MAX_RESULTS", "6")), 12))

# --- Chữ động (text overlay) ---
# Dùng chung endpoint PROMPT_API_* cho gợi ý chữ.
TEXT_EFFECT_AI_ENABLED = os.getenv("TEXT_EFFECT_AI_ENABLED", "1").strip().lower() not in ("0", "false", "no")
FONTS_DIR = BASE_DIR / "data" / "fonts"   # font bundle tùy chọn; thiếu thì dùng font hệ thống
FONTS_DIR.mkdir(parents=True, exist_ok=True)
