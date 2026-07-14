"""
Cấu hình chung cho toàn bộ pipeline.
Đọc từ biến môi trường (.env) để không lộ key khi đẩy code lên git.
"""
import os
from pathlib import Path

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
MUSIC_SOURCES = {
    "ncs": "https://ncs.io/music",       # No Copyright Sounds - danh mục chính thức
    "soundcloud_tag": "CC-BY",
}

# --- Provider ảnh (Bước 2) ---
POLLINATIONS_BASE_URL = "https://image.pollinations.ai/prompt"
POLLINATIONS_API_KEY = os.getenv("POLLINATIONS_API_KEY", "")  # để trống vẫn dùng được mức free

SD_LOCAL_API_URL = os.getenv("SD_LOCAL_API_URL", "http://127.0.0.1:7860")  # Automatic1111 mặc định
SD_LOCAL_CHECKPOINT = os.getenv("SD_LOCAL_CHECKPOINT", "sd_v1.5_anime.safetensors")

# Ảnh ngang chuẩn video. Pollinations dùng 1280x720, SD local dùng 1024x576 để nhẹ máy hơn.
IMAGE_WIDTH = int(os.getenv("IMAGE_WIDTH", "1280"))
IMAGE_HEIGHT = int(os.getenv("IMAGE_HEIGHT", "720"))
SD_LOCAL_WIDTH = int(os.getenv("SD_LOCAL_WIDTH", "1024"))
SD_LOCAL_HEIGHT = int(os.getenv("SD_LOCAL_HEIGHT", "576"))
SD_LOCAL_STEPS = int(os.getenv("SD_LOCAL_STEPS", "24"))
SD_LOCAL_CFG_SCALE = float(os.getenv("SD_LOCAL_CFG_SCALE", "7"))

IMAGE_BASE_STYLE = (
    "lofi anime background, cinematic wide shot, 16:9 widescreen composition, "
    "cozy atmosphere, soft cinematic lighting, detailed environment, clean background, "
    "high quality, no text, no logo, no watermark"
)

IMAGE_NEGATIVE_PROMPT = (
    "text, words, letters, logo, watermark, signature, blurry, low quality, low resolution, "
    "bad anatomy, deformed face, extra fingers, missing fingers, distorted hands, duplicate body, "
    "ugly, noisy, jpeg artifacts, cropped, frame, border"
)

IMAGE_PROMPTS = [
    "cozy anime bedroom, rainy window, warm desk lamp, soft blanket, calm night city lights outside, chill study vibe",
    "peaceful countryside anime landscape, sunset sky, purple and orange clouds, grass moving in the wind, quiet warm mood",
    "messy study desk, open laptop, hot coffee steam, sleeping cat, rainy night city bokeh outside, warm cozy room",
    "retro lofi coffee shop interior, vintage record player, soft neon glow, rainy street outside, nostalgic warm lighting",
    "small attic studio, headphones on desk, notebook, plants near window, moonlight and soft rain, relaxing creative vibe",
]

# --- FFmpeg / Render (Bước 4) ---
VIDEO_DURATION_SECONDS = 3600         # 1 tiếng
AUDIO_TEMPO_RATE = 0.88               # 0.85x - 0.9x
VIDEO_FPS = 24
VIDEO_BITRATE = "2800k"
AUDIO_BITRATE = "320k"
AUDIO_SAMPLE_RATE = 48000
NVENC_CODEC = "libx264"            # Dùng libx264 để chạy CPU cho ổn định (không lo thiếu VRAM)

# --- YouTube upload (Bước 5) ---
ENABLE_YOUTUBE_UPLOAD = False         # Tạm thời tắt để tập trung phát triển và render local
YOUTUBE_CLIENT_SECRETS_FILE = BASE_DIR / "secrets" / "client_secret.json"
YOUTUBE_TOKEN_FILE = BASE_DIR / "secrets" / "token.json"
UPLOAD_PRIVACY_INITIAL = "private"
DAILY_VIDEO_TARGET = 5                # giữ dưới 6 để an toàn quota
SCHEDULE_HOURS = [7, 12, 15, 19, 21]   # khung giờ traffic cao, rải lịch đăng

# --- Streamlit review app (Bước 3) ---
REVIEW_APP_PORT = 8501
