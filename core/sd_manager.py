"""
Stable Diffusion Manager Module.
Consolidates Stable Diffusion WebUI operations: local process creation, automatic installation, option settings, checkpoint switching, health checks, and API requests.
"""
import base64
import hashlib
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Any

import psutil
import requests

import config
from core.lock_manager import LockManager, LockAcquisitionError
from core.provider_capability import ProviderCapabilityRegistry
from core.schemas import validate_data_schema

logger = logging.getLogger("lofi_automation")

# =====================================================================
# Custom Exceptions
# =====================================================================

class SDAdapterError(Exception):
    """Lỗi phát sinh trong các thao tác của SD Adapter."""
    pass

class SDModelError(Exception):
    """Lỗi quản lý model checkpoint Stable Diffusion."""
    pass

class SDProcessError(Exception):
    """Lỗi quản lý tiến trình Stable Diffusion."""
    pass

class SDInstallerError(Exception):
    """Lỗi trong quá trình cài đặt Stable Diffusion."""
    pass

# =====================================================================
# SDAdapter (formerly core/sd_adapter.py)
# =====================================================================

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


# =====================================================================
# SDHealthChecker (formerly core/sd_health.py)
# =====================================================================

def requests_get_models(api_url: str) -> list:
    try:
        r = requests.get(f"{api_url}/sdapi/v1/sd-models", timeout=5)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return []

class SDHealthChecker:
    """
    Thực hiện kiểm tra sức khỏe Stable Diffusion cục bộ và xuất báo cáo (Mục 6.11).
    """
    @classmethod
    def run_health_check(cls, api_url: str, report_out_path: Path) -> dict:
        adapter = SDAdapter(api_url)
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        
        report = {
            "schema_name": "sd_health_report",
            "schema_version": 1,
            "api_check": "failed",
            "model_load_check": "failed",
            "generation_check": "failed",
            "tested_at_utc": now_str
        }
        
        try:
            # 1. API check
            adapter.discover_api()
            is_cap_ok = adapter.capability_check()
            if not is_cap_ok:
                raise ValueError("Không hỗ trợ đủ các API endpoints bắt buộc.")
            report["api_check"] = "passed"
            
            # 2. Model load check
            # Kiểm tra xem có models khả dụng không
            r = requests_get_models(api_url)
            if not r:
                raise ValueError("Không có model checkpoint nào được tải.")
            report["model_load_check"] = "passed"
            
            # 3. Generation check (Test render 256x256 nhẹ)
            test_payload = {
                "prompt": "test lofi style background",
                "steps": 5,
                "width": 256,
                "height": 256,
                "cfg_scale": 5,
                "seed": 42
            }
            img_b64 = adapter.txt2img(test_payload)
            if img_b64:
                report["generation_check"] = "passed"
                
        except Exception as e:
            logger.error(f"[SDHealth] Kiểm tra sức khỏe thất bại: {e}")
            report["error_detail"] = str(e)
            
        # Xác thực báo cáo theo schema
        try:
            validate_data_schema(report, "sd_health_report")
        except Exception as ve:
            logger.error(f"[SDHealth] Báo cáo sức khỏe không khớp schema: {ve}")
            
        # Ghi báo cáo ra file an toàn
        try:
            report_out_path.parent.mkdir(parents=True, exist_ok=True)
            with open(report_out_path, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
            logger.info(f"[SDHealth] Đã xuất báo cáo sức khỏe tại: {report_out_path.name}")
        except Exception as write_err:
            logger.error(f"[SDHealth] Không ghi được file báo cáo: {write_err}")
            
        return report


# =====================================================================
# SDInstaller (formerly core/sd_installer.py)
# =====================================================================

class SDInstaller:
    """
    Module cài đặt tự động Stable Diffusion Local (Gate G4 / Mục 6.4 & 6.8).
    """
    PINNED_GIT_URL = "https://github.com/AUTOMATIC1111/stable-diffusion-webui.git"
    PINNED_COMMIT = "v1.6.0"  # Release tag chính thức
    ALLOWED_EXTENSIONS = {
        "active-development-helper",
        "multidiffusion-upscaler-for-automatic1111",
        "sd-webui-controlnet",
        "stable-diffusion-webui-images-browser"
    }

    @classmethod
    def _rmtree_safe(cls, path: Path):
        """Xóa thư mục an toàn trên Windows chống khóa file và file read-only."""
        if not path.exists():
            return
        def onerror(func, p, exc_info):
            import stat
            try:
                os.chmod(p, stat.S_IWRITE)
                func(p)
            except Exception:
                pass
        for attempt in range(5):
            try:
                shutil.rmtree(str(path), onerror=onerror)
                break
            except Exception:
                time.sleep(0.5)

    @classmethod
    def _safe_rename(cls, src: Path, dst: Path):
        """Đổi tên thư mục an toàn, có thử lại phòng khi antivirus/indexer khóa file."""
        if not src.exists():
            return
        last_err = None
        for attempt in range(5):
            try:
                if dst.exists():
                    if dst.is_dir():
                        cls._rmtree_safe(dst)
                    else:
                        dst.unlink()
                os.rename(str(src), str(dst))
                last_err = None
                break
            except PermissionError as e:
                last_err = e
                time.sleep(0.5)
        if last_err:
            raise last_err

    @classmethod
    def run_preflight(cls, install_dir: Path, port: int = 7860) -> dict:
        """Kiểm tra điều kiện tiên quyết trước khi cài đặt (Mục 6.4)."""
        install_dir = Path(install_dir)
        results = {
            "os_check": "passed",
            "gpu_check": "passed",
            "ram_check": "passed",
            "disk_check": "passed",
            "write_permission": "passed",
            "git_check": "passed",
            "python_check": "passed",
            "port_check": "passed",
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
                r = subprocess.run([nvidia_smi, "--query-gpu=memory.total", "--format=csv,noheader,nounits"], capture_output=True, text=True, check=True)
                vram_mb = int(r.stdout.strip())
                if vram_mb < 3900: # ~4GB
                    results["gpu_check"] = "warning"
                    results["warnings"].append(f"GPU chỉ có {vram_mb}MB VRAM. Có thể gặp lỗi OOM. Khuyên dùng >= 4GB VRAM.")
        except Exception as e:
            results["gpu_check"] = "warning"
            results["warnings"].append(f"Không lấy được thông số VRAM GPU: {e}")
            
        # 3. Kiểm tra RAM hệ thống
        mem = psutil.virtual_memory()
        total_ram_gb = mem.total / (1024**3)
        if total_ram_gb < 15.5: # 16GB
            results["ram_check"] = "warning"
            results["warnings"].append(f"RAM hệ thống thấp ({total_ram_gb:.1f} GB). Khuyến cáo RAM >= 16GB để chạy mượt.")
            
        # 4. Kiểm tra dung lượng ổ đĩa trống
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

        # 6. Kiểm tra Git
        git_path = shutil.which("git")
        if not git_path:
            results["git_check"] = "failed"
            results["errors"].append("Không tìm thấy Git trong hệ thống PATH. Vui lòng cài đặt Git trước.")
            
        # 7. Kiểm tra Python
        python_ver_ok = sys.version_info.major == 3 and sys.version_info.minor in [10, 11]
        if not python_ver_ok:
            results["python_check"] = "warning"
            results["warnings"].append(f"Phiên bản Python hiện tại ({sys.version_info.major}.{sys.version_info.minor}) không phải 3.10/3.11. Có thể gặp lỗi tương thích.")
            
        # 8. Kiểm tra Cổng mạng
        if SDProcessManager.check_port_open("127.0.0.1", port):
            results["port_check"] = "warning"
            results["warnings"].append(f"Cổng {port} hiện đang bị chiếm dụng bởi tiến trình khác.")
            
        # Tính toán overall
        if any(v == "failed" for v in [results["os_check"], results["disk_check"], results["write_permission"], results["git_check"]]):
            results["overall"] = "failed"
        elif any(v == "warning" for v in [results["gpu_check"], results["ram_check"], results["python_check"], results["port_check"]]):
            results["overall"] = "warning"
            
        return results

    @classmethod
    def install(cls, install_dir: Path, port: int = 7860, progress_callback=None) -> bool:
        """Thực hiện cài đặt tự động qua Staging & Rollback (Mục 6.6 & 6.8)."""
        install_dir = Path(install_dir)
        
        # Giai đoạn 1: Prechecking
        if progress_callback:
            progress_callback(0.05, "Đang chạy Preflight check...")
            
        preflight = cls.run_preflight(install_dir, port=port)
        if preflight["overall"] == "failed":
            raise SDInstallerError(f"Cài đặt bị chặn do lỗi Preflight: {preflight['errors']}")
            
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        inst_id = str(uuid.uuid4())
        
        # Giai đoạn 2: User Consent
        if progress_callback:
            progress_callback(0.1, "Khởi tạo trạng thái cài đặt...")
            
        state = {
            "schema_name": "sd_install_state",
            "schema_version": 1,
            "installation_id": inst_id,
            "ownership_mode": "app_managed",
            "state": "waiting_user_consent",
            "installed": False,
            "running": False,
            "healthy": False,
            "ready": False,
            "configured_port": port,
            "bind_host": "127.0.0.1",
            "updated_at_utc": now_str,
            "distribution_id": "a1111_windows_managed",
            "version": "v1.6.0",
            "commit_or_release": cls.PINNED_COMMIT,
            "adapter_version": "1.0.0",
            "install_root": str(install_dir.as_posix()),
            "last_completed_step": "prechecking"
        }
        
        install_dir.mkdir(parents=True, exist_ok=True)
        cls._write_state_atomic(install_dir, state)
        
        # Thư mục Staging độc lập
        staging_dir = install_dir / "staging" / inst_id
        staging_webui_dir = staging_dir / "stable-diffusion-webui"
        staging_runtime_dir = staging_dir / "runtime"
        
        try:
            # Giai đoạn 3: Downloading / Cloning
            state["state"] = "downloading"
            state["last_completed_step"] = "waiting_user_consent"
            cls._write_state_atomic(install_dir, state)
            
            if progress_callback:
                progress_callback(0.2, "Đang clone AUTOMATIC1111 từ Git vào thư mục Staging...")
                
            staging_webui_dir.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                ["git", "clone", "--branch", cls.PINNED_COMMIT, "--single-branch", cls.PINNED_GIT_URL, str(staging_webui_dir)],
                check=True
            )
            
            # Giai đoạn 4: Verifying download
            state["state"] = "verifying_download"
            state["last_completed_step"] = "downloading"
            cls._write_state_atomic(install_dir, state)
            
            if progress_callback:
                progress_callback(0.35, "Xác minh mã nguồn tải về...")
                
            # Đảm bảo checkout đúng detached commit đã ghim
            subprocess.run(
                ["git", "checkout", "--detach", cls.PINNED_COMMIT],
                cwd=str(staging_webui_dir),
                check=True
            )
            
            # Giai đoạn 5: Creating Environment
            state["state"] = "creating_environment"
            state["last_completed_step"] = "verifying_download"
            cls._write_state_atomic(install_dir, state)
            
            if progress_callback:
                progress_callback(0.45, "Khởi tạo môi trường Python venv riêng biệt...")
                
            venv_dir = staging_runtime_dir / "venv"
            venv_dir.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)
            
            # Giai đoạn 6: Installing Dependencies
            state["state"] = "installing_dependencies"
            state["last_completed_step"] = "creating_environment"
            cls._write_state_atomic(install_dir, state)
            
            if progress_callback:
                progress_callback(0.6, "Cài đặt thư viện phụ thuộc (Torch CUDA, Requirements)...")
                
            pip_exe = venv_dir / "Scripts" / "pip.exe" if sys.platform == "win32" else venv_dir / "bin" / "pip"
            
            # Nâng cấp pip
            subprocess.run([str(pip_exe), "install", "--upgrade", "pip"], check=True)
            # Cài đặt PyTorch tương thích CUDA
            subprocess.run([str(pip_exe), "install", "torch", "torchvision", "--index-url", "https://download.pytorch.org/whl/cu118"], check=True)
            # Cài đặt requirements của WebUI
            req_file = staging_webui_dir / "requirements.txt"
            if req_file.exists():
                subprocess.run([str(pip_exe), "install", "-r", str(req_file)], check=True)
                
            # Giai đoạn 7: Configuring & Extension Allowlist
            state["state"] = "configuring"
            state["last_completed_step"] = "installing_dependencies"
            cls._write_state_atomic(install_dir, state)
            
            if progress_callback:
                progress_callback(0.8, "Áp dụng cấu hình và lọc Extension Allowlist...")
                
            # Lọc extension: Vô hiệu hóa extension lạ
            ext_dir = staging_webui_dir / "extensions"
            if ext_dir.exists() and ext_dir.is_dir():
                for item in ext_dir.iterdir():
                    if item.is_dir():
                        if item.name.lower() not in cls.ALLOWED_EXTENSIONS:
                            logger.warning(f"[SDInstaller] Vô hiệu hóa extension không nằm trong allowlist: {item.name}")
                            cls._safe_rename(item, item.with_name(f"{item.name}.disabled"))
            
            # Giai đoạn 8: Promote (Swapping Staging to Active)
            if progress_callback:
                progress_callback(0.9, "Đang tiến hành nâng cấp bản chạy chính thức (Promoting Staging)...")
                
            active_webui = install_dir / "stable-diffusion-webui"
            active_runtime = install_dir / "runtime"
            
            # Lưu trữ phiên bản cũ để rollback nếu gặp lỗi trong lúc swap
            rollback_dir = install_dir / "rollback" / inst_id
            rollback_webui = rollback_dir / "stable-diffusion-webui"
            rollback_runtime = rollback_dir / "runtime"
            
            rollback_dir.mkdir(parents=True, exist_ok=True)
            
            try:
                # Sao lưu bản cũ nếu có
                if active_webui.exists():
                    cls._safe_rename(active_webui, rollback_webui)
                if active_runtime.exists():
                    cls._safe_rename(active_runtime, rollback_runtime)
                    
                # Thao tác promote
                cls._safe_rename(staging_webui_dir, active_webui)
                cls._safe_rename(staging_runtime_dir, active_runtime)
                
                # Dọn dẹp thư mục staging
                cls._rmtree_safe(staging_dir)
                
            except Exception as promote_err:
                logger.error(f"[SDInstaller] Thao tác promote thất bại, đang kích hoạt rollback: {promote_err}")
                state["state"] = "rolling_back"
                cls._write_state_atomic(install_dir, state)
                
                # Phục hồi lại bản cũ
                if rollback_webui.exists():
                    cls._safe_rename(rollback_webui, active_webui)
                if rollback_runtime.exists():
                    cls._safe_rename(rollback_runtime, active_runtime)
                
                state["state"] = "rolled_back"
                cls._write_state_atomic(install_dir, state)
                raise promote_err
                
            # Đánh dấu hoàn tất
            state["state"] = "ready"
            state["installed"] = True
            state["last_completed_step"] = "configuring"
            cls._write_state_atomic(install_dir, state)
            
            if progress_callback:
                progress_callback(1.0, "Nâng cấp Stable Diffusion Local hoàn tất!")
                
            logger.info("[SDInstaller] Quá trình nâng cấp Stable Diffusion hoàn tất thành công.")
            return True
            
        except Exception as e:
            logger.error(f"[SDInstaller] Lỗi trong lúc cài đặt/nâng cấp: {e}")
            state["state"] = "failed_manual_action"
            state["last_error_id"] = str(uuid.uuid4())
            try:
                cls._write_state_atomic(install_dir, state)
            except Exception as state_err:
                logger.error(f"[SDInstaller] Không thể ghi nhận trạng thái lỗi: {state_err}")
                
            # Dọn dẹp staging nếu còn tồn tại
            cls._rmtree_safe(staging_dir)
            raise SDInstallerError(f"Cài đặt/nâng cấp Stable Diffusion thất bại: {e}") from e

    @classmethod
    def _write_state_atomic(cls, install_dir: Path, state_data: dict):
        """Ghi trạng thái cài đặt nguyên tử; tương thích Windows."""
        install_dir = Path(install_dir)
        state_data["updated_at_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        validate_data_schema(state_data, "sd_install_state")

        install_dir.mkdir(parents=True, exist_ok=True)
        if not install_dir.is_dir():
            raise SDInstallerError(f"Đường dẫn cài đặt không phải thư mục: {install_dir}")

        out_path = install_dir / "install_state.json"
        tmp_path = install_dir / f"install_state.{uuid.uuid4().hex}.tmp"

        if out_path.exists() and out_path.is_dir():
            raise SDInstallerError(
                f"'{out_path}' đang là thư mục. Hãy xóa/đổi tên thư mục đó rồi cài lại."
            )

        try:
            with tmp_path.open("w", encoding="utf-8", newline="\n") as f:
                json.dump(state_data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())

            last_error = None
            for attempt in range(3):
                try:
                    os.replace(str(tmp_path), str(out_path))
                    last_error = None
                    break
                except PermissionError as e:
                    last_error = e
                    if attempt < 2:
                        time.sleep(0.2 * (attempt + 1))
            if last_error is not None:
                raise last_error

            if os.name == "posix":
                try:
                    parent_fd = os.open(str(install_dir), os.O_RDONLY)
                    try:
                        os.fsync(parent_fd)
                    finally:
                        os.close(parent_fd)
                except OSError:
                    pass
        except Exception as e:
            logger.exception(
                "[SDInstaller] Ghi install_state.json thất bại "
                f"(dir={install_dir}, tmp={tmp_path}, out={out_path})"
            )
            try:
                if tmp_path.exists() and tmp_path.is_file():
                    tmp_path.unlink()
            except OSError:
                pass
            raise


# =====================================================================
# SDProcessManager (formerly core/sd_process_manager.py)
# =====================================================================

class SDProcessManager:
    """
    Quản lý vòng đời khởi động, theo dõi và tắt tiến trình con SD WebUI (Gate G4 / Mục 6.10).
    """
    @classmethod
    def check_port_open(cls, host: str, port: int) -> bool:
        """Kiểm tra xem cổng port có đang bị chiếm dụng không."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1.0)
            try:
                s.connect((host, port))
                return True
            except Exception:
                return False

    @classmethod
    def start_process(cls, install_dir: Path, port: int = 7860) -> int:
        """Khởi động tiến trình SD WebUI trên host loopback 127.0.0.1 (Mục 6.10)."""
        install_dir = Path(install_dir)
        state_file = install_dir / "install_state.json"
        
        if not state_file.exists():
            raise SDProcessError("Không tìm thấy tệp install_state.json. Hãy cài đặt SD trước.")
            
        with open(state_file, "r", encoding="utf-8") as f:
            state = json.load(f)
            
        # Kiểm tra port trước khi bắt đầu
        if cls.check_port_open("127.0.0.1", port):
            # Nếu cổng bị chiếm dụng, kiểm tra xem có phải bởi tiến trình của chính chúng ta đang chạy không
            current_pid = state.get("process_identity")
            if current_pid and psutil.pid_exists(current_pid):
                logger.info(f"[SDProcess] Server đã chạy với PID {current_pid} trên cổng {port}.")
                return current_pid
            else:
                raise SDProcessError(
                    f"Cổng {port} hiện đang bị chiếm dụng bởi một tiến trình khác không thuộc hệ thống của App. "
                    "Vui lòng giải phóng cổng này hoặc cấu hình cổng khác."
                )
            
        webui_dir = install_dir / "stable-diffusion-webui"
        python_exe = install_dir / "runtime" / "venv" / "Scripts" / "python.exe" if sys.platform == "win32" else install_dir / "runtime" / "venv" / "bin" / "python"
        
        if not python_exe.exists():
            raise SDProcessError(f"Không tìm thấy trình thông dịch Python trong venv: {python_exe}")
            
        launch_script = webui_dir / "launch.py"
        if not launch_script.exists():
            raise SDProcessError(f"Không tìm thấy file launch.py trong: {webui_dir}")
            
        # Chuẩn bị log file
        log_dir = install_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "webui.log"
        
        # Cấu hình startup profile cho GPU 4GB
        # Chỉ lắng nghe 127.0.0.1 (loopback) để bảo mật
        args = [
            str(python_exe),
            str(launch_script),
            "--api",
            "--medvram",
            "--port", str(port),
            "--listen", "127.0.0.1"
        ]
        
        logger.info(f"[SDProcess] Bắt đầu khởi động SD WebUI: {' '.join(args)}")
        
        # Mở file ghi stdout/stderr
        f_log = open(log_file, "w", encoding="utf-8")
        
        try:
            creation_flags = 0
            if sys.platform == "win32":
                creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP
                
            proc = subprocess.Popen(
                args,
                cwd=str(webui_dir),
                stdout=f_log,
                stderr=f_log,
                creationflags=creation_flags,
                stdin=subprocess.DEVNULL
            )
            
            # Đợi nhanh xem tiến trình có chết ngay lập tức không
            time.sleep(2)
            if proc.poll() is not None:
                f_log.close()
                with open(log_file, "r", encoding="utf-8", errors="ignore") as lf:
                    log_tail = lf.read()[-1000:]
                raise SDProcessError(f"Tiến trình SD WebUI dừng đột ngột ngay sau khi khởi động. Log tail:\n{log_tail}")
                
            # Đăng ký PID vào install_state
            state["running"] = True
            state["process_identity"] = proc.pid
            state["configured_port"] = port
            state["state"] = "starting"
            SDInstaller._write_state_atomic(install_dir, state)
            
            logger.info(f"[SDProcess] Tiến trình SD WebUI đã được khởi chạy với PID: {proc.pid}")
            return proc.pid
            
        except Exception as e:
            f_log.close()
            raise SDProcessError(f"Khởi động tiến trình SD WebUI thất bại: {e}")

    @classmethod
    def start_existing_process(cls, existing_dir: Path) -> None:
        """Khởi động bản AUTOMATIC1111 đã cài sẵn của người dùng trong console mới."""
        existing_dir = Path(existing_dir)
        bat_file = existing_dir / "webui-user.bat"
        if not bat_file.exists():
            bat_file = existing_dir / "webui.bat"
            
        if not bat_file.exists():
            raise SDProcessError("Không tìm thấy tệp webui-user.bat hoặc webui.bat trong thư mục chỉ định.")
            
        creation_flags = 0
        if sys.platform == "win32":
            creation_flags = subprocess.CREATE_NEW_CONSOLE
            
        logger.info(f"[SDProcess] Khởi động bản cài sẵn tại {bat_file.name}...")
        subprocess.Popen(
            [str(bat_file)],
            cwd=str(existing_dir),
            creationflags=creation_flags,
            shell=True
        )

    @classmethod
    def kill_process_by_port(cls, port: int) -> bool:
        """Tìm và đóng toàn bộ tiến trình đang chiếm cổng port được chỉ định."""
        killed = False
        for proc in psutil.process_iter(['pid', 'name']):
            try:
                for conn in proc.connections(kind='inet'):
                    if conn.laddr.port == port:
                        logger.info(f"[SDProcess] Đóng tiến trình PID {proc.pid} ({proc.info['name']}) đang chiếm cổng {port}...")
                        p = psutil.Process(proc.pid)
                        for child in p.children(recursive=True):
                            child.kill()
                        p.kill()
                        killed = True
            except Exception:
                pass
        return killed

    @classmethod
    def stop_process(cls, install_dir: Path) -> bool:
        """Dừng mềm-sang-cứng tiến trình SD WebUI theo PID tree (Mục 6.10)."""
        install_dir = Path(install_dir)
        state_file = install_dir / "install_state.json"
        
        if not state_file.exists():
            return False
            
        with open(state_file, "r", encoding="utf-8") as f:
            state = json.load(f)
            
        pid = state.get("process_identity")
        if not pid:
            logger.info("[SDProcess] Không tìm thấy PID tiến trình đang chạy trong install_state.json.")
            return False
            
        logger.info(f"[SDProcess] Bắt đầu tắt tiến trình SD WebUI PID: {pid}...")
        
        children = []
        try:
            parent = psutil.Process(pid)
            children = parent.children(recursive=True)
            processes = [parent] + children
        except psutil.NoSuchProcess:
            logger.info(f"[SDProcess] Tiến trình PID {pid} đã dừng trước đó.")
            processes = []
            
        # Dừng mềm (terminate)
        for p in processes:
            try:
                p.terminate()
            except Exception:
                pass
                 
        # Chờ tối đa 10 giây grace period
        if processes:
            gone, alive = psutil.wait_procs(processes, timeout=10)
            if alive:
                logger.warning(f"[SDProcess] Có {len(alive)} tiến trình chưa dừng, thực thi force-kill...")
                for p in alive:
                    try:
                        p.kill()
                    except Exception:
                        pass
                        
        # Cập nhật trạng thái
        state["running"] = False
        state["process_identity"] = None
        state["ready"] = False
        state["healthy"] = False
        state["state"] = "cancelled"
        SDInstaller._write_state_atomic(install_dir, state)
        
        logger.info("[SDProcess] Đã giải phóng hoàn toàn tiến trình SD WebUI.")
        return True


# =====================================================================
# SDModelManager (formerly core/sd_model_manager.py)
# =====================================================================

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
