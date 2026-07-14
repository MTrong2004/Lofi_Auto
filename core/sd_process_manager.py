import os
import sys
import subprocess
import logging
import time
import socket
import psutil
from pathlib import Path
from datetime import datetime, timezone
import config
from core.sd_installer import SDInstaller

logger = logging.getLogger("lofi_automation")

class SDProcessError(Exception):
    """Lỗi quản lý tiến trình Stable Diffusion."""
    pass

class SDProcessManager:
    """
    Quản lý vòng đời khởi động, theo dõi và tắt tiến trình con SD WebUI (Gate G4 / Mục 6.10).
    """
    @classmethod
    def check_port_open(cls, host: str, port: int) -> bool:
        """Kiểm tra xem cổng port có đang bị chiếm dụng không."""
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1.0)
        try:
            s.connect((host, port))
            s.close()
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
            
        if cls.check_port_open("127.0.0.1", port):
            raise SDProcessError(f"Cổng {port} hiện đang bị chiếm dụng. Vui lòng giải phóng cổng trước.")
            
        webui_dir = install_dir / "stable-diffusion-webui"
        python_exe = install_dir / "runtime" / "venv" / "Scripts" / "python.exe" if sys.platform == "win32" else install_dir / "runtime" / "venv" / "bin" / "python"
        
        if not python_exe.exists():
            raise SDProcessError(f"Không tìm thấy trình thông dịch Python trong venv: {python_exe}")
            
        # Đường dẫn tới launch.py chính thức của AUTOMATIC1111
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
            # Khởi động subprocess với Group ID riêng
            creation_flags = 0
            if sys.platform == "win32":
                # Tạo process group riêng trên Windows để dễ tắt
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
        
        # 1. Tìm toàn bộ process tree con
        children = []
        try:
            parent = psutil.Process(pid)
            children = parent.children(recursive=True)
            processes = [parent] + children
        except psutil.NoSuchProcess:
            logger.info(f"[SDProcess] Tiến trình PID {pid} đã dừng trước đó.")
            processes = []
            
        # 2. Dừng mềm (terminate)
        for p in processes:
            try:
                p.terminate()
            except Exception:
                pass
                
        # Chờ tối đa 10 giây grace period
        if processes:
            gone, alive = psutil.wait_procs(processes, timeout=10)
            if alive:
                # 3. Buộc dừng cứng (kill)
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

import json
