"""
Image Upscaler Module.
Upscales input images to Full HD (1920x1080) using Stable Diffusion Local WebUI extra single-image endpoint, with a PIL Lanczos fallback.
"""
import os
import sys
import subprocess
import logging
import base64
import requests
from pathlib import Path

logger = logging.getLogger("lofi_automation")

class ImageUpscaleError(Exception):
    """Lỗi phát sinh trong quá trình upscale hình ảnh."""
    pass

class ImageUpscaler:
    """
    Quản lý việc phóng to hình ảnh (Upscale) lên chuẩn Full HD 1920x1080 (Mục 5).
    Hỗ trợ AI Upscale qua AUTOMATIC1111 và fallback Lanczos qua FFmpeg.
    """

    @classmethod
    def upscale_image(cls, input_path: Path, output_path: Path, api_url: str = None,
                      upscaler_name: str = "R-ESRGAN 4x+", scale_factor: float = 2.0) -> dict:
        """
        Thực hiện upscale hình ảnh. Trả về metadata của quá trình xử lý.
        """
        input_path = Path(input_path)
        output_path = Path(output_path)
        
        if not input_path.is_file():
            raise ImageUpscaleError(f"Không tìm thấy file ảnh gốc: {input_path}")
            
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        metadata = {
            "upscale_method": "lanczos_fallback",
            "scale_factor": scale_factor,
            "success": True,
            "error_detail": None
        }

        # 1. Thử AI Upscale nếu có api_url hoạt động
        if api_url:
            api_url = api_url.rstrip("/")
            try:
                logger.info(f"[ImageUpscaler] Đang thử AI Upscale qua endpoint của WebUI...")
                with open(input_path, "rb") as img_f:
                    img_b64 = base64.b64encode(img_f.read()).decode("utf-8")
                    
                payload = {
                    "resize_mode": 0,
                    "upscaling_resize": scale_factor,
                    "upscaler_1": upscaler_name,
                    "image": img_b64
                }
                
                # Gọi API extra-single-image của WebUI
                r = requests.post(f"{api_url}/sdapi/v1/extra-single-image", json=payload, timeout=90)
                if r.status_code == 200:
                    res_json = r.json()
                    res_image_b64 = res_json.get("image")
                    if res_image_b64:
                        # Ghi file ảnh đã upscale thành công
                        with open(output_path, "wb") as out_f:
                            out_f.write(base64.b64decode(res_image_b64))
                        
                        metadata["upscale_method"] = f"ai_upscale_{upscaler_name}"
                        logger.info(f"[ImageUpscaler] AI Upscale thành công sử dụng bộ lọc {upscaler_name}.")
                        return metadata
                logger.warning(f"[ImageUpscaler] WebUI API trả về status: {r.status_code}. Chuyển sang fallback.")
            except Exception as e:
                logger.warning(f"[ImageUpscaler] Lỗi kết nối WebUI API khi upscale: {e}. Chuyển sang fallback.")
                metadata["error_detail"] = str(e)
                
        # 2. Fallback sang bộ lọc Lanczos chất lượng cao bằng FFmpeg
        try:
            logger.info(f"[ImageUpscaler] Thực hiện upscale Lanczos 1920x1080 qua FFmpeg...")
            cmd = [
                "ffmpeg", "-y",
                "-i", str(input_path),
                "-vf", "scale=1920:1080:flags=lanczos",
                str(output_path)
            ]
            result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
            
            if result.returncode != 0:
                raise ImageUpscaleError(f"FFmpeg Lanczos scale thất bại: {result.stderr}")
                
            metadata["upscale_method"] = "lanczos_fallback"
            return metadata
            
        except Exception as e:
            logger.error(f"[ImageUpscaler] Cả AI Upscale và Lanczos Fallback đều thất bại: {e}")
            raise ImageUpscaleError(f"Không thể thực hiện phóng ảnh: {e}")
