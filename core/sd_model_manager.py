import logging
import hashlib
import requests
import time
from pathlib import Path
from core.lock_manager import LockManager, LockAcquisitionError

logger = logging.getLogger("lofi_automation")

class SDModelError(Exception):
    """Lỗi quản lý model checkpoint Stable Diffusion."""
    pass

class SDModelManager:
    """
    Quản lý các model checkpoint của Stable Diffusion (Gate G3/G4 / Mục 6.12).
    """
    @classmethod
    def get_file_sha256(cls, file_path: Path) -> str:
        """Tính toán mã băm SHA-256 của tệp tin checkpoint."""
        sha = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                sha.update(chunk)
        return sha.hexdigest()

    @classmethod
    def verify_model_hash(cls, model_path: Path, expected_sha256: str) -> bool:
        """Xác minh mã băm của tệp tin checkpoint trước khi nạp (Mục 6.3)."""
        if not model_path.exists():
            return False
        actual = cls.get_file_sha256(model_path)
        return actual.lower() == expected_sha256.lower()

    @classmethod
    def load_checkpoint(cls, api_url: str, model_title: str) -> bool:
        """
        Nạp model checkpoint được chỉ định qua API Options (Mục 6.12).
        Đợi nạp thành công và đối chiếu lại model nạp thực tế.
        """
        api_url = api_url.rstrip("/")
        
        # 1. Lấy danh sách models khả dụng
        try:
            r = requests.get(f"{api_url}/sdapi/v1/sd-models", timeout=10)
            r.raise_for_status()
            models = r.json()
        except Exception as e:
            raise SDModelError(f"Không lấy được danh sách models từ SD WebUI: {e}")
            
        # Tìm xem model mong muốn có khớp với title/model_name nào không
        matched_model = None
        for m in models:
            if model_title in m["title"] or model_title in m["model_name"]:
                matched_model = m
                break
                
        if not matched_model:
            available_titles = [m["title"] for m in models]
            raise SDModelError(f"Không tìm thấy model '{model_title}' trong các model khả dụng: {available_titles}")
            
        target_title = matched_model["title"]
        
        # 2. Đổi checkpoint qua endpoint options
        try:
            logger.info(f"[SDModel] Đang gửi yêu cầu đổi model sang: {target_title}...")
            payload = {"sd_model_checkpoint": target_title}
            r = requests.post(f"{api_url}/sdapi/v1/options", json=payload, timeout=60)
            r.raise_for_status()
        except Exception as e:
            raise SDModelError(f"Lỗi khi gửi yêu cầu đổi model checkpoint: {e}")
            
        # 3. Đợi model nạp thành công (đối chiếu lại)
        # WebUI có thể mất vài giây tới vài chục giây để đổi model
        for attempt in range(12): # Đợi tối đa 60 giây
            try:
                r_opt = requests.get(f"{api_url}/sdapi/v1/options", timeout=5)
                r_opt.raise_for_status()
                current_checkpoint = r_opt.json().get("sd_model_checkpoint")
                if current_checkpoint and (target_title in current_checkpoint or current_checkpoint in target_title):
                    logger.info(f"[SDModel] Đã nạp thành công model checkpoint: {current_checkpoint}")
                    return True
            except Exception as e:
                logger.warning(f"[SDModel] Đang đợi nạp model... Lỗi thử lại: {e}")
            time.sleep(5)
            
        raise SDModelError(f"Quá thời gian chờ nạp model checkpoint '{target_title}'.")

    @classmethod
    def acquire_exclusive_model_lease(cls, project_id: str, owner_id: str, lease_seconds: int = 120) -> bool:
        """
        Lấy khóa tài nguyên model độc quyền (Exclusive Lease) khi đang chạy render.
        Ngăn chặn các dự án khác đổi chéo checkpoint giữa chừng (Mục 6.12).
        """
        resource_id = "sd_model_checkpoint_lease"
        try:
            token = LockManager.acquire_lock("gpu", resource_id, owner_id, lease_seconds=lease_seconds)
            if token:
                logger.info(f"[SDModel] Đã khóa model checkpoint lease cho project '{project_id}' (Owner: {owner_id}).")
                return True
        except LockAcquisitionError:
            pass
        return False

    @classmethod
    def release_exclusive_model_lease(cls, owner_id: str) -> bool:
        """Giải phóng khóa nạp model sau khi hoàn tất công việc."""
        resource_id = "sd_model_checkpoint_lease"
        return LockManager.release_lock("gpu", resource_id, owner_id)
