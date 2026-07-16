import os
import sys
import json
import logging
import threading
from pathlib import Path
from typing import Optional, List
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import config
import step1_music_hunter
import step2_image_provider
import step4_render
import system_check
from core.image.sd_manager import SDProcessManager
from core.image.upscaler import ImageUpscaler


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("lofi_automation")

app = FastAPI(title="Lo-Fi Studio Automation API", version="4.9")

# Kích hoạt CORS cho React Frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Trạng thái render chạy nền
render_state = {
    "status": "idle",       # "idle" | "rendering" | "success" | "failed"
    "progress": 0.0,
    "error": None,
    "output_video": None
}

class SDControlPayload(BaseModel):
    action: str            # "start" | "stop"
    sd_mode: str          # "existing" | "app_managed"
    install_dir: str

class MusicSearchPayload(BaseModel):
    query: str

class MusicDownloadPayload(BaseModel):
    track: dict

class StreamPayload(BaseModel):
    url: str

class ImageGeneratePayload(BaseModel):
    prompt: str
    provider: str
    sd_api_url: Optional[str] = "http://127.0.0.1:7860"

class RenderPayload(BaseModel):
    project_id: str
    track: dict
    image_path: str
    effect_path: str
    duration_seconds: float
    vibe_mode: str
    parallax_enabled: bool

class SettingsPayload(BaseModel):
    sd_dir: Optional[str] = ""
    sd_url: Optional[str] = "http://127.0.0.1:7860"
    provider: Optional[str] = "Pollinations AI (Online, Miễn phí)"
    vibe_mode: Optional[str] = "clean"
    duration: Optional[int] = 10

SETTINGS_FILE = config.BASE_DIR / "data" / "settings.json"

def _load_settings() -> dict:
    """Đọc settings từ file JSON, trả về dict mặc định nếu chưa có."""
    defaults = {
        "sd_dir": "C:/stable-diffusion-webui",
        "sd_url": "http://127.0.0.1:7860",
        "provider": "Pollinations AI (Online, Miễn phí)",
        "vibe_mode": "clean",
        "duration": 10,
    }
    try:
        if SETTINGS_FILE.exists():
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
            defaults.update(saved)
    except Exception:
        pass
    return defaults

def _save_settings(data: dict):
    """Ghi settings xuống file JSON."""
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

@app.get("/api/settings")
def api_get_settings():
    """Trả về cài đặt người dùng đã lưu."""
    return _load_settings()

@app.post("/api/settings")
def api_save_settings(payload: SettingsPayload):
    """Lưu cài đặt người dùng xuống file."""
    data = payload.dict()
    _save_settings(data)
    return {"ok": True, "saved": data}

@app.get("/api/system/check")
def api_system_check():
    """Kiểm tra cấu hình phần cứng và đưa ra khuyến nghị SD."""
    try:
        res = system_check.run_check(verbose=False)
        return res
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/sd/status")
def api_sd_status(url: str = "http://127.0.0.1:7860"):
    """Kiểm tra kết nối tới Stable Diffusion local, trả về model đang active."""
    import requests
    try:
        r = requests.get(f"{url.rstrip('/')}/sdapi/v1/options", timeout=3)
        if r.status_code == 200:
            model = r.json().get("sd_model_checkpoint", "Unknown Model")
            return {"online": True, "model": model}
    except Exception:
        pass
    return {"online": False, "model": None}

@app.get("/api/sd/models")
def api_sd_models(url: str = "http://127.0.0.1:7860"):
    """Lấy danh sách tất cả checkpoint hiện có trong SD."""
    import requests
    try:
        r = requests.get(f"{url.rstrip('/')}/sdapi/v1/sd-models", timeout=5)
        if r.status_code == 200:
            models = r.json()
            return {
                "ok": True,
                "models": [
                    {"title": m.get("title", ""), "model_name": m.get("model_name", ""), "filename": m.get("filename", "")}
                    for m in models
                ]
            }
    except Exception as e:
        pass
    return {"ok": False, "models": []}

class SetModelPayload(BaseModel):
    url: Optional[str] = "http://127.0.0.1:7860"
    model_title: str

@app.post("/api/sd/set-model")
def api_sd_set_model(payload: SetModelPayload):
    """Đổi checkpoint đang active của SD (gọi sdapi/v1/options)."""
    import requests
    try:
        r = requests.post(
            f"{payload.url.rstrip('/')}/sdapi/v1/options",
            json={"sd_model_checkpoint": payload.model_title},
            timeout=30
        )
        if r.status_code == 200:
            return {"ok": True, "message": f"Đã chuyển sang model: {payload.model_title}"}
        return {"ok": False, "message": f"SD trả về HTTP {r.status_code}"}
    except Exception as e:
        return {"ok": False, "message": str(e)}

@app.post("/api/sd/control")
def api_sd_control(payload: SDControlPayload):
    """Bật/tắt tiến trình Stable Diffusion."""
    try:
        if payload.action == "start":
            if payload.sd_mode == "existing":
                SDProcessManager.start_existing_process(Path(payload.install_dir))
                return {"message": "Đang khởi chạy AUTOMATIC1111 (console mới)..."}
            else:
                SDProcessManager.start_process(Path(payload.install_dir))
                return {"message": "Đang khởi động AUTOMATIC1111 (chạy nền)..."}
        elif payload.action == "stop":
            killed = SDProcessManager.kill_process_by_port(7860)
            # Thêm đóng theo PID nếu app-managed
            try:
                SDProcessManager.stop_process(Path(payload.install_dir))
            except Exception:
                pass
            return {"message": "Đã gửi tín hiệu tắt tiến trình.", "killed_port_7860": killed}
        else:
            raise HTTPException(status_code=400, detail="Hành động không hợp lệ.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/music/search")
def api_music_search(payload: MusicSearchPayload):
    """Tìm kiếm nhạc từ SoundCloud."""
    try:
        tracks = step1_music_hunter.fetch_candidate_tracks(query=payload.query, limit=10)
        return {"tracks": tracks}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/music/stream_url")
def api_music_stream_url(payload: StreamPayload):
    """Lấy link stream trực tuyến không cần tải trước."""
    import subprocess
    try:
        cmd = [
            "python", "-m", "yt_dlp",
            "-g",
            "-f", "http_mp3_0_1/bestaudio/best",
            payload.url
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
            encoding="utf-8",
            errors="ignore",
            timeout=15
        )
        stream_url = result.stdout.strip().split('\n')[0]
        return {"stream_url": stream_url}
    except Exception as e:
        logger.warning(f"Lỗi phân giải stream URL: {e}")
        return {"stream_url": payload.url}

@app.post("/api/music/download")
def api_music_download(payload: MusicDownloadPayload):
    """Tải bài hát được chọn."""
    try:
        config.INPUT_AUDIO_DIR.mkdir(parents=True, exist_ok=True)
        # Tải nhạc
        res = step1_music_hunter.download_track(payload.track, project_id="api_download")
        return {"audio_path": str(res.as_posix()), "track_id": payload.track.get("track_id")}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/image/generate")
def api_image_generate(payload: ImageGeneratePayload):
    """Tạo ảnh nền bằng AI."""
    try:
        config.TEMP_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
        out_path = config.TEMP_IMAGE_DIR / f"bg_api_{os.urandom(4).hex()}.png"
        
        provider_name = payload.provider
        if "Pollinations" in provider_name:
            provider = step2_image_provider.PollinationsProvider()
        else:
            # Gán URL API
            config.SD_LOCAL_API_URL = payload.sd_api_url
            provider = step2_image_provider.SDLocalProvider()
            
        raw_img = provider.generate(payload.prompt, out_path)
        # Đảm bảo upscale đúng tỉ lệ Full HD 1920x1080
        upscaled_out = config.TEMP_IMAGE_DIR / f"bg_api_up_{os.urandom(4).hex()}.png"
        ImageUpscaler.upscale_image(raw_img, upscaled_out, api_url=payload.sd_api_url if "SD Local" in provider_name else None)
        
        return {"image_path": str(upscaled_out.as_posix())}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/effects/list")
def api_effects_list():
    """Lấy danh sách các hiệu ứng overlay có sẵn."""
    effects = []
    if config.EFFECTS_DIR.exists():
        for f in config.EFFECTS_DIR.glob("*.mp4"):
            effects.append({
                "name": f.name,
                "path": str(f.as_posix())
            })
    return {"effects": effects}

# Luồng render chạy nền độc lập
def bg_render_task(payload: RenderPayload):
    global render_state
    render_state["status"] = "rendering"
    render_state["progress"] = 0.0
    render_state["error"] = None
    render_state["output_video"] = None
    
    def update_progress(pct: float):
        render_state["progress"] = pct
        
    try:
        import core.runtime.db
        core.runtime.db.init_db()
        
        # Cập nhật cấu hình
        config.VIDEO_DURATION_SECONDS = payload.duration_seconds
        
        audio_file = Path(payload.track.get("track_id", ""))
        # Kiểm tra file audio tồn tại
        full_audio_path = config.INPUT_AUDIO_DIR / f"{payload.track['track_id']}.m4a"
        if not full_audio_path.exists():
            step1_music_hunter.download_track(payload.track, project_id=payload.project_id)
            
        out_video = step4_render.run_step4(
            project_id=payload.project_id,
            audio_path=full_audio_path,
            image_path=Path(payload.image_path),
            effect_path=Path(payload.effect_path),
            progress_callback=update_progress,
            vibe_mode=payload.vibe_mode,
            parallax_enabled=payload.parallax_enabled
        )
        
        render_state["status"] = "success"
        render_state["progress"] = 1.0
        render_state["output_video"] = str(out_video.as_posix())
    except Exception as e:
        logger.exception("Render task background failed:")
        render_state["status"] = "failed"
        render_state["error"] = str(e)

@app.post("/api/render")
def api_render_start(payload: RenderPayload, background_tasks: BackgroundTasks):
    """Khởi động tiến trình render dưới nền."""
    global render_state
    if render_state["status"] == "rendering":
        return {"status": "rendering", "message": "Tiến trình render khác đang chạy."}
        
    background_tasks.add_task(bg_render_task, payload)
    return {"status": "started", "message": "Bắt đầu render video dưới nền."}

@app.get("/api/render/status")
def api_render_status():
    """Theo dõi tiến độ render."""
    global render_state
    return render_state

# Phục vụ tệp tĩnh media từ data/ (nhạc, ảnh, video)
app.mount("/static", StaticFiles(directory="data"), name="static")

@app.get("/")
def api_root():
    """Thông tin API — giao diện sử dụng Streamlit tại cổng 8501."""
    return {
        "service": "Lo-Fi Studio Automation API",
        "version": "5.0",
        "streamlit_ui": "http://127.0.0.1:8501",
        "docs": "http://127.0.0.1:8000/docs",
    }

if __name__ == "__main__":
    import uvicorn
    # Reconfigure stdout to print utf-8 safely
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    uvicorn.run(app, host="127.0.0.1", port=8000)
