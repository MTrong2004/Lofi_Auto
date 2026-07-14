import base64
import logging
import requests
import json
from pathlib import Path
from core.provider_capability import ProviderCapabilityRegistry

logger = logging.getLogger("lofi_automation")

class SDAdapterError(Exception):
    """Lỗi phát sinh trong các thao tác của SD Adapter."""
    pass

class SDAdapter:
    """
    Adapter phiên bản hóa kết nối và điều phối Stable Diffusion Local WebUI (Mục 6.11).
    """
    def __init__(self, api_url: str):
        self.api_url = api_url.rstrip("/")

    def discover_api(self) -> dict:
        """Khám phá schema OpenAPI động từ endpoint /openapi.json hoặc /docs."""
        try:
            r = requests.get(f"{self.api_url}/openapi.json", timeout=10)
            if r.status_code == 200:
                logger.info("[SDAdapter] Đã tải OpenAPI schema từ /openapi.json.")
                return r.json()
        except Exception as e:
            logger.warning(f"[SDAdapter] Không tải được /openapi.json: {e}. Thử fallback /docs.")
            
        try:
            r = requests.get(f"{self.api_url}/docs", timeout=10)
            if r.status_code == 200:
                logger.info("[SDAdapter] API docs có sẵn tại /docs.")
                return {"docs_available": True}
        except Exception as e:
            logger.warning(f"[SDAdapter] docs fallback cũng thất bại: {e}")
            
        raise SDAdapterError("Không thể kết nối hoặc khám phá API cấu trúc của Stable Diffusion.")

    def capability_check(self) -> bool:
        """Kiểm tra sự tồn tại của các endpoint bắt buộc."""
        endpoints = [
            "/sdapi/v1/options",
            "/sdapi/v1/sd-models",
            "/sdapi/v1/samplers",
            "/sdapi/v1/txt2img"
        ]
        # Thử gọi kiểm tra nhanh GET cho các endpoint cấu hình
        try:
            for ep in endpoints[:-1]: # skip txt2img vì nó là POST
                r = requests.get(f"{self.api_url}{ep}", timeout=5)
                if r.status_code != 200:
                    logger.warning(f"[SDAdapter] Endpoint bắt buộc '{ep}' trả về status: {r.status_code}")
                    return False
            
            # Cập nhật Registry
            ProviderCapabilityRegistry.update_status("SDLocalProvider", "available")
            return True
        except Exception as e:
            logger.error(f"[SDAdapter] Lỗi kiểm tra capability: {e}")
            ProviderCapabilityRegistry.update_status("SDLocalProvider", "disabled")
            return False

    def validate_response(self, response_data: dict, expected_width: int, expected_height: int) -> str:
        """
        Kiểm tra tính hợp lệ của response (Mục 6.11).
        Tránh cạn RAM bằng cách giới hạn kích thước ảnh và kiểm tra base64 hợp lệ.
        """
        if not response_data or "images" not in response_data:
            raise SDAdapterError("Response không chứa trường 'images'.")
            
        images = response_data["images"]
        if not images or len(images) == 0:
            raise SDAdapterError("Mảng 'images' rỗng.")
            
        img_b64 = images[0]
        # Giới hạn kích thước ảnh thô tối đa 30MB để tránh cạn RAM
        if len(img_b64) > 30 * 1024 * 1024:
            raise SDAdapterError("Dữ liệu ảnh trả về vượt quá giới hạn an toàn 30MB.")
            
        # Kiểm tra giải mã base64 có thành công không
        try:
            decoded = base64.b64decode(img_b64)
            if len(decoded) < 1024:
                raise SDAdapterError("Ảnh giải mã quá nhỏ (< 1KB).")
        except Exception as e:
            raise SDAdapterError(f"Dữ liệu trả về không phải base64 hợp lệ: {e}")
            
        return img_b64

    def txt2img(self, payload: dict) -> str:
        """Gọi API sinh ảnh txt2img chính thức (Mục 6.11)."""
        url = f"{self.api_url}/sdapi/v1/txt2img"
        try:
            logger.info(f"[SDAdapter] Gửi yêu cầu txt2img tới {url}...")
            r = requests.post(url, json=payload, timeout=180)
            r.raise_for_status()
            
            # Giới hạn kích thước response body tối đa 50MB tránh tràn RAM
            if len(r.content) > 50 * 1024 * 1024:
                raise SDAdapterError("Response body vượt giới hạn an toàn 50MB.")
                
            response_json = r.json()
            width = payload.get("width", 1024)
            height = payload.get("height", 576)
            
            return self.validate_response(response_json, width, height)
        except requests.exceptions.RequestException as e:
            raise SDAdapterError(f"HTTP Connection error: {e}")
        except Exception as e:
            raise SDAdapterError(f"txt2img execution failed: {e}")
