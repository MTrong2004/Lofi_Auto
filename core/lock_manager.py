"""
AI FILE NOTE - HARDWARE RESOURCE LOCK MANAGER
Chức năng chính:
- Cung cấp cơ chế khóa tài nguyên độc quyền (locks) qua SQLite để quản lý GPU/CPU khi nhiều tác vụ song song cùng chạy.
- Sử dụng cơ chế Fencing Tokens (lớn dần) để phát hiện và ngăn chặn các tiến trình ghi đè xung đột.
- Tự động kiểm tra sức khỏe tiến trình (qua PID) và dọn dẹp các khóa cũ của tiến trình đã chết (stale locks).
Đầu vào chính:
- Tên tài nguyên cần khóa (ví dụ: `gpu`, `cpu`), định danh owner, thời hạn khóa (lease time).
Đầu ra chính:
- Fencing token nếu khóa thành công, hoặc trả lỗi `LockAcquisitionError` / Trả về `None` nếu thất bại.
API được file khác sử dụng:
- Lớp `LockManager`, `ResourceLock`, `LockAcquisitionError`.
Phụ thuộc quan trọng:
- sqlite3, psutil, core.db
Lưu ý khi sửa:
- Giữ logic thread-safety của cơ chế khóa SQLite và đảm bảo lock luôn được giải phóng sau khi xong việc (`release_lock`).
"""
import os
import sys
import time
import threading
from datetime import datetime, timezone, timedelta
import sqlite3
from pathlib import Path
import psutil

# Đảm bảo import được config.py từ thư mục cha
sys.path.append(str(Path(__file__).parent.parent))
import config
from core.db import get_db_connection

class LockAcquisitionError(Exception):
    """Lỗi khi không thể chiếm khóa tài nguyên do khóa đang bận hoặc tranh chấp."""
    pass

class LockManager:
    @staticmethod
    def get_process_start_time() -> str:
        """Lấy thời điểm bắt đầu của tiến trình hiện tại dưới dạng ISO 8601 UTC."""
        try:
            p = psutil.Process(os.getpid())
            # create_time trả về epoch time (local)
            dt = datetime.fromtimestamp(p.create_time(), timezone.utc)
            return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            # Fallback nếu psutil gặp lỗi
            return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    @classmethod
    def acquire_lock(cls, resource_type: str, resource_id: str, owner_id: str, lease_seconds: int = 30) -> int:
        """
        Chiếm khóa tài nguyên. 
        Nếu chiếm thành công, trả về fencing_token (int). 
        Nếu khóa đang bị chiếm bởi một worker khác và chưa hết hạn, quăng lỗi LockAcquisitionError.
        """
        now_dt = datetime.now(timezone.utc)
        now_str = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        expires_str = (now_dt + timedelta(seconds=lease_seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")
        pid = os.getpid()
        proc_start_str = cls.get_process_start_time()

        conn = get_db_connection()
        try:
            with conn:
                cursor = conn.cursor()
                cursor.execute("""
                SELECT lock_id, owner_id, pid, process_started_at_utc, lease_expires_at_utc, fencing_token
                FROM resource_locks
                WHERE resource_type = ? AND resource_id = ?;
                """, (resource_type, resource_id))
                row = cursor.fetchone()

                if row:
                    lock_id, cur_owner, cur_pid, cur_proc_start, cur_expires, cur_fencing = row
                    
                    # Kiểm tra xem khóa hiện tại đã hết hạn lease chưa
                    cur_expires_dt = datetime.strptime(cur_expires, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                    
                    # Nếu cùng owner_id và cùng pid/start_time, cho phép re-acquire
                    if cur_owner == owner_id and cur_pid == pid and cur_proc_start == proc_start_str:
                        # Tái gia hạn, giữ nguyên fencing_token
                        conn.execute("""
                        UPDATE resource_locks
                        SET heartbeat_at_utc = ?, lease_expires_at_utc = ?
                        WHERE lock_id = ?;
                        """, (now_str, expires_str, lock_id))
                        return cur_fencing
                    
                    # Nếu lease chưa hết hạn, và không phải chủ cũ
                    if now_dt < cur_expires_dt:
                        # Kiểm tra xem tiến trình chủ khóa còn sống trên máy local không
                        is_alive = False
                        try:
                            if psutil.pid_exists(cur_pid):
                                p = psutil.Process(cur_pid)
                                cur_proc_start_dt = datetime.fromtimestamp(p.create_time(), timezone.utc)
                                cur_proc_start_str_actual = cur_proc_start_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                                if cur_proc_start_str_actual == cur_proc_start:
                                    is_alive = True
                        except Exception:
                            pass

                        if is_alive:
                            raise LockAcquisitionError(
                                f"Resource {resource_type}:{resource_id} is locked by active owner '{cur_owner}' (PID: {cur_pid}) until {cur_expires}."
                            )
                    
                    # Nếu lease đã hết hạn hoặc tiến trình giữ khóa đã chết, chiếm quyền (Lock Takeover)
                    new_fencing = cur_fencing + 1
                    conn.execute("""
                    UPDATE resource_locks
                    SET owner_id = ?, pid = ?, process_started_at_utc = ?, acquired_at_utc = ?, heartbeat_at_utc = ?, lease_expires_at_utc = ?, fencing_token = ?
                    WHERE lock_id = ?;
                    """, (owner_id, pid, proc_start_str, now_str, now_str, expires_str, new_fencing, lock_id))
                    return new_fencing
                else:
                    # Chưa có khóa, tạo mới hoàn toàn
                    lock_id = os.urandom(16).hex()
                    fencing_token = 1
                    conn.execute("""
                    INSERT INTO resource_locks (lock_id, resource_type, resource_id, owner_id, pid, process_started_at_utc, acquired_at_utc, heartbeat_at_utc, lease_expires_at_utc, fencing_token)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """, (lock_id, resource_type, resource_id, owner_id, pid, proc_start_str, now_str, now_str, expires_str, fencing_token))
                    return fencing_token
        finally:
            conn.close()

    @classmethod
    def renew_lock(cls, resource_type: str, resource_id: str, owner_id: str, lease_seconds: int = 30) -> bool:
        """Gia hạn thời gian hết hạn lease của khóa."""
        now_dt = datetime.now(timezone.utc)
        now_str = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        expires_str = (now_dt + timedelta(seconds=lease_seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")
        pid = os.getpid()
        proc_start_str = cls.get_process_start_time()

        conn = get_db_connection()
        try:
            with conn:
                cursor = conn.cursor()
                cursor.execute("""
                SELECT owner_id, pid, process_started_at_utc
                FROM resource_locks
                WHERE resource_type = ? AND resource_id = ?;
                """, (resource_type, resource_id))
                row = cursor.fetchone()
                if not row:
                    return False
                
                cur_owner, cur_pid, cur_proc_start = row
                # Chỉ cho phép gia hạn nếu đúng chủ sở hữu và tiến trình sở hữu còn trùng khớp
                if cur_owner == owner_id and cur_pid == pid and cur_proc_start == proc_start_str:
                    conn.execute("""
                    UPDATE resource_locks
                    SET heartbeat_at_utc = ?, lease_expires_at_utc = ?
                    WHERE resource_type = ? AND resource_id = ?;
                    """, (now_str, expires_str, resource_type, resource_id))
                    return True
                return False
        finally:
            conn.close()

    @classmethod
    def release_lock(cls, resource_type: str, resource_id: str, owner_id: str) -> bool:
        """Giải phóng khóa tài nguyên."""
        pid = os.getpid()
        proc_start_str = cls.get_process_start_time()

        conn = get_db_connection()
        try:
            with conn:
                cursor = conn.cursor()
                cursor.execute("""
                SELECT owner_id, pid, process_started_at_utc
                FROM resource_locks
                WHERE resource_type = ? AND resource_id = ?;
                """, (resource_type, resource_id))
                row = cursor.fetchone()
                if not row:
                    return True # Khóa không tồn tại thì coi như giải phóng thành công
                
                cur_owner, cur_pid, cur_proc_start = row
                # Chỉ cho phép giải phóng nếu đúng chủ sở hữu
                if cur_owner == owner_id and cur_pid == pid and cur_proc_start == proc_start_str:
                    conn.execute("""
                    DELETE FROM resource_locks
                    WHERE resource_type = ? AND resource_id = ?;
                    """, (resource_type, resource_id))
                    return True
                return False
        finally:
            conn.close()

    @classmethod
    def run_recovery(cls):
        """
        Trình phục hồi hệ thống (Recovery Manager).
        Tìm các lock hết hạn lease hoặc các lock giữ bởi tiến trình đã chết.
        Tự động chuyển các job tương ứng sang trạng thái 'interrupted' hoặc 'recovering'.
        """
        now_dt = datetime.now(timezone.utc)
        now_str = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        
        conn = get_db_connection()
        try:
            locks_to_reclaim = []
            with conn:
                cursor = conn.cursor()
                cursor.execute("""
                SELECT lock_id, resource_type, resource_id, owner_id, pid, process_started_at_utc, lease_expires_at_utc
                FROM resource_locks;
                """)
                rows = cursor.fetchall()
                
                for row in rows:
                    lock_id, r_type, r_id, owner_id, pid, proc_start, lease_expires = row
                    
                    # 1. Kiểm tra hết hạn lease
                    expires_dt = datetime.strptime(lease_expires, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                    is_stale = (now_dt > expires_dt)
                    
                    # 2. Nếu lease chưa hết, kiểm tra PID còn sống không
                    if not is_stale:
                        is_alive = False
                        try:
                            if psutil.pid_exists(pid):
                                p = psutil.Process(pid)
                                actual_start_dt = datetime.fromtimestamp(p.create_time(), timezone.utc)
                                actual_start_str = actual_start_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                                if actual_start_str == proc_start:
                                    is_alive = True
                        except Exception:
                            pass
                        
                        if not is_alive:
                            is_stale = True # Tiến trình đã chết bất thường
                            
                    if is_stale:
                        locks_to_reclaim.append((lock_id, r_type, r_id, owner_id))
            
            # Reclaim các lock stale
            for lock_id, r_type, r_id, owner_id in locks_to_reclaim:
                with conn:
                    # Xóa lock
                    conn.execute("DELETE FROM resource_locks WHERE lock_id = ?;", (lock_id,))
                    print(f"[Recovery] Reclaimed stale lock {lock_id} on {r_type}:{r_id} owned by {owner_id}")
                    
                    # Nếu là project lock, tìm các running job của project đó để cập nhật sang interrupted
                    if r_type == "project":
                        cursor = conn.cursor()
                        cursor.execute("""
                        SELECT job_id, job_status FROM jobs
                        WHERE project_id = ? AND job_status IN ('running', 'verifying', 'queued_waiting_resource');
                        """, (r_id,))
                        active_jobs = cursor.fetchall()
                        
                        for job_id, job_status in active_jobs:
                            conn.execute("""
                            UPDATE jobs
                            SET job_status = 'interrupted', updated_at_utc = ?
                            WHERE job_id = ?;
                            """, (now_str, job_id))
                            
                            # Ghi nhận record lỗi
                            error_id = os.urandom(16).hex()
                            conn.execute("""
                            INSERT INTO error_records (error_id, job_id, error_code, category, step, message, retryable, fallback_available, suggested_action, occurred_at_utc)
                            VALUES (?, ?, 'JOB_INTERRUPTED_LEASE_EXPIRED', 'state', 'recovery', 'Cong viec bi gian doan do worker mat kết noi hoac bi crash.', 1, 0, 'Khoi dong lai cong viec tu checkpoint', ?);
                            """, (error_id, job_id, now_str))
                            
                            print(f"[Recovery] Marked job {job_id} as interrupted")
                            
        finally:
            conn.close()

class ResourceLock:
    """Context Manager cho Resource Lock kết hợp Heartbeat chạy nền."""
    def __init__(self, resource_type: str, resource_id: str, owner_id: str, lease_seconds: int = 30, heartbeat_seconds: int = 10):
        self.resource_type = resource_type
        self.resource_id = resource_id
        self.owner_id = owner_id
        self.lease_seconds = lease_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.fencing_token = None
        self._stop_event = threading.Event()
        self._heartbeat_thread = None

    def __enter__(self):
        # 1. Chiếm lock
        self.fencing_token = LockManager.acquire_lock(
            self.resource_type, self.resource_id, self.owner_id, self.lease_seconds
        )
        # 2. Khởi chạy Heartbeat thread
        self._stop_event.clear()
        self._heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._heartbeat_thread.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        # 1. Stop heartbeat thread
        self._stop_event.set()
        if self._heartbeat_thread:
            self._heartbeat_thread.join(timeout=2.0)
        # 2. Giải phóng lock
        LockManager.release_lock(self.resource_type, self.resource_id, self.owner_id)

    def _heartbeat_loop(self):
        while not self._stop_event.wait(self.heartbeat_seconds):
            try:
                success = LockManager.renew_lock(
                    self.resource_type, self.resource_id, self.owner_id, self.lease_seconds
                )
                if not success:
                    print(f"[LockHeartbeat Warning] Failed to renew lock for {self.resource_type}:{self.resource_id}. Lock may have been taken over.")
            except Exception as e:
                print(f"[LockHeartbeat Error] Exception during lock renewal: {e}")

if __name__ == "__main__":
    # Test nhanh Lock & Heartbeat
    import core.db
    core.db.init_db()

    res_type = "project"
    res_id = "test_lock_prj"
    owner_1 = "worker_alpha"
    owner_2 = "worker_beta"

    # Xóa lock cũ nếu tồn tại
    conn = get_db_connection()
    with conn:
        conn.execute("DELETE FROM resource_locks WHERE resource_type = ? AND resource_id = ?;", (res_type, res_id))
    conn.close()

    print("Test 1: Acquired lock by alpha...")
    token1 = LockManager.acquire_lock(res_type, res_id, owner_1, lease_seconds=5)
    print("Fencing Token:", token1)

    print("Test 2: Beta tries to acquire same lock (should fail)...")
    try:
        LockManager.acquire_lock(res_type, res_id, owner_2, lease_seconds=5)
        print("FAIL: Beta acquired lock successfully while it should have failed.")
    except LockAcquisitionError as e:
        print("SUCCESS: Beta blocked by alpha. Error msg:", str(e))

    print("Test 3: Wait 6s for lease to expire and beta tries to acquire...")
    time.sleep(6)
    try:
        token2 = LockManager.acquire_lock(res_type, res_id, owner_2, lease_seconds=5)
        print("SUCCESS: Beta took over expired lock. New Fencing Token:", token2)
    except LockAcquisitionError as e:
        print("FAIL: Beta blocked even after lease expired. Error:", str(e))

    # Cleanup
    LockManager.release_lock(res_type, res_id, owner_2)
    print("Cleanup done.")
