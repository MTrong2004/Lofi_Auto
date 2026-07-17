"""
AI FILE NOTE - CORE DATABASE
Chức năng chính:
- Khởi tạo và quản lý kết nối SQLite Database (`lofi_automation.db`).
- Định nghĩa cấu trúc DDL và tự động chạy migrations để tạo 8 bảng dữ liệu cốt lõi.
Đầu vào chính:
- Đường dẫn DB_PATH cấu hình từ config.
Đầu ra chính:
- Đối tượng sqlite3 Connection (`get_db_connection()`), cấu trúc bảng DB được thiết lập.
API được file khác sử dụng:
- `get_db_connection()`, `init_db()`
Phụ thuộc quan trọng:
- sqlite3, config
Lưu ý khi sửa:
- Luôn bật khóa ngoại (`PRAGMA foreign_keys = ON;`) và cấu hình timeout 30s tránh lỗi lock DB trên Windows.
"""
import sqlite3
import os
import sys
from pathlib import Path
from datetime import datetime

# Đảm bảo import được config.py từ thư mục cha
sys.path.append(str(Path(__file__).parent.parent))
import config

DB_PATH = getattr(config, "DB_PATH", config.BASE_DIR / "data" / "database" / "lofi_automation.db")

def get_db_connection():
    """Tạo kết nối tới SQLite và bật Foreign Keys, thiết lập timeout."""
    conn = sqlite3.connect(str(DB_PATH), timeout=30.0)
    conn.execute("PRAGMA foreign_keys = ON;")
    
    # Bật chế độ ghi nhật ký WAL nếu filesystem hỗ trợ khóa tin cậy
    try:
        conn.execute("PRAGMA journal_mode = WAL;")
    except sqlite3.OperationalError:
        # Fallback về journal mode thông thường (DELETE/TRUNCATE) nếu chạy ở ổ đĩa không hỗ trợ WAL
        conn.execute("PRAGMA journal_mode = DELETE;")
        
    return conn

def init_db():
    """Khởi tạo cấu trúc bảng SQLite nếu chưa tồn tại."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = get_db_connection()
    try:
        with conn:
            # 1. Bảng projects
            conn.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                project_id TEXT PRIMARY KEY,
                schema_name TEXT NOT NULL,
                schema_version INTEGER NOT NULL,
                snapshot_sequence INTEGER NOT NULL DEFAULT 0,
                database_revision INTEGER NOT NULL DEFAULT 0,
                created_at_utc TEXT NOT NULL,
                updated_at_utc TEXT NOT NULL,
                project_json TEXT
            );
            """)

            # 2. Bảng project_modules
            conn.execute("""
            CREATE TABLE IF NOT EXISTS project_modules (
                project_id TEXT,
                module_name TEXT,
                processing_status TEXT NOT NULL,
                review_status TEXT NOT NULL,
                input_hash TEXT,
                output_hash TEXT,
                updated_at_utc TEXT NOT NULL,
                PRIMARY KEY (project_id, module_name),
                FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE
            );
            """)

            # 3. Bảng jobs
            conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                job_type TEXT NOT NULL,
                job_status TEXT NOT NULL,
                current_step TEXT,
                idempotency_key TEXT,
                request_payload_hash TEXT,
                owner_id TEXT,
                lease_expires_at_utc TEXT,
                created_at_utc TEXT NOT NULL,
                updated_at_utc TEXT NOT NULL,
                config_snapshot TEXT,
                FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE,
                UNIQUE(project_id, idempotency_key)
            );
            """)

            # 4. Bảng resource_locks
            conn.execute("""
            CREATE TABLE IF NOT EXISTS resource_locks (
                lock_id TEXT PRIMARY KEY,
                resource_type TEXT NOT NULL,
                resource_id TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                pid INTEGER NOT NULL,
                process_started_at_utc TEXT NOT NULL,
                acquired_at_utc TEXT NOT NULL,
                heartbeat_at_utc TEXT NOT NULL,
                lease_expires_at_utc TEXT NOT NULL,
                fencing_token INTEGER NOT NULL,
                UNIQUE(resource_type, resource_id)
            );
            """)

            # 5. Bảng assets
            conn.execute("""
            CREATE TABLE IF NOT EXISTS assets (
                asset_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                path TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                mime_type TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                processing_status TEXT NOT NULL,
                review_status TEXT NOT NULL,
                created_at_utc TEXT NOT NULL,
                FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE
            );
            """)

            # 6. Bảng asset_provenance
            conn.execute("""
            CREATE TABLE IF NOT EXISTS asset_provenance (
                asset_id TEXT,
                derived_from_asset_id TEXT,
                relation TEXT NOT NULL,
                input_hash TEXT,
                producer_name TEXT,
                producer_version TEXT,
                PRIMARY KEY (asset_id, derived_from_asset_id),
                FOREIGN KEY (asset_id) REFERENCES assets(asset_id) ON DELETE CASCADE,
                FOREIGN KEY (derived_from_asset_id) REFERENCES assets(asset_id) ON DELETE CASCADE
            );
            """)

            # 7. Bảng state_history
            conn.execute("""
            CREATE TABLE IF NOT EXISTS state_history (
                history_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                module TEXT NOT NULL,
                state_dimension TEXT NOT NULL,
                from_state TEXT NOT NULL,
                to_state TEXT NOT NULL,
                reason TEXT,
                actor TEXT NOT NULL,
                changed_at_utc TEXT NOT NULL,
                FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE
            );
            """)

            # 8. Bảng error_records
            conn.execute("""
            CREATE TABLE IF NOT EXISTS error_records (
                error_id TEXT PRIMARY KEY,
                job_id TEXT,
                error_code TEXT NOT NULL,
                category TEXT NOT NULL,
                step TEXT NOT NULL,
                message TEXT NOT NULL,
                technical_detail TEXT,
                retryable INTEGER NOT NULL,
                fallback_available INTEGER NOT NULL,
                suggested_action TEXT,
                occurred_at_utc TEXT NOT NULL,
                FOREIGN KEY (job_id) REFERENCES jobs(job_id) ON DELETE SET NULL
            );
            """)

            # Tạo chỉ mục để tối ưu truy vấn
            conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(job_status);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_assets_project ON assets(project_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_history_project ON state_history(project_id);")
    finally:
        conn.close()

if __name__ == "__main__":
    init_db()
    print("Database initialized successfully at:", DB_PATH)
