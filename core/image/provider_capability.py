"""
AI FILE NOTE - PROVIDER CAPABILITY REGISTRY
Chức năng chính:
- Theo dõi khả năng (capabilities) và trạng thái sức khỏe của các provider sinh ảnh (SD Local, AI Horde, Pollinations, HuggingFace).
- Cho Core xác định động provider nào khả dụng và hỗ trợ tính năng gì (txt2img, image_upscale...) trước khi thực thi.
- Đăng ký sẵn các provider mặc định của hệ thống ở cuối file.
Đầu vào chính:
- provider_name, capabilities (dict {tên_capability: bool/str}), status ("available"/"degraded"/"disabled").
Đầu ra chính:
- Trạng thái provider (str), kết quả kiểm tra capability (bool).
API được file khác sử dụng:
- Lớp ProviderCapabilityRegistry: register_provider(), update_status(), get_provider_status(), has_capability().
Phụ thuộc quan trọng:
- logging (thuần Python, không phụ thuộc ngoài).
Lưu ý khi sửa:
- _registry là state cấp lớp (class-level) dùng chung toàn tiến trình; lưu ý khi thay đổi.
- has_capability() trả False nếu provider ở trạng thái "disabled" bất kể capability khai báo.
"""
import logging

logger = logging.getLogger("lofi_automation")

class ProviderCapabilityRegistry:
    """
    Quản lý khả năng (capabilities) của các bộ điều hợp (adapters) tích hợp ngoài.
    Giúp Core xác định động tính khả dụng trước khi thực thi công việc (Mục 39).
    """
    _registry = {}

    @classmethod
    def register_provider(cls, provider_name: str, capabilities: dict):
        """Đăng ký nhà cung cấp với các khả năng và trạng thái tương ứng."""
        cls._registry[provider_name] = {
            "status": "available",  # "available", "degraded", "disabled"
            "capabilities": capabilities,  # dict {capability_name: bool/str}
            "last_checked_utc": None
        }
        logger.info(f"[CapabilityRegistry] Đăng ký provider '{provider_name}' thành công.")

    @classmethod
    def update_status(cls, provider_name: str, status: str):
        """Cập nhật trạng thái sức khỏe/sẵn sàng của provider."""
        if provider_name in cls._registry:
            cls._registry[provider_name]["status"] = status
            logger.info(f"[CapabilityRegistry] Cập nhật trạng thái provider '{provider_name}' thành: {status}")

    @classmethod
    def get_provider_status(cls, provider_name: str) -> str:
        """Lấy trạng thái tổng quan của nhà cung cấp."""
        if provider_name not in cls._registry:
            return "disabled"
        return cls._registry[provider_name]["status"]

    @classmethod
    def has_capability(cls, provider_name: str, capability_name: str) -> bool:
        """Kiểm tra một adapter cụ thể có hỗ trợ tính năng nào không (txt2img, upscale, audio_download...)."""
        if provider_name not in cls._registry:
            return False
        
        provider = cls._registry[provider_name]
        if provider["status"] == "disabled":
            return False
            
        caps = provider["capabilities"]
        return caps.get(capability_name, False) is True

# Đăng ký sẵn các provider mặc định của hệ thống
ProviderCapabilityRegistry.register_provider("PollinationsProvider", {
    "txt2img": True,
    "image_upscale": False
})

ProviderCapabilityRegistry.register_provider("AIHordeProvider", {
    "txt2img": True,
    "image_upscale": False
})

ProviderCapabilityRegistry.register_provider("HuggingFaceProvider", {
    "txt2img": True,
    "image_upscale": False
})

ProviderCapabilityRegistry.register_provider("SDLocalProvider", {
    "txt2img": True,
    "image_upscale": True,
    "api_options": True,
    "progress_check": True
})
