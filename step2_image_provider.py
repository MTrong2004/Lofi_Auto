"""
Bước 2 - Tạo nguyên liệu visual.
Kiến trúc: interface chung ImageProvider, thử Pollinations trước,
fallback sang SD local nếu lỗi/timeout.
"""
import base64
import logging
import os
import random
import re
import time
import urllib.parse
from abc import ABC, abstractmethod
from pathlib import Path

import requests

import config

logger = logging.getLogger("lofi_automation")


def normalize_image_prompt(prompt: str) -> str:
    """Chuẩn hóa prompt để ảnh ra đúng kiểu video lofi ngang, ít lỗi chữ/logo."""
    prompt = (prompt or "").strip()
    base_style = getattr(config, "IMAGE_BASE_STYLE", "lofi anime background, 16:9, natural proportions, no stretched objects, no text, no logo")
    if not prompt:
        prompt = random.choice(config.IMAGE_PROMPTS)

    final_prompt = f"{prompt}, {base_style}"

    # Tránh lặp cụm quan trọng quá nhiều.
    parts = []
    seen = set()
    for part in final_prompt.split(","):
        clean = part.strip()
        key = clean.lower()
        if clean and key not in seen:
            seen.add(key)
            parts.append(clean)
    return ", ".join(parts)


def validate_image_file(path: Path) -> None:
    """Kiểm tra file lưu ra có thật là ảnh không."""
    if not path.exists() or path.stat().st_size < 1024:
        raise ValueError("File ảnh bị thiếu hoặc quá nhỏ.")

    header = path.read_bytes()[:16]
    is_png = header.startswith(b"\x89PNG\r\n\x1a\n")
    is_jpeg = header.startswith(b"\xff\xd8\xff")
    is_webp = header.startswith(b"RIFF") and b"WEBP" in header

    if not (is_png or is_jpeg or is_webp):
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass
        raise ValueError("API không trả về file ảnh hợp lệ.")


class ImageProvider(ABC):
    @abstractmethod
    def generate(self, prompt: str, out_path: Path) -> Path:
        ...


class PollinationsProvider(ImageProvider):
    """Nguồn ảnh chính - miễn phí, không cần key ở mức cơ bản."""

    def generate(self, prompt: str, out_path: Path) -> Path:
        final_prompt = normalize_image_prompt(prompt)
        encoded_prompt = urllib.parse.quote(final_prompt)
        url = f"{config.POLLINATIONS_BASE_URL}/{encoded_prompt}"
        params = {
            "width": getattr(config, "IMAGE_WIDTH", 1280),
            "height": getattr(config, "IMAGE_HEIGHT", 720),
            "nologo": "true",
            "enhance": "true",
            "seed": random.randint(100000, 999999),
        }
        if config.POLLINATIONS_API_KEY:
            params["key"] = config.POLLINATIONS_API_KEY

        response = requests.get(url, params=params, timeout=60)
        response.raise_for_status()

        content_type = response.headers.get("content-type", "").lower()
        if "image" not in content_type:
            raise ValueError(f"Pollinations không trả về ảnh hợp lệ: {content_type}")

        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(response.content)
        validate_image_file(out_path)
        logger.info(f"[Pollinations] Đã tạo ảnh: {out_path.name}")
        return out_path


class AIHordeProvider(ImageProvider):
    """Nguồn ảnh miễn phí cộng đồng AI Horde / Stable Horde."""

    def __init__(self, api_key: str = "0000000000"):
        self.api_key = (api_key or "0000000000").strip()

    def generate(self, prompt: str, out_path: Path) -> Path:
        final_prompt = normalize_image_prompt(prompt)
        headers = {
            "apikey": self.api_key,
            "Client-Agent": "lofi-automation:1.0:local-app",
        }
        payload = {
            "prompt": final_prompt,
            "params": {
                "width": 1024,
                "height": 576,
                "steps": 24,
                "n": 1,
                "cfg_scale": 7,
                "sampler_name": "k_euler_a",
            },
            "nsfw": False,
            "censor_nsfw": True,
            "trusted_workers": False,
            "r2": True,
        }

        submit = requests.post(
            "https://stablehorde.net/api/v2/generate/async",
            json=payload,
            headers=headers,
            timeout=30,
        )
        submit.raise_for_status()
        job_id = submit.json().get("id")
        if not job_id:
            raise ValueError("AI Horde không trả về job id.")

        for _ in range(90):
            check = requests.get(
                f"https://stablehorde.net/api/v2/generate/check/{job_id}",
                headers=headers,
                timeout=20,
            )
            check.raise_for_status()
            check_data = check.json()
            if check_data.get("done"):
                break
            time.sleep(4)
        else:
            raise TimeoutError("AI Horde chờ quá lâu, hãy thử lại sau.")

        status = requests.get(
            f"https://stablehorde.net/api/v2/generate/status/{job_id}",
            headers=headers,
            timeout=30,
        )
        status.raise_for_status()
        generations = status.json().get("generations") or []
        if not generations:
            raise ValueError("AI Horde không trả về ảnh.")

        img_value = generations[0].get("img")
        if not img_value:
            raise ValueError("AI Horde trả về kết quả thiếu ảnh.")

        out_path.parent.mkdir(parents=True, exist_ok=True)
        if img_value.startswith("http"):
            img_response = requests.get(img_value, timeout=120)
            img_response.raise_for_status()
            out_path.write_bytes(img_response.content)
        else:
            out_path.write_bytes(base64.b64decode(img_value))

        validate_image_file(out_path)
        logger.info(f"[AIHorde] Đã tạo ảnh: {out_path.name}")
        return out_path


class HuggingFaceProvider(ImageProvider):
    """Nguồn ảnh Hugging Face Inference API / Inference Providers."""

    def __init__(self, token: str, model_id: str = "stabilityai/stable-diffusion-xl-base-1.0"):
        self.token = (token or "").strip()
        self.model_id = (model_id or "stabilityai/stable-diffusion-xl-base-1.0").strip()

    def generate(self, prompt: str, out_path: Path) -> Path:
        if not self.token:
            raise ValueError("Chưa nhập Hugging Face token.")

        final_prompt = normalize_image_prompt(prompt)
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        payload = {
            "inputs": final_prompt,
            "parameters": {
                "width": 1024,
                "height": 576,
                "num_inference_steps": 24,
                "guidance_scale": 7,
            },
        }
        url = f"https://api-inference.huggingface.co/models/{self.model_id}"

        for _ in range(3):
            response = requests.post(url, headers=headers, json=payload, timeout=180)
            content_type = response.headers.get("content-type", "").lower()
            if response.status_code == 503:
                time.sleep(12)
                continue
            response.raise_for_status()
            if "image" in content_type:
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_bytes(response.content)
                validate_image_file(out_path)
                logger.info(f"[HuggingFace] Đã tạo ảnh: {out_path.name}")
                return out_path
            raise ValueError(f"Hugging Face không trả về ảnh hợp lệ: {response.text[:300]}")

        raise TimeoutError("Hugging Face model đang tải quá lâu, hãy thử lại sau.")


class SDLocalProvider(ImageProvider):
    """Dự phòng - chạy Stable Diffusion 1.5 qua API Automatic1111/ComfyUI local."""

    def __init__(self):
        self._checked = False

    def _check_hardware_once(self):
        """Chỉ kiểm tra 1 lần/phiên chạy, cảnh báo nếu máy không đủ điều kiện."""
        if self._checked:
            return
        self._checked = True
        try:
            import system_check
            result = system_check.run_check(verbose=False)
            rec = result["recommendation"]
            if not rec["can_run_sd_local"]:
                logger.warning(f"[SDLocal] Cấu hình máy không phù hợp: {rec['reason']}")
            elif rec.get("ram_warning"):
                logger.warning(f"[SDLocal] {rec['ram_warning']}")
        except Exception as e:
            logger.warning(f"[SDLocal] Không kiểm tra được cấu hình máy: {e}")

    def generate(self, prompt: str, out_path: Path) -> Path:
        self._check_hardware_once()
        final_prompt = normalize_image_prompt(prompt)
        payload = {
            "prompt": final_prompt,
            "negative_prompt": getattr(config, "IMAGE_NEGATIVE_PROMPT", "text, watermark, logo, blurry, low quality"),
            "steps": getattr(config, "SD_LOCAL_STEPS", 24),
            "width": getattr(config, "SD_LOCAL_WIDTH", 1024),
            "height": getattr(config, "SD_LOCAL_HEIGHT", 576),
            "cfg_scale": getattr(config, "SD_LOCAL_CFG_SCALE", 7),
            "seed": random.randint(100000, 999999),
            "override_settings": {"sd_model_checkpoint": config.SD_LOCAL_CHECKPOINT},
        }
        response = requests.post(f"{config.SD_LOCAL_API_URL}/sdapi/v1/txt2img", json=payload, timeout=180)
        response.raise_for_status()

        img_data = response.json()["images"][0]
        if "," in img_data:
            img_data = img_data.split(",", 1)[1]

        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(base64.b64decode(img_data))
        validate_image_file(out_path)
        logger.info(f"[SDLocal] Đã tạo ảnh: {out_path.name}")
        return out_path


def scale_image_ffmpeg(input_path: Path, output_path: Path, width: int = 1920, height: int = 1080):
    """Sử dụng FFmpeg để scale ảnh sang kích thước Full HD với bộ lọc Lanczos."""
    import subprocess
    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_path),
        "-vf", f"scale={width}:{height}:flags=lanczos",
        str(output_path)
    ]
    subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

def get_background_image(index: int = 0, project_id: str = None) -> Path:
    """
    Hàm chính mà step2 expose ra cho main.py.
    Thử Pollinations trước, lỗi thì fallback SD local.
    Đầu ra luôn được upscale lên 1920x1080.
    """
    from datetime import datetime, timezone
    import json
    from core.db import get_db_connection
    from core.schemas import validate_data_schema
    from core.project_manager import ProjectManager
    from core.cache_manager import CacheManager
    
    prompt = config.IMAGE_PROMPTS[index % len(config.IMAGE_PROMPTS)]
    out_path = config.TEMP_IMAGE_DIR / f"bg_raw_{random.randint(1000, 9999)}.png"
    full_hd_path = config.TEMP_IMAGE_DIR / f"bg_full_hd_{random.randint(1000, 9999)}.png"

    providers = [PollinationsProvider(), AIHordeProvider(), SDLocalProvider()]
    last_error = None
    generated_path = None
    used_provider = None
    
    for provider in providers:
        try:
            generated_path = provider.generate(prompt, out_path)
            used_provider = provider.__class__.__name__
            break
        except Exception as e:
            last_error = e
            logger.warning(f"[{provider.__class__.__name__}] lỗi, thử provider kế tiếp: {e}")
            
    if not generated_path:
        raise RuntimeError(f"Tất cả provider ảnh đều lỗi: {last_error}")

    # Upscale lên 1920x1080
    scale_image_ffmpeg(generated_path, full_hd_path, 1920, 1080)
    validate_image_file(full_hd_path)
    
    # Xóa ảnh gốc chưa scale
    try:
        generated_path.unlink(missing_ok=True)
    except Exception:
        pass
        
    file_sha256 = CacheManager.get_file_sha256(full_hd_path)
    file_size = full_hd_path.stat().st_size
    
    # Ghi nhận metadata ảnh theo schema
    image_meta = {
        "schema_name": "image_metadata",
        "schema_version": 1,
        "provider": used_provider,
        "model": getattr(config, "SD_LOCAL_CHECKPOINT", "unknown_model"),
        "prompt": prompt,
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

    # Đăng ký SQLite và cập nhật workflow status
    if project_id:
        conn = get_db_connection()
        try:
            now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            asset_id = f"image_{file_sha256[:12]}"
            with conn:
                conn.execute("""
                INSERT OR REPLACE INTO assets (asset_id, project_id, path, sha256, mime_type, size_bytes, processing_status, review_status, created_at_utc)
                VALUES (?, ?, ?, ?, 'image/png', ?, 'verified', 'approved', ?);
                """, (asset_id, project_id, f"data/temp_image/{full_hd_path.name}", file_sha256, file_size, now_str))
                
            ProjectManager.update_workflow_status(
                project_id=project_id,
                module_name="image",
                processing_status="verified",
                review_status="approved",
                input_hash=file_sha256,
                output_hash=file_sha256,
                reason=f"Image generated via {used_provider} and scaled to Full HD 1920x1080.",
                actor="image_provider"
            )
        finally:
            conn.close()
            
    return full_hd_path


def _safe_slug(text: str, max_len: int = 60) -> str:
    """Tạo tên file an toàn từ từ khóa tìm kiếm."""
    text = (text or "effect").lower().strip()
    text = re.sub(r"[^a-z0-9_-]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return (text or "effect")[:max_len]


def list_effect_videos() -> list[Path]:
    """Lấy danh sách file hiệu ứng trong data/effects."""
    config.EFFECTS_DIR.mkdir(parents=True, exist_ok=True)
    return sorted(config.EFFECTS_DIR.glob("*.mp4"))


def download_pexels_effect(query: str, api_key: str = "", max_results: int = 8) -> Path:
    """
    Tải 1 video hiệu ứng từ Pexels về data/effects.
    Cần Pexels API key. Có thể nhập trong UI hoặc đặt biến môi trường PEXELS_API_KEY.
    """
    api_key = (api_key or os.getenv("PEXELS_API_KEY", "")).strip()
    query = (query or "rain overlay").strip()
    if not api_key:
        raise ValueError("Chưa có Pexels API key. Hãy nhập API key Pexels trong giao diện hoặc đặt biến môi trường PEXELS_API_KEY.")

    headers = {"Authorization": api_key}
    params = {
        "query": query,
        "orientation": "landscape",
        "per_page": max(1, min(int(max_results), 20)),
    }
    response = requests.get("https://api.pexels.com/videos/search", headers=headers, params=params, timeout=30)
    response.raise_for_status()
    data = response.json()
    videos = data.get("videos") or []
    if not videos:
        raise ValueError(f"Không tìm thấy hiệu ứng online cho từ khóa: {query}")

    best_video_file = None
    best_video = None
    for video in videos:
        video_files = video.get("video_files") or []
        candidates = [
            f for f in video_files
            if f.get("file_type") == "video/mp4" and f.get("link")
        ]
        if not candidates:
            continue
        candidates.sort(key=lambda f: (
            abs((f.get("height") or 720) - 720),
            abs((f.get("width") or 1280) - 1280),
        ))
        best_video_file = candidates[0]
        best_video = video
        break

    if not best_video_file:
        raise ValueError("Pexels có kết quả nhưng không có file mp4 phù hợp.")

    slug = _safe_slug(query)
    video_id = best_video.get("id", random.randint(1000, 9999)) if best_video else random.randint(1000, 9999)
    out_path = config.EFFECTS_DIR / f"pexels_{slug}_{video_id}.mp4"

    download = requests.get(best_video_file["link"], stream=True, timeout=120)
    download.raise_for_status()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("wb") as f:
        for chunk in download.iter_content(chunk_size=1024 * 512):
            if chunk:
                f.write(chunk)

    if not out_path.exists() or out_path.stat().st_size < 1024:
        raise ValueError("File hiệu ứng tải về bị lỗi hoặc quá nhỏ.")

    credit_path = config.EFFECTS_DIR / "online_effects_credits.txt"
    photographer = (best_video or {}).get("user", {}).get("name", "Pexels")
    source_url = (best_video or {}).get("url", "https://www.pexels.com")
    with credit_path.open("a", encoding="utf-8") as f:
        f.write(f"{out_path.name} | Video by {photographer} on Pexels | {source_url}\n")

    logger.info(f"[Pexels] Đã tải hiệu ứng online: {out_path.name}")
    return out_path


def create_builtin_effect_pack() -> list[Path]:
    """Tạo vài hiệu ứng mẫu local khi chưa có hiệu ứng đẹp."""
    import subprocess
    config.EFFECTS_DIR.mkdir(parents=True, exist_ok=True)
    effect_specs = {
        "effect_snow_noise.mp4": "nullsrc=s=1280x720:d=8:r=24,geq=lum='if(gt(random(1),0.985),255,0)':cb=128:cr=128,format=yuv420p",
        "effect_dust_soft.mp4": "nullsrc=s=1280x720:d=8:r=24,geq=lum='if(gt(random(1),0.995),180,0)':cb=128:cr=128,boxblur=2:1,format=yuv420p",
        "effect_retro_scanline.mp4": "nullsrc=s=1280x720:d=8:r=24,geq=lum='if(eq(mod(Y,6),0),70,0)':cb=128:cr=128,format=yuv420p",
        "effect_light_film_grain.mp4": "nullsrc=s=1280x720:d=8:r=24,geq=lum='random(1)*55':cb=128:cr=128,format=yuv420p",
    }
    created = []
    for file_name, lavfi in effect_specs.items():
        out_path = config.EFFECTS_DIR / file_name
        if out_path.exists() and out_path.stat().st_size > 1024:
            created.append(out_path)
            continue
        cmd = [
            "ffmpeg", "-y", "-f", "lavfi", "-i", lavfi,
            "-t", "8", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(out_path),
        ]
        subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        created.append(out_path)
    return created


def pick_effect_video() -> Path:
    """Chọn ngẫu nhiên 1 video hiệu ứng (mưa/bụi/đĩa than) từ thư mục asset tĩnh."""
    effects = list_effect_videos()
    if not effects:
        default_path = config.EFFECTS_DIR / "default_effect.mp4"
        import subprocess
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "color=c=black:s=1280x720:d=5",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            str(default_path)
        ]
        logger.info(f"Tạo file hiệu ứng mặc định: {' '.join(cmd)}")
        subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        effects = [default_path]
    return random.choice(effects)


if __name__ == "__main__":
    p_id = "test_step2_prj"
    
    # Khởi tạo database
    import core.db
    from core.db import get_db_connection
    core.db.init_db()
    
    # Dọn dẹp trước khi test
    conn = get_db_connection()
    with conn:
        conn.execute("DELETE FROM projects WHERE project_id = ?;", (p_id,))
    conn.close()
    
    from core.project_manager import ProjectManager
    ProjectManager.create_project(p_id)
    
    try:
        # Chạy với index 0 để lấy ảnh mẫu
        img = get_background_image(0, p_id)
        print("Image test success. Path:", str(img))
        
        # Kiểm tra workflow status trong DB
        p = ProjectManager.load_project(p_id)
        print("Image status in DB:", p["workflow_status"]["image"])
        
        # Cleanup file ảnh vừa tạo để đỡ tốn bộ nhớ
        if img.exists():
            img.unlink()
            
    except Exception as e:
        print("Test failed:", str(e))
        
    # Cleanup project
    conn = get_db_connection()
    with conn:
        conn.execute("DELETE FROM projects WHERE project_id = ?;", (p_id,))
    conn.close()
    
    p_json = ProjectManager.get_project_json_path(p_id)
    if p_json.exists():
        p_json.unlink()

