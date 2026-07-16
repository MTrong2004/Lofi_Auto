"""
AI FILE NOTE - RENDER WORKER PROCESS
Chức năng chính:
- Rút các Job từ Scheduler và thực thi tác vụ xử lý đồ họa/render video.
- Tạo tiến trình con xử lý độc lập và theo dõi mã thoát (exit codes).
- Hỗ trợ cơ chế hủy tác vụ khẩn cấp (Cancel Job), kết thúc đệ quy cây tiến trình (PID tree) để giải phóng tài nguyên.
Đầu vào chính:
- Tên Worker (worker_id), thông số Job được gán.
Đầu ra chính:
- Tệp video phân đoạn kết quả, trạng thái Job cập nhật trong DB.
API được file khác sử dụng:
- Lớp `RenderWorker`, `JobCancelledError`.
Phụ thuộc quan trọng:
- core.resource_scheduler, core.db, psutil, subprocess
Lưu ý khi sửa:
- Thao tác kill tiến trình con bắt buộc phải thực hiện đệ quy trên toàn bộ cây tiến trình (`parent.children()`) để không bỏ sót các tiến trình FFmpeg chạy mồ côi.
"""
import os
import sys
import time
import subprocess
import threading
from pathlib import Path
from datetime import datetime, timezone, timedelta
import psutil

# Đảm bảo import được config.py từ thư mục cha
sys.path.append(str(Path(__file__).parent.parent))
import config
from core.db import get_db_connection
from core.resource_scheduler import ResourceScheduler

class JobCancelledError(Exception):
    """Lỗi ném ra khi công việc bị hủy giữa chừng."""
    pass

class RenderWorker:
    def __init__(self, worker_id: str = None):
        self.worker_id = worker_id or f"worker_{os.getpid()}_{os.urandom(4).hex()}"
        self.active_job_id = None
        self._stop_heartbeat = threading.Event()
        self._heartbeat_thread = None

    def start_heartbeat(self, job_id: str, lease_seconds: int = 60):
        """Khởi động thread gia hạn lease cho job đang chạy."""
        self._stop_heartbeat.clear()
        self.active_job_id = job_id
        
        def heartbeat_loop():
            conn = get_db_connection()
            try:
                while not self._stop_heartbeat.wait(10.0): # gia hạn mỗi 10 giây
                    now_dt = datetime.now(timezone.utc)
                    expires_str = (now_dt + timedelta(seconds=lease_seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")
                    with conn:
                        conn.execute("""
                        UPDATE jobs
                        SET lease_expires_at_utc = ?, updated_at_utc = ?
                        WHERE job_id = ? AND owner_id = ?;
                        """, (expires_str, now_dt.strftime("%Y-%m-%dT%H:%M:%SZ"), job_id, self.worker_id))
            except Exception as e:
                print(f"[WorkerHeartbeat Error] {e}")
            finally:
                conn.close()
                
        self._heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
        self._heartbeat_thread.start()

    def stop_heartbeat(self):
        """Dừng thread gia hạn lease."""
        self._stop_heartbeat.set()
        if self._heartbeat_thread:
            self._heartbeat_thread.join(timeout=2.0)
        self.active_job_id = None
        self._heartbeat_thread = None

    @staticmethod
    def kill_process_tree(pid: int, grace_period: float = 2.0):
        """
        Dừng mềm và buộc dừng toàn bộ Process Tree theo đúng quy tắc bảo mật Mục 22.5.
        """
        try:
            parent = psutil.Process(pid)
            children = parent.children(recursive=True)
            
            # 1. Gửi tín hiệu dừng mềm (terminate)
            for child in children:
                try:
                    child.terminate()
                except psutil.NoSuchProcess:
                    pass
            try:
                parent.terminate()
            except psutil.NoSuchProcess:
                pass
                
            # 2. Chờ thời gian grace period
            gone, alive = psutil.wait_procs(children + [parent], timeout=grace_period)
            
            # 3. Buộc dừng (kill) các tiến trình cứng đầu còn sót lại
            for survivor in alive:
                try:
                    survivor.kill()
                    print(f"[Worker] Force killed surviving process PID: {survivor.pid}")
                except psutil.NoSuchProcess:
                    pass
        except psutil.NoSuchProcess:
            pass
        except Exception as e:
            print(f"[Worker Error] Failed to kill process tree for PID {pid}: {e}")

    def check_cancellation(self, job_id: str) -> bool:
        """Kiểm tra xem người dùng có gửi tín hiệu hủy công việc (cancelling) hay không."""
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT job_status FROM jobs WHERE job_id = ?;", (job_id,))
            row = cursor.fetchone()
            if row and row[0] == "cancelling":
                return True
            return False
        finally:
            conn.close()

    def execute_command_with_cancellation_check(self, cmd: list[str], job_id: str, 
                                                cwd: str = None, progress_parser=None) -> subprocess.CompletedProcess:
        """
        Chạy câu lệnh tiến trình con, định kỳ kiểm tra tín hiệu hủy từ DB.
        Nếu nhận tín hiệu hủy, hạ gục tiến trình con và quăng lỗi JobCancelledError.
        """
        print(f"[Worker] Running cmd: {' '.join(cmd)}")
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=cwd,
            encoding="utf-8",
            errors="ignore",
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
        )
        
        try:
            # Loop theo dõi tiến trình con
            while process.poll() is None:
                # 1. Kiểm tra tín hiệu hủy từ DB mỗi 2 giây
                if self.check_cancellation(job_id):
                    print(f"[Worker] Cancellation signal received for job '{job_id}'. Killing process tree...")
                    self.kill_process_tree(process.pid)
                    raise JobCancelledError(f"Job {job_id} was cancelled by user.")
                
                # 2. Đọc output log nếu có hàm phân tích tiến độ
                if progress_parser and process.stdout:
                    # Đọc không blocking
                    line = process.stdout.readline()
                    if line:
                        progress_parser(line)
                        
                time.sleep(2.0)
                
            # Đọc nốt stdout/stderr khi kết thúc
            stdout, stderr = process.communicate()
            
            if process.returncode != 0:
                raise subprocess.CalledProcessError(process.returncode, cmd, stdout, stderr)
                
            return subprocess.CompletedProcess(cmd, process.returncode, stdout, stderr)
            
        except Exception as e:
            # Đảm bảo tiến trình con luôn được dọn dẹp nếu có exception ném ra
            if process.poll() is None:
                self.kill_process_tree(process.pid)
            raise e

    def run_job(self, job: dict):
        """Xử lý thực thi một job cụ thể."""
        job_id = job["job_id"]
        job_type = job["job_type"]
        project_id = job["project_id"]
        
        print(f"[Worker] Starting execution of job '{job_id}' (type: {job_type}) for project '{project_id}'")
        self.start_heartbeat(job_id)
        
        try:
            # Giả lập hoặc định tuyến thực thi theo loại job
            if job_type == "test_job":
                # Chạy câu lệnh ping/echo đơn giản làm mẫu
                cmd = ["ping", "127.0.0.1", "-n", "5"] if sys.platform == "win32" else ["sleep", "5"]
                self.execute_command_with_cancellation_check(cmd, job_id)
                ResourceScheduler.update_job_status(job_id, "completed", "done")
                print(f"[Worker] Completed job '{job_id}' successfully.")
            else:
                # Với các job thực tế, chúng ta sẽ gọi logic chuyên dụng (ví dụ step4_render)
                # Tạm thời cập nhật sang failed do chưa đấu nối logic đầy đủ ở bước này
                raise NotImplementedError(f"Job type '{job_type}' handler is not implemented yet in the worker.")
                
        except JobCancelledError:
            ResourceScheduler.update_job_status(job_id, "cancelled", "cancelled", "Cong viec bi huy boi nguoi dung.")
            print(f"[Worker] Job '{job_id}' was cancelled and marked in DB.")
        except Exception as e:
            ResourceScheduler.update_job_status(job_id, "failed", "failed", str(e))
            print(f"[Worker Error] Job '{job_id}' failed: {e}")
        finally:
            self.stop_heartbeat()

    def run_loop(self, once: bool = False):
        """Vòng lặp nhận việc và xử lý của Worker."""
        print(f"[Worker] Worker '{self.worker_id}' is online and polling for jobs...")
        while True:
            try:
                job = ResourceScheduler.claim_job(self.worker_id)
                if job:
                    self.run_job(job)
                else:
                    if once:
                        break
                    time.sleep(5.0) # ngủ 5s nếu hàng đợi rỗng
            except KeyboardInterrupt:
                print("[Worker] Shutting down...")
                break
            except Exception as e:
                print(f"[Worker Poll Error] {e}")
                time.sleep(5.0)

if __name__ == "__main__":
    # Test nhanh Worker và Khả năng Hủy
    import core.db
    core.db.init_db()
    
    p_id = "test_wrk_prj"
    conn = get_db_connection()
    with conn:
        conn.execute("DELETE FROM jobs WHERE project_id = ?;", (p_id,))
        conn.execute("DELETE FROM projects WHERE project_id = ?;", (p_id,))
    conn.close()
    
    from core.project_manager import ProjectManager
    ProjectManager.create_project(p_id)
    
    # 1. Gửi test job
    job_info = ResourceScheduler.submit_job(p_id, "test_job", "wrk_key_1", {}, {})
    
    # 2. Khởi chạy worker chạy thử
    worker = RenderWorker("test_worker_instance")
    
    # Giả lập thread hủy job sau 2 giây
    def cancel_after_2s():
        time.sleep(2.0)
        conn = get_db_connection()
        with conn:
            conn.execute("UPDATE jobs SET job_status = 'cancelling' WHERE project_id = ?;", (p_id,))
        conn.close()
        print("[Test] Injected 'cancelling' status into DB!")
        
    t = threading.Thread(target=cancel_after_2s)
    t.start()
    
    # Chạy vòng lặp worker 1 lần
    worker.run_loop(once=True)
    
    # Kiểm tra trạng thái cuối cùng của job
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT job_status FROM jobs WHERE project_id = ?;", (p_id,))
    final_status = cursor.fetchone()[0]
    conn.close()
    
    print("Final Job Status in DB (Expected 'cancelled'):", final_status)
    
    # Cleanup
    conn = get_db_connection()
    with conn:
        conn.execute("DELETE FROM jobs WHERE project_id = ?;", (p_id,))
        conn.execute("DELETE FROM projects WHERE project_id = ?;", (p_id,))
    conn.close()
    p_json = ProjectManager.get_project_json_path(p_id)
    if p_json.exists():
        p_json.unlink()
