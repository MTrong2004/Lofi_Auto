import os
import sys
import shutil
import logging
import subprocess
import json
import uuid
import random
from pathlib import Path
from datetime import datetime, timezone
import psutil
import config
from core.schemas import validate_data_schema

logger = logging.getLogger("lofi_automation")

class SDInstallerError(Exception):
    """Lỗi trong quá trình cài đặt Stable Diffusion."""
    pass

class SDInstaller:
    """
    Module cài đặt tự động Stable Diffusion Local (Gate G4 / Mục 6.4 & 6.8).
    """
    PINNED_GIT_URL = "https://github.com/AUTOMATIC1111/stable-diffusion-webui.git"
    PINNED_COMMIT = "5efc1a7d6e0bb2a7d0e459fe1a2b0c34efca07e6"  # Phiên bản v1.6.0 ổn định

    @classmethod
    def run_preflight(cls, install_dir: Path) -> dict:
        """Kiểm tra điều kiện tiên quyết trước khi cài đặt (Mục 6.4)."""
        install_dir = Path(install_dir)
        results = {
            "os_check": "passed",
            "gpu_check": "passed",
            "ram_check": "passed",
            "disk_check": "passed",
            "write_permission": "passed",
            "overall": "passed",
            "warnings": [],
            "errors": []
        }
        
        # 1. Kiểm tra Hệ điều hành
        if sys.platform != "win32":
            results["os_check"] = "failed"
            results["errors"].append("Hệ điều hành không phải Windows. Ứng dụng chỉ được tối ưu hóa cho Windows.")
            
        # 2. Kiểm tra GPU Nvidia
        try:
            nvidia_smi = shutil.which("nvidia-smi")
            if not nvidia_smi:
                results["gpu_check"] = "warning"
                results["warnings"].append("Không tìm thấy lệnh nvidia-smi. Hãy chắc chắn máy tính của bạn sử dụng card NVIDIA.")
            else:
                # Chạy nvidia-smi để kiểm tra VRAM
                r = subprocess.run([nvidia_smi, "--query-gpu=memory.total", "--format=csv,noheader,nounits"], capture_output=True, text=True, check=True)
                vram_mb = int(r.stdout.strip())
                if vram_mb < 3900: # ~4GB
                    results["gpu_check"] = "warning"
                    results["warnings"].append(f"GPU chỉ có {vram_mb}MB VRAM. Có thể gặp lỗi OOM (thiếu bộ nhớ). Khuyên dùng >= 4GB VRAM.")
        except Exception as e:
            results["gpu_check"] = "warning"
            results["warnings"].append(f"Không lấy được thông số VRAM GPU: {e}")
            
        # 3. Kiểm tra RAM hệ thống
        mem = psutil.virtual_memory()
        total_ram_gb = mem.total / (1024**3)
        if total_ram_gb < 15.5: # 16GB
            results["ram_check"] = "warning"
            results["warnings"].append(f"RAM hệ thống thấp ({total_ram_gb:.1f} GB). Khuyến cáo RAM >= 16GB để chạy mượt.")
            
        # 4. Kiểm tra dung lượng ổ đĩa trống (Cần ít nhất 10GB trống để tải/giải nén)
        try:
            install_dir.parent.mkdir(parents=True, exist_ok=True)
            usage = shutil.disk_usage(install_dir.parent if install_dir.parent.exists() else Path.cwd())
            free_gb = usage.free / (1024**3)
            if free_gb < 10.0:
                results["disk_check"] = "failed"
                results["errors"].append(f"Ổ đĩa trống quá thấp ({free_gb:.1f} GB). Cần ít nhất 10GB dung lượng trống để cài đặt.")
        except Exception as e:
            results["disk_check"] = "warning"
            results["warnings"].append(f"Không đo được dung lượng trống ổ đĩa: {e}")
            
        # 5. Kiểm tra quyền ghi file trong thư mục cài đặt
        try:
            install_dir.mkdir(parents=True, exist_ok=True)
            test_file = install_dir / f"test_write_{uuid.uuid4().hex[:8]}.tmp"
            test_file.write_text("write test")
            test_file.unlink()
        except Exception as e:
            results["write_permission"] = "failed"
            results["errors"].append(f"Không có quyền ghi/xóa file trong thư mục cài đặt: {e}")
            
        # Tính toán overall
        if any(v == "failed" for v in [results["os_check"], results["disk_check"], results["write_permission"]]):
            results["overall"] = "failed"
        elif any(v == "warning" for v in [results["gpu_check"], results["ram_check"]]):
            results["overall"] = "warning"
            
        return results

    @classmethod
    def install(cls, install_dir: Path, progress_callback=None) -> bool:
        """Thực hiện cài đặt tự động (Mục 6.8)."""
        install_dir = Path(install_dir)
        preflight = cls.run_preflight(install_dir)
        
        if preflight["overall"] == "failed":
            raise SDInstallerError(f"Cài đặt bị chặn do lỗi Preflight: {preflight['errors']}")
            
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        inst_id = str(uuid.uuid4())
        
        # Trạng thái cài đặt ban đầu
        state = {
            "schema_name": "sd_install_state",
            "schema_version": 1,
            "installation_id": inst_id,
            "ownership_mode": "app_managed",
            "state": "downloading",
            "installed": False,
            "running": False,
            "healthy": False,
            "ready": False,
            "configured_port": 7860,
            "bind_host": "127.0.0.1",
            "updated_at_utc": now_str,
            "distribution_id": "a1111_windows_managed",
            "version": "v1.6.0",
            "commit_or_release": cls.PINNED_COMMIT,
            "adapter_version": "1.0.0",
            "install_root": str(install_dir.as_posix()),
            "last_completed_step": "prechecking"
        }
        
        # Tạo thư mục cài đặt trước, sau đó mới ghi state
        try:
            install_dir.mkdir(parents=True, exist_ok=True)
        except PermissionError as pe:
            raise SDInstallerError(
                f"Không có quyền tạo thư mục '{install_dir}'.\n"
                f"Hãy chọn một thư mục khác mà bạn có quyền ghi, ví dụ: D:/LofiSD hoặc C:/AI/SD."
            ) from pe
        
        cls._write_state_atomic(install_dir, state)
        
        try:
            # 1. Clone Git repository (detached commit)
            if progress_callback:
                progress_callback(0.1, "Đang tải mã nguồn AUTOMATIC1111 từ Git...")
                
            webui_dir = install_dir / "stable-diffusion-webui"
            if not webui_dir.exists():
                logger.info(f"[SDInstaller] Đang clone SD WebUI về: {webui_dir}")
                subprocess.run(["git", "clone", cls.PINNED_GIT_URL, str(webui_dir)], check=True)
                
            # Checkout đúng commit đã pin
            logger.info(f"[SDInstaller] Checkout commit: {cls.PINNED_COMMIT}")
            subprocess.run(["git", "checkout", cls.PINNED_COMMIT], cwd=str(webui_dir), check=True)
            
            state["state"] = "creating_environment"
            state["last_completed_step"] = "downloading"
            cls._write_state_atomic(install_dir, state)
            
            # 2. Tạo Virtual Environment riêng biệt
            if progress_callback:
                progress_callback(0.4, "Đang khởi tạo môi trường Python venv riêng biệt...")
                
            venv_dir = install_dir / "runtime" / "venv"
            venv_dir.parent.mkdir(parents=True, exist_ok=True)
            
            if not venv_dir.exists():
                logger.info(f"[SDInstaller] Đang tạo venv tại: {venv_dir}")
                subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)
                
            state["state"] = "installing_dependencies"
            state["last_completed_step"] = "creating_environment"
            cls._write_state_atomic(install_dir, state)
            
            # 3. Cài đặt các thư viện cần thiết
            if progress_callback:
                progress_callback(0.6, "Đang cài đặt các thư viện (Torch, requirements)... Có thể mất vài phút.")
                
            pip_exe = venv_dir / "Scripts" / "pip.exe" if sys.platform == "win32" else venv_dir / "bin" / "pip"
            
            # Nâng cấp pip và cài đặt torch
            logger.info("[SDInstaller] Nâng cấp pip...")
            subprocess.run([str(pip_exe), "install", "--upgrade", "pip"], check=True)
            
            # Cài đặt torch tương thích CUDA
            logger.info("[SDInstaller] Cài đặt PyTorch...")
            subprocess.run([str(pip_exe), "install", "torch", "torchvision", "--index-url", "https://download.pytorch.org/whl/cu118"], check=True)
            
            # Cài đặt requirements của WebUI
            logger.info("[SDInstaller] Cài đặt requirements.txt...")
            req_file = webui_dir / "requirements.txt"
            if req_file.exists():
                subprocess.run([str(pip_exe), "install", "-r", str(req_file)], check=True)
                
            state["state"] = "configuring"
            state["last_completed_step"] = "installing_dependencies"
            cls._write_state_atomic(install_dir, state)
            
            # 4. Ghi nhận thành công
            if progress_callback:
                progress_callback(0.9, "Cài đặt thành công! Đang thiết lập cấu hình chạy.")
                
            state["state"] = "ready"
            state["installed"] = True
            state["last_completed_step"] = "configuring"
            cls._write_state_atomic(install_dir, state)
            
            logger.info("[SDInstaller] Quá trình cài đặt Stable Diffusion hoàn tất thành công.")
            return True
            
        except Exception as e:
            logger.error(f"[SDInstaller] Lỗi cài đặt: {e}")
            state["state"] = "failed_manual_action"
            state["last_error_id"] = str(uuid.uuid4())
            cls._write_state_atomic(install_dir, state)
            raise SDInstallerError(f"Cài đặt Stable Diffusion thất bại: {e}")

    @classmethod
    def _write_state_atomic(cls, install_dir: Path, state_data: dict):
        """Ghi trạng thái cài đặt nguyên tử (Atomic Write) theo schema."""
        state_data["updated_at_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        
        # Xác thực schema trước khi ghi
        validate_data_schema(state_data, "sd_install_state")
        
        # Đảm bảo thư mục tồn tại
        install_dir.mkdir(parents=True, exist_ok=True)
        
        out_path = install_dir / "install_state.json"
        tmp_path = out_path.with_suffix(".tmp")
        
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(state_data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
                
            os.replace(tmp_path, out_path)
            
            # Sync thư mục cha — chỉ thực hiện trên Linux/macOS
            # Windows không hỗ trợ os.fsync() trên handle thư mục (PermissionError)
            if sys.platform != "win32":
                try:
                    parent_fd = os.open(str(install_dir), os.O_RDONLY)
                    try:
                        os.fsync(parent_fd)
                    finally:
                        os.close(parent_fd)
                except OSError:
                    pass  # Directory fsync là best-effort
        except Exception as e:
            logger.error(f"[SDInstaller] Ghi install_state.json thất bại: {e}")
            if tmp_path.exists():
                tmp_path.unlink()
            raise
