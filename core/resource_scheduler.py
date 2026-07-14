import os
import sys
import json
import uuid
import hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path
import sqlite3

# Đảm bảo import được config.py từ thư mục cha
sys.path.append(str(Path(__file__).parent.parent))
import config
from core.db import get_db_connection

class IdempotencyConflictError(Exception):
    pass

class ResourceScheduler:
    # Bản đồ phân loại tài nguyên của từng loại công việc
    JOB_RESOURCE_MAP = {
        "sd_generate": "gpu_heavy",
        "depth_estimate": "gpu_heavy",
        "sam_segment": "gpu_heavy",
        "ai_upscale": "gpu_heavy",
        "render_nvenc": "gpu_encode",
        "render_cpu": "cpu_heavy",
        "download_audio": "network",
        "download_image": "network",
        "concat_video": "disk_heavy"
    }

    @classmethod
    def get_resource_category(cls, job_type: str) -> str:
        return cls.JOB_RESOURCE_MAP.get(job_type, "lightweight")

    @classmethod
    def submit_job(cls, project_id: str, job_type: str, idempotency_key: str, 
                   request_payload: dict, config_snapshot: dict) -> dict:
        """
        Gửi một job mới vào hàng đợi SQLite có hỗ trợ Idempotency.
        Nếu cùng project_id và idempotency_key đã tồn tại:
        - So sánh hash của payload. Nếu khớp, tái sử dụng job cũ (nếu chưa hoàn thành/thất bại).
        - Nếu không khớp, ném lỗi IdempotencyConflictError.
        """
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        
        # Tính toán hash của payload
        payload_str = json.dumps(request_payload, sort_keys=True, ensure_ascii=False)
        payload_hash = hashlib.sha256(payload_str.encode("utf-8")).hexdigest()
        
        config_snapshot_str = json.dumps(config_snapshot, sort_keys=True, ensure_ascii=False)
        
        conn = get_db_connection()
        try:
            with conn:
                cursor = conn.cursor()
                cursor.execute("""
                SELECT job_id, job_status, request_payload_hash, job_type
                FROM jobs
                WHERE project_id = ? AND idempotency_key = ?;
                """, (project_id, idempotency_key))
                row = cursor.fetchone()
                
                if row:
                    existing_job_id, status, existing_hash, existing_type = row
                    if existing_hash != payload_hash:
                        raise IdempotencyConflictError(
                            f"Idempotency conflict: A job already exists with key '{idempotency_key}' but different payload."
                        )
                    print(f"[Scheduler] Reusing existing job '{existing_job_id}' (status: {status})")
                    return {
                        "job_id": existing_job_id,
                        "job_status": status,
                        "job_type": existing_type,
                        "is_new": False
                    }
                
                # Tạo job mới
                job_id = str(uuid.uuid4())
                conn.execute("""
                INSERT INTO jobs (job_id, project_id, job_type, job_status, current_step, idempotency_key, request_payload_hash, owner_id, lease_expires_at_utc, created_at_utc, updated_at_utc, config_snapshot)
                VALUES (?, ?, ?, 'queued', 'preparing', ?, ?, NULL, NULL, ?, ?, ?);
                """, (job_id, project_id, job_type, idempotency_key, payload_hash, now_str, now_str, config_snapshot_str))
                
                print(f"[Scheduler] Submitted new job '{job_id}' (type: {job_type})")
                return {
                    "job_id": job_id,
                    "job_status": "queued",
                    "job_type": job_type,
                    "is_new": True
                }
        finally:
            conn.close()

    @classmethod
    def claim_job(cls, worker_id: str, lease_seconds: int = 60) -> dict | None:
        """
        Worker gọi để nhận việc. 
        Ràng buộc tài nguyên: trên GPU 4GB, tại một thời điểm chỉ cho phép 1 job gpu_heavy OR 1 job gpu_encode chạy.
        Trả về job dict nếu nhận thành công, ngược lại trả về None.
        """
        now_dt = datetime.now(timezone.utc)
        now_str = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        expires_str = (now_dt + timedelta(seconds=lease_seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")
        
        conn = get_db_connection()
        try:
            with conn:
                cursor = conn.cursor()
                
                # 1. Kiểm tra xem có job GPU nào đang chạy không
                cursor.execute("""
                SELECT job_id, job_type FROM jobs
                WHERE job_status IN ('running', 'verifying');
                """)
                running_jobs = cursor.fetchall()
                
                gpu_busy = False
                for rj_id, rj_type in running_jobs:
                    cat = cls.get_resource_category(rj_type)
                    if cat in ("gpu_heavy", "gpu_encode"):
                        gpu_busy = True
                        break
                
                # 2. Lấy tất cả các job đang ở trạng thái chờ xếp hàng (queued / queued_waiting_resource)
                # Ưu tiên theo thứ tự thời gian tạo tăng dần
                cursor.execute("""
                SELECT job_id, project_id, job_type, job_status
                FROM jobs
                WHERE job_status IN ('queued', 'queued_waiting_resource')
                ORDER BY created_at_utc ASC;
                """)
                candidate_jobs = cursor.fetchall()
                
                for job_id, project_id, job_type, status in candidate_jobs:
                    cat = cls.get_resource_category(job_type)
                    
                    # Nếu là job GPU nhưng GPU đang bận
                    if cat in ("gpu_heavy", "gpu_encode") and gpu_busy:
                        # Chuyển sang queued_waiting_resource nếu chưa ở trạng thái đó
                        if status == "queued":
                            conn.execute("""
                            UPDATE jobs
                            SET job_status = 'queued_waiting_resource', updated_at_utc = ?
                            WHERE job_id = ?;
                            """, (now_str, job_id))
                        continue # Bỏ qua để tìm job khác không dùng GPU (ví dụ tải nhạc, audio processing, cpu render)
                    
                    # Nếu thỏa mãn điều kiện hoặc là job CPU/Lightweight
                    conn.execute("""
                    UPDATE jobs
                    SET job_status = 'running', owner_id = ?, lease_expires_at_utc = ?, updated_at_utc = ?
                    WHERE job_id = ?;
                    """, (worker_id, expires_str, now_str, job_id))
                    
                    # Đồng bộ sang workflow status của dự án nếu cần thiết
                    cursor.execute("SELECT config_snapshot FROM jobs WHERE job_id = ?;", (job_id,))
                    config_snap = cursor.fetchone()[0]
                    
                    print(f"[Scheduler] Worker '{worker_id}' successfully claimed job '{job_id}' (type: {job_type})")
                    return {
                        "job_id": job_id,
                        "project_id": project_id,
                        "job_type": job_type,
                        "job_status": "running",
                        "config_snapshot": json.loads(config_snap) if config_snap else {}
                    }
                    
            return None
        finally:
            conn.close()

    @staticmethod
    def update_job_status(job_id: str, job_status: str, current_step: str = None, 
                          reason: str = None) -> bool:
        """Cập nhật tiến độ của một công việc."""
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        conn = get_db_connection()
        try:
            with conn:
                cursor = conn.cursor()
                cursor.execute("SELECT job_id FROM jobs WHERE job_id = ?;", (job_id,))
                if not cursor.fetchone():
                    return False
                
                if current_step:
                    conn.execute("""
                    UPDATE jobs
                    SET job_status = ?, current_step = ?, updated_at_utc = ?
                    WHERE job_id = ?;
                    """, (job_status, current_step, now_str, job_id))
                else:
                    conn.execute("""
                    UPDATE jobs
                    SET job_status = ?, updated_at_utc = ?
                    WHERE job_id = ?;
                    """, (job_status, now_str, job_id))
                    
                # Ghi nhận record lỗi nếu job thất bại
                if job_status == "failed" and reason:
                    error_id = os.urandom(16).hex()
                    conn.execute("""
                    INSERT INTO error_records (error_id, job_id, error_code, category, step, message, retryable, fallback_available, suggested_action, occurred_at_utc)
                    VALUES (?, ?, 'JOB_EXECUTION_FAILED', 'runtime', ?, ?, 1, 1, 'Check process log files', ?);
                    """, (error_id, job_id, current_step or "execution", reason, now_str))
                    
            return True
        finally:
            conn.close()

if __name__ == "__main__":
    # Test nhanh Scheduler và Hàng đợi
    import core.db
    from core.project_manager import ProjectManager
    core.db.init_db()
    
    p_id = "test_sch_prj"
    
    # Dọn dẹp trước khi test
    conn = get_db_connection()
    with conn:
        conn.execute("DELETE FROM jobs WHERE project_id = ?;", (p_id,))
        conn.execute("DELETE FROM projects WHERE project_id = ?;", (p_id,))
    conn.close()
    
    # Tạo project trước để tránh lỗi Foreign Key
    ProjectManager.create_project(p_id)
    
    print("Test 1: Submit two GPU heavy jobs...")
    payload = {"test": 1}
    job1 = ResourceScheduler.submit_job(p_id, "sd_generate", "key_1", payload, {})
    job2 = ResourceScheduler.submit_job(p_id, "render_nvenc", "key_2", payload, {})
    
    print("Test 2: Claiming first job...")
    claimed1 = ResourceScheduler.claim_job("worker_1")
    if claimed1:
        print("Claimed job 1:", claimed1["job_id"], "(type:", claimed1["job_type"], ")")
        
    print("Test 3: Tying to claim second job while GPU busy (should fail)...")
    claimed2 = ResourceScheduler.claim_job("worker_2")
    if claimed2 is None:
        print("SUCCESS: Second GPU job was blocked because GPU is busy!")
    else:
        print("FAIL: Second GPU job was claimed while GPU was busy.")
        
    # Thử submit 1 job CPU và claim
    job3 = ResourceScheduler.submit_job(p_id, "download_audio", "key_3", payload, {})
    claimed3 = ResourceScheduler.claim_job("worker_2")
    if claimed3:
        print("SUCCESS: Claimed CPU/network job 3:", claimed3["job_id"], "(type:", claimed3["job_type"], ")")
    else:
        print("FAIL: Failed to claim CPU job while GPU was busy.")

    # Cleanup
    conn = get_db_connection()
    with conn:
        conn.execute("DELETE FROM jobs WHERE project_id = ?;", (p_id,))
        conn.execute("DELETE FROM projects WHERE project_id = ?;", (p_id,))
    conn.close()
    
    p_json = ProjectManager.get_project_json_path(p_id)
    if p_json.exists():
        p_json.unlink()
        
    print("Cleanup done.")

