"""
AI FILE NOTE - IMAGE UPSCALER
Chức năng chính:
- Phóng to ảnh lên chuẩn Full HD 1920x1080.
- Ưu tiên AI Upscale qua endpoint /sdapi/v1/extra-single-image của SD Local WebUI (bộ lọc mặc định R-ESRGAN 4x+).
- Fallback dùng FFmpeg scale Lanczos 1920x1080 khi không có/lỗi WebUI.
Đầu vào chính:
- input_path (Path ảnh gốc), output_path (Path), api_url tùy chọn, upscaler_name, scale_factor (mặc định 2.0).
Đầu ra chính:
- File ảnh đã upscale tại output_path; trả dict metadata {upscale_method, scale_factor, success, error_detail}.
API được file khác sử dụng:
- Lớp ImageUpscaler.upscale_image(); exception ImageUpscaleError.
Phụ thuộc quan trọng:
- requests (gọi WebUI), ffmpeg (qua subprocess), base64, logging.
Lưu ý khi sửa:
- FFmpeg là fallback bắt buộc; nếu cả AI lẫn Lanczos thất bại thì ném ImageUpscaleError.
- Máy không GPU sẽ dựa vào fallback FFmpeg; giữ nhánh này hoạt động ổn định.
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
