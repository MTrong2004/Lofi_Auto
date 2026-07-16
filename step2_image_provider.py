"""
AI FILE NOTE - STEP 2: IMAGE PROVIDER AND EFFECT ASSETS

Chức năng chính:
- Chuẩn hóa prompt và tạo ảnh nền qua nhiều provider online/local.
- Provider hỗ trợ: Pollinations, AI Horde, Hugging Face, Cloudflare và SD Local.
- Tự fallback theo IMAGE_PROVIDER_ORDER, kiểm tra file ảnh và upscale Full HD bằng FFmpeg.
- Xử lý ảnh upload thành 16:9, đăng ký metadata/asset vào SQLite.
- Quản lý video overlay: liệt kê, tải Pexels, tạo bộ hiệu ứng local và chọn hiệu ứng.

Đầu vào chính:
- Prompt, provider/config API, ảnh upload, project_id hoặc từ khóa hiệu ứng.

Đầu ra chính:
- Path ảnh nền 1920x1080, Path video hiệu ứng và metadata tương ứng.

API được file khác sử dụng:
- Các lớp *Provider và ImageProvider.generate()
- normalize_image_prompt(), validate_image_file(), scale_image_ffmpeg()
- prepare_local_background(), get_background_image()
- list_effect_videos(), download_pexels_effect()
- create_builtin_effect_pack(), pick_effect_video()

Phụ thuộc quan trọng:
- requests, config, FFmpeg; SD Local còn phụ thuộc core.sd_manager và API A1111/ComfyUI.

Lưu ý khi sửa:
- Mọi provider phải trả Path tới ảnh hợp lệ và đi qua validate_image_file().
- Không đổi kích thước đầu ra 1920x1080 hoặc tên provider nếu chưa cập nhật step3_review_app.py.
- Giữ cơ chế model lease của SD Local để tránh nhiều tiến trình cùng chiếm model/GPU.
"""
import base64
import logging
import os
import subprocess
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

        response = requests.get(url, params=params, timeout=(10, 60))
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
        # AI Horde nhận negative prompt qua cú pháp "prompt ### negative"
        negative = getattr(config, "IMAGE_NEGATIVE_PROMPT", "")
        if negative:
            final_prompt = f"{final_prompt} ### {negative}"
        headers = {
            "apikey": self.api_key,
            "Client-Agent": "lofi-automation:1.0:local-app",
        }
        payload = {
            "prompt": final_prompt,
            "params": {
                "width": 1024,
                "height": 576,
                "steps": 28,
                "n": 1,
                "cfg_scale": 6.5,
                "sampler_name": "k_dpmpp_2m",
                "karras": True,
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
                "negative_prompt": getattr(config, "IMAGE_NEGATIVE_PROMPT", ""),
            },
        }
        # Endpoint router mới (api-inference.huggingface.co đã ngừng hoạt động)
        url = f"https://router.huggingface.co/hf-inference/models/{self.model_id}"

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


class CloudflareWorkersAIProvider(ImageProvider):
    """Cloudflare Workers AI - SDXL, free tier ~10k neurons/ngày."""

    def generate(self, prompt: str, out_path: Path) -> Path:
        account_id = getattr(config, "CLOUDFLARE_ACCOUNT_ID", "")
        token = getattr(config, "CLOUDFLARE_API_TOKEN", "")
        model = getattr(config, "CLOUDFLARE_IMAGE_MODEL", "@cf/stabilityai/stable-diffusion-xl-base-1.0")
        if not account_id or not token:
            raise ValueError("Chưa cấu hình CLOUDFLARE_ACCOUNT_ID / CLOUDFLARE_API_TOKEN trong .env.")

        final_prompt = normalize_image_prompt(prompt)
        url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}"
        payload = {
            "prompt": final_prompt,
            "negative_prompt": getattr(config, "IMAGE_NEGATIVE_PROMPT", ""),
            "width": 1024,
            "height": 576,
            "num_steps": 20,
            "guidance": 7,
        }
        response = requests.post(
            url, headers={"Authorization": f"Bearer {token}"}, json=payload, timeout=120
        )
        response.raise_for_status()

        content_type = response.headers.get("content-type", "").lower()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if "image" in content_type:
            out_path.write_bytes(response.content)
        else:
            # Một số model trả JSON {"result": {"image": "<base64>"}}
            img_b64 = (response.json().get("result") or {}).get("image")
            if not img_b64:
                raise ValueError(f"Cloudflare không trả về ảnh hợp lệ: {response.text[:200]}")
            out_path.write_bytes(base64.b64decode(img_b64))

        validate_image_file(out_path)
        logger.info(f"[Cloudflare] Đã tạo ảnh: {out_path.name}")
        return out_path


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
        
        # 1. Kiểm tra khả năng từ Registry trước
        from core.provider_capability import ProviderCapabilityRegistry
        if not ProviderCapabilityRegistry.has_capability("SDLocalProvider", "txt2img"):
            # Thử tự chạy check lại xem instance có bật lên chưa
            from core.sd_manager import SDAdapter
            adapter = SDAdapter(config.SD_LOCAL_API_URL)
            if not adapter.capability_check():
                raise RuntimeError("Stable Diffusion Local không khả dụng hoặc chưa khởi động.")

        # 2. Độc quyền nạp model (Exclusive model lease)
        from core.sd_manager import SDModelManager
        owner_id = f"worker_sd_{os.getpid()}"
        SDModelManager.acquire_exclusive_model_lease("project_sd", owner_id, lease_seconds=120)
        
        try:
            # 3. Đảm bảo model checkpoint đã được nạp
            checkpoint = getattr(config, "SD_LOCAL_CHECKPOINT", "")
            if checkpoint:
                SDModelManager.load_checkpoint(config.SD_LOCAL_API_URL, checkpoint)
                
            # 4. Thực thi sinh ảnh qua SDAdapter
            from core.sd_manager import SDAdapter
            adapter = SDAdapter(config.SD_LOCAL_API_URL)
            
            final_prompt = normalize_image_prompt(prompt)
            steps = getattr(config, "SD_LOCAL_STEPS", 24)
            width = getattr(config, "SD_LOCAL_WIDTH", 1024)
            height = getattr(config, "SD_LOCAL_HEIGHT", 576)
            
            payload = {
                "prompt": final_prompt,
                "negative_prompt": getattr(config, "IMAGE_NEGATIVE_PROMPT", "text, watermark, logo, blurry, low quality"),
                "steps": steps,
                "width": width,
                "height": height,
                "cfg_scale": getattr(config, "SD_LOCAL_CFG_SCALE", 7),
                "sampler_name": getattr(config, "SD_LOCAL_SAMPLER", "DPM++ 2M Karras"),
                "seed": random.randint(100000, 999999),
                # Clip skip 2: chuẩn cho checkpoint anime SD1.5, màu/nét đúng style hơn
                "override_settings": {"CLIP_stop_at_last_layers": 2},
                "override_settings_restore_afterwards": True,
            }
            
            img_b64 = adapter.txt2img(payload)
            
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(base64.b64decode(img_b64))
            validate_image_file(out_path)
            
            # 5. Lưu báo cáo sức khỏe sau khi chạy thành công
            from core.sd_manager import SDHealthChecker
            health_report_path = config.METADATA_DIR / "sd_health_report.json"
            SDHealthChecker.run_health_check(config.SD_LOCAL_API_URL, health_report_path)
            
            logger.info(f"[SDLocal] Đã tạo ảnh thành công và cập nhật health check: {out_path.name}")
            return out_path
            
        finally:
            # Giải phóng model lease
            SDModelManager.release_exclusive_model_lease(owner_id)


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


def prepare_local_background(
    input_path: Path,
    output_path: Path,
    zoom_percent: int = 8,
    target_width: int = 1920,
    target_height: int = 1080,
) -> Path:
    """Cắt giữa theo 16:9, phóng nhẹ để loại viền góc, rồi xuất Full HD."""
    input_path = Path(input_path)
    output_path = Path(output_path)
    if not input_path.exists():
        raise FileNotFoundError(f"Không tìm thấy ảnh upload: {input_path}")

    zoom_percent = max(0, min(int(zoom_percent), 20))
    keep_ratio = max(0.80, 1.0 - zoom_percent / 100.0)
    target_ratio = target_width / target_height
    crop_filter = (
        f"crop='if(gt(iw/ih,{target_ratio}),ih*{target_ratio},iw)*{keep_ratio}':"
        f"'if(gt(iw/ih,{target_ratio}),ih,iw/{target_ratio})*{keep_ratio}':"
        "(iw-ow)/2:(ih-oh)/2,"
        f"scale={target_width}:{target_height}:flags=lanczos,setsar=1"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-i", str(input_path),
        "-vf", crop_filter,
        "-frames:v", "1",
        str(output_path),
    ]
    try:
        subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "FFmpeg không trả chi tiết").strip().splitlines()
        raise RuntimeError(f"Không xử lý được ảnh upload: {detail[-1] if detail else 'Không rõ lỗi'}") from exc

    validate_image_file(output_path)
    return output_path

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

    provider_factories = {
        "pollinations": PollinationsProvider,
        "aihorde": lambda: AIHordeProvider(getattr(config, "AI_HORDE_API_KEY", "0000000000")),
        "huggingface": lambda: HuggingFaceProvider(
            getattr(config, "HUGGINGFACE_TOKEN", ""),
            getattr(config, "HUGGINGFACE_MODEL", "stabilityai/stable-diffusion-xl-base-1.0"),
        ),
        "cloudflare": CloudflareWorkersAIProvider,
        "sdlocal": SDLocalProvider,
    }
    order = getattr(config, "IMAGE_PROVIDER_ORDER", None) or ["pollinations", "aihorde", "sdlocal"]
    providers = [provider_factories[name]() for name in order if name in provider_factories]
    if not providers:
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
    """
    Tạo bộ hiệu ứng code local (nền đen, dùng với blend screen).
    Kỹ thuật hạt rơi: sinh 1 khung noise tĩnh (select frame 0 + loop) rồi cuộn dọc
    bằng filter scroll -> hạt có quỹ đạo rơi thật thay vì nhiễu nhấp nháy.
    Tốc độ cuộn chọn sao cho sau 8s (192 frame) trôi tròn số lần chiều cao khung
    -> video lặp khít (seamless loop) khi render dùng -stream_loop.
    """
    import subprocess
    config.EFFECTS_DIR.mkdir(parents=True, exist_ok=True)

    # Khung noise tĩnh: giữ frame 0, lặp đủ 192 frame, đặt lại timestamp 24fps
    _static_noise = (
        "select='eq(n,0)',loop=loop=191:size=1:start=0,setpts=N/(24*TB)"
    )
    # 3/192 vòng/frame: mưa rơi ~270px/s; 1/192: tuyết ~90px/s (đều tròn vòng sau 8s)
    effect_specs = {
        # Mưa: hạt thưa kéo dọc thành vệt (avgblur dọc) + tăng sáng lại, cuộn nhanh
        "effect_rain_fall.mp4": (
            "nullsrc=s=1280x720:d=8:r=24,"
            "geq=lum='if(gt(random(1),0.9975),255,0)':cb=128:cr=128,"
            f"{_static_noise},"
            "avgblur=sizeX=1:sizeY=7,lutyuv=y='min(val*10,190)',"
            "scroll=vertical=0.015625,"
            "format=yuv420p"
        ),
        # Tuyết: bông mềm (blur đều), rơi chậm
        "effect_snow_fall.mp4": (
            "nullsrc=s=1280x720:d=8:r=24,"
            "geq=lum='if(gt(random(1),0.996),255,0)':cb=128:cr=128,"
            f"{_static_noise},"
            "avgblur=sizeX=2:sizeY=2,lutyuv=y='min(val*7,210)',"
            "scroll=vertical=0.00520833,"
            "format=yuv420p"
        ),
        # Bụi: hạt rất thưa, mờ và tối, trôi lơ lửng lên trên
        "effect_dust_soft.mp4": (
            "nullsrc=s=1280x720:d=8:r=24,"
            "geq=lum='if(gt(random(1),0.998),255,0)':cb=128:cr=128,"
            f"{_static_noise},"
            "avgblur=sizeX=3:sizeY=3,lutyuv=y='min(val*5,140)',"
            "scroll=vertical=-0.00520833,"
            "format=yuv420p"
        ),
        # Scanline retro: tĩnh, vốn là hiệu ứng nhân tạo nên giữ nguyên
        "effect_retro_scanline.mp4": "nullsrc=s=1280x720:d=8:r=24,geq=lum='if(eq(mod(Y,6),0),70,0)':cb=128:cr=128,format=yuv420p",
        # Film grain: random mỗi frame là ĐÚNG bản chất grain, giữ nguyên
        "effect_light_film_grain.mp4": "nullsrc=s=1280x720:d=8:r=24,geq=lum='random(1)*45':cb=128:cr=128,format=yuv420p",
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

