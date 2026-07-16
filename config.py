"""
Cấu hình chung cho toàn bộ pipeline.
Đọc từ biến môi trường (.env) để không lộ key khi đẩy code lên git.
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
TEMP_IMAGE_DIR = BASE_DIR / "data" / "temp_image"
EFFECTS_DIR = BASE_DIR / "data" / "effects"       # video mưa, bụi, đĩa than (asset tĩnh)
OUTPUT_DIR = BASE_DIR / "data" / "output_final"
METADATA_DIR = BASE_DIR / "data" / "metadata"      # log license nhạc, ảnh

for d in [INPUT_AUDIO_DIR, TEMP_IMAGE_DIR, EFFECTS_DIR, OUTPUT_DIR, METADATA_DIR]:
    d.mkdir(parents=True, exist_ok=True)

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

# Hugging Face Inference (cần token miễn phí từ huggingface.co/settings/tokens)
HUGGINGFACE_TOKEN = os.getenv("HUGGINGFACE_TOKEN", "")
HUGGINGFACE_MODEL = os.getenv("HUGGINGFACE_MODEL", "stabilityai/stable-diffusion-xl-base-1.0")

# LLM viết prompt ảnh theo bài nhạc (OpenAI-compatible chat completions).
# Mặc định: text.pollinations.ai miễn phí không cần key.
# Có key riêng (Groq/OpenRouter/Gemini...) thì đổi URL + KEY + MODEL trong .env.
PROMPT_API_URL = os.getenv("PROMPT_API_URL", "https://text.pollinations.ai/openai")
PROMPT_API_KEY = os.getenv("PROMPT_API_KEY", "")
PROMPT_API_MODEL = os.getenv("PROMPT_API_MODEL", "openai")

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
    "lofi anime background, cinematic wide shot, 16:9 widescreen composition, "
    "layered composition with clear foreground midground and background, strong sense of depth, "
    "cozy atmosphere, soft cinematic lighting, detailed environment, clean background, "
    "high quality, no text, no logo, no watermark"
)

IMAGE_NEGATIVE_PROMPT = (
    "text, words, letters, logo, watermark, signature, blurry, low quality, low resolution, "
    "bad anatomy, deformed face, extra fingers, missing fingers, distorted hands, duplicate body, "
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
UPLOAD_PRIVACY_INITIAL = "private"
DAILY_VIDEO_TARGET = 5                # giữ dưới 6 để an toàn quota
SCHEDULE_HOURS = [7, 12, 15, 19, 21]   # khung giờ traffic cao, rải lịch đăng

# --- Streamlit review app (Bước 3) ---
REVIEW_APP_PORT = 8501
