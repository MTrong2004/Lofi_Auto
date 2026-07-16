"""
AI FILE NOTE - PROJECT LIFE CYCLE MANAGER
Chức năng chính:
- Khởi tạo và quản lý trạng thái/vòng đời của các Project trong hệ thống.
- Cập nhật tiến trình cho từng module phụ trách (audio, image, render, uploader).
- Đảm bảo cơ chế lưu metadata an toàn qua cơ chế Atomic Write (ghi tạm rồi đổi tên nguyên tử).
Đầu vào chính:
- project_id, cấu hình workflow chi tiết.
Đầu ra chính:
- File `project.json` trong thư mục dự án và cập nhật trạng thái SQLite đồng bộ.
API được file khác sử dụng:
- Lớp `ProjectManager` và các hàm tĩnh của nó (`create_project()`, `load_project()`, `update_workflow_status()`, v.v.).
Phụ thuộc quan trọng:
- core.runtime.db, core.runtime.schemas, config
Lưu ý khi sửa:
- Mọi thao tác ghi file JSON trạng thái dự án phải đi qua `_write_atomic` để bảo toàn dữ liệu tránh hư hỏng file.
"""
import os
import sys
import json
from pathlib import Path
from datetime import datetime, timezone
import sqlite3

# Đảm bảo import được config.py từ thư mục cha
sys.path.append(str(Path(__file__).parent.parent))
import config
from core.runtime.db import get_db_connection
from core.runtime.schemas import validate_data_schema, SchemaValidationError

PROJECTS_DIR = config.BASE_DIR / "data" / "projects"

def atomic_write_json(data: dict, dest_path: Path, schema_name: str = None):
    """
    Ghi dữ liệu JSON nguyên tử (Atomic Writer) theo quy chuẩn v4.5:
    1. Ghi file tạm .tmp.
    2. Gọi flush, fsync.
    3. Đọc ngược kiểm tra tính toàn vẹn và validate schema.
    4. Đổi tên nguyên tử sang file chính.
    5. fsync thư mục cha.
    """
    if schema_name:
        validate_data_schema(data, schema_name)
        
    tmp_path = dest_path.with_suffix(".tmp")
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    
    try:
        # 1. Ghi file tạm
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass # Có thể hệ thống tập tin không hỗ trợ fsync trực tiếp
                
        # 2. Đọc lại và kiểm định toàn vẹn
        with open(tmp_path, "r", encoding="utf-8") as f:
            read_back = json.load(f)
            
        if schema_name:
            validate_data_schema(read_back, schema_name)
            
        # 3. Đổi tên nguyên tử (atomic replace)
        os.replace(str(tmp_path), str(dest_path))
        
        # 4. fsync thư mục cha (nếu OS hỗ trợ)
        try:
            fd = os.open(str(dest_path.parent), os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except Exception:
            pass
            
    except Exception as e:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass
        raise RuntimeError(f"Ghi file nguyên tử thất bại cho {dest_path.name}: {e}")

class ProjectManager:
    @staticmethod
    def get_project_dir(project_id: str) -> Path:
        return PROJECTS_DIR / project_id

    @classmethod
    def get_project_json_path(cls, project_id: str) -> Path:
        return cls.get_project_dir(project_id) / "project.json"

    @classmethod
    def create_project(cls, project_id: str) -> dict:
        """Tạo dự án mới trong database và tạo snapshot ban đầu."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        
        conn = get_db_connection()
        try:
            with conn:
                # Kiểm tra xem dự án đã tồn tại chưa
                cursor = conn.cursor()
                cursor.execute("SELECT project_id FROM projects WHERE project_id = ?;", (project_id,))
                if cursor.fetchone():
                    raise ValueError(f"Project with ID '{project_id}' already exists.")
                
                # Tạo bản ghi project
                conn.execute("""
                INSERT INTO projects (project_id, schema_name, schema_version, snapshot_sequence, database_revision, created_at_utc, updated_at_utc, project_json)
                VALUES (?, 'project', 2, 1, 1, ?, ?, NULL);
                """, (project_id, now, now))
                
                # Khởi tạo các module trạng thái mặc định
                modules = ["trend", "audio", "image", "layers", "preview", "render", "output"]
                for mod in modules:
                    conn.execute("""
                    INSERT INTO project_modules (project_id, module_name, processing_status, review_status, input_hash, output_hash, updated_at_utc)
                    VALUES (?, ?, 'not_started', 'not_required', NULL, NULL, ?);
                    """, (project_id, mod, now))
            
            # Đồng bộ ghi file project.json
            return cls.sync_and_save_snapshot(project_id)
        finally:
            conn.close()

    @classmethod
    def load_project(cls, project_id: str) -> dict:
        """Tải trạng thái dự án hiện tại từ SQLite và kiểm tra tính nhất quán với snapshot."""
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
            SELECT schema_name, schema_version, snapshot_sequence, database_revision, created_at_utc, updated_at_utc
            FROM projects WHERE project_id = ?;
            """, (project_id,))
            proj_row = cursor.fetchone()
            if not proj_row:
                # Nếu không có trong DB nhưng có file project.json, tự động khôi phục (Import/Restore)
                json_path = cls.get_project_json_path(project_id)
                if json_path.exists():
                    return cls.import_project_from_snapshot(json_path)
                raise FileNotFoundError(f"Không tìm thấy dự án '{project_id}' trong DB lẫn file hệ thống.")
            
            schema_name, schema_version, snapshot_sequence, database_revision, created_at_utc, updated_at_utc = proj_row
            
            # Lấy trạng thái các module
            cursor.execute("""
            SELECT module_name, processing_status, review_status, input_hash, output_hash
            FROM project_modules WHERE project_id = ?;
            """, (project_id,))
            module_rows = cursor.fetchall()
            
            wf_status = {}
            # Khởi tạo mặc định
            for mod in ["trend", "audio", "image", "layers", "preview", "render", "output"]:
                wf_status[mod] = "not_started"
            
            modules_detail = {}
            for row in module_rows:
                mod_name, proc_stat, rev_stat, in_h, out_h = row
                modules_detail[mod_name] = {
                    "processing_status": proc_stat,
                    "review_status": rev_stat,
                    "input_hash": in_h,
                    "output_hash": out_h
                }
                
                # Suy luận nhãn workflow_status hiển thị ở UI
                # Chú ý: approved = verified + approved
                # waiting_review = verified + pending
                if proc_stat == "verified" and rev_stat == "approved":
                    wf_status[mod_name] = "approved"
                elif proc_stat == "verified" and rev_stat == "pending":
                    wf_status[mod_name] = "waiting_review"
                else:
                    wf_status[mod_name] = proc_stat
            
            project_data = {
                "schema_name": schema_name,
                "schema_version": schema_version,
                "project_id": project_id,
                "snapshot_sequence": snapshot_sequence,
                "database_revision": database_revision,
                "workflow_status": wf_status,
                "modules_detail": modules_detail,
                "created_at_utc": created_at_utc,
                "updated_at_utc": updated_at_utc
            }
            
            # Đối chiếu với file project.json
            json_path = cls.get_project_json_path(project_id)
            if json_path.exists():
                try:
                    with open(json_path, "r", encoding="utf-8") as f:
                        file_data = json.load(f)
                    
                    # Nếu SQLite cũ hơn file JSON (do copy dự án hoặc phục hồi backup)
                    file_rev = file_data.get("database_revision", 0)
                    if file_rev > database_revision:
                        # Ghi ngược file_data vào SQLite để cập nhật
                        cls._restore_to_sqlite(file_data)
                        return cls.load_project(project_id)
                except Exception as e:
                    print(f"[PM Warning] Không thể đối chiếu file JSON: {e}")
                    
            return project_data
            
        finally:
            conn.close()

    @classmethod
    def update_workflow_status(cls, project_id: str, module_name: str, 
                               processing_status: str, review_status: str, 
                               input_hash: str = None, output_hash: str = None,
                               reason: str = None, actor: str = "system") -> dict:
        """Cập nhật trạng thái một module của project."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        
        conn = get_db_connection()
        try:
            with conn:
                cursor = conn.cursor()
                # Lấy trạng thái hiện hành để ghi lịch sử
                cursor.execute("""
                SELECT processing_status, review_status
                FROM project_modules WHERE project_id = ? AND module_name = ?;
                """, (project_id, module_name))
                row = cursor.fetchone()
                
                from_proc = row[0] if row else "not_started"
                from_rev = row[1] if row else "not_required"
                
                # Cập nhật DB
                conn.execute("""
                UPDATE project_modules
                SET processing_status = ?, review_status = ?, input_hash = ?, output_hash = ?, updated_at_utc = ?
                WHERE project_id = ? AND module_name = ?;
                """, (processing_status, review_status, input_hash, output_hash, now, project_id, module_name))
                
                # Tăng database_revision của dự án
                conn.execute("""
                UPDATE projects
                SET database_revision = database_revision + 1, updated_at_utc = ?
                WHERE project_id = ?;
                """, (now, project_id))
                
                # Ghi lịch sử chuyển đổi trạng thái (State History)
                # 1. Về mặt kỹ thuật
                if from_proc != processing_status:
                    conn.execute("""
                    INSERT INTO state_history (history_id, project_id, module, state_dimension, from_state, to_state, reason, actor, changed_at_utc)
                    VALUES (?, ?, ?, 'processing_status', ?, ?, ?, ?, ?);
                    """, (os.urandom(16).hex(), project_id, module_name, from_proc, processing_status, reason, actor, now))
                
                # 2. Về mặt duyệt
                if from_rev != review_status:
                    conn.execute("""
                    INSERT INTO state_history (history_id, project_id, module, state_dimension, from_state, to_state, reason, actor, changed_at_utc)
                    VALUES (?, ?, ?, 'review_status', ?, ?, ?, ?, ?);
                    """, (os.urandom(16).hex(), project_id, module_name, from_rev, review_status, reason, actor, now))
                    
            # Ghi snapshot mới sau khi cập nhật
            return cls.sync_and_save_snapshot(project_id)
        finally:
            conn.close()

    @classmethod
    def sync_and_save_snapshot(cls, project_id: str) -> dict:
        """Đồng bộ trạng thái SQLite hiện tại sang project.json thông qua atomic write."""
        project_data = cls.load_project(project_id)
        
        # Tăng snapshot_sequence trong SQLite
        conn = get_db_connection()
        try:
            with conn:
                conn.execute("""
                UPDATE projects
                SET snapshot_sequence = snapshot_sequence + 1
                WHERE project_id = ?;
                """, (project_id,))
            
            project_data["snapshot_sequence"] += 1
            
            # Tạo snapshot JSON
            snapshot = {
                "schema_name": "project",
                "schema_version": 2,
                "project_id": project_data["project_id"],
                "snapshot_sequence": project_data["snapshot_sequence"],
                "database_revision": project_data["database_revision"],
                "workflow_status": project_data["workflow_status"],
                "modules_detail": project_data["modules_detail"],
                "created_at_utc": project_data["created_at_utc"],
                "updated_at_utc": project_data["updated_at_utc"]
            }
            
            # Ghi file project.json nguyên tử
            json_path = cls.get_project_json_path(project_id)
            atomic_write_json(snapshot, json_path, "project")
            
            # Ghi snapshot JSON vào trường project_json của SQLite để backup
            with conn:
                conn.execute("""
                UPDATE projects
                SET project_json = ?
                WHERE project_id = ?;
                """, (json.dumps(snapshot), project_id))
                
            return project_data
        finally:
            conn.close()

    @classmethod
    def import_project_from_snapshot(cls, snapshot_path: Path) -> dict:
        """Đọc project.json và khôi phục vào SQLite."""
        if not snapshot_path.exists():
            raise FileNotFoundError(f"Không tìm thấy file snapshot: {snapshot_path}")
            
        with open(snapshot_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        validate_data_schema(data, "project")
        cls._restore_to_sqlite(data)
        return cls.load_project(data["project_id"])

    @staticmethod
    def _restore_to_sqlite(data: dict):
        """Khôi phục dữ liệu thô vào các bảng SQLite."""
        project_id = data["project_id"]
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        
        conn = get_db_connection()
        try:
            with conn:
                # Xóa dữ liệu cũ nếu có
                conn.execute("DELETE FROM project_modules WHERE project_id = ?;", (project_id,))
                conn.execute("DELETE FROM projects WHERE project_id = ?;", (project_id,))
                
                # Chèn lại dự án
                conn.execute("""
                INSERT INTO projects (project_id, schema_name, schema_version, snapshot_sequence, database_revision, created_at_utc, updated_at_utc, project_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                """, (project_id, data["schema_name"], data["schema_version"], data["snapshot_sequence"], data["database_revision"], data["created_at_utc"], data["updated_at_utc"], json.dumps(data)))
                
                # Chèn các module chi tiết
                detail = data.get("modules_detail", {})
                for mod_name, details in detail.items():
                    conn.execute("""
                    INSERT INTO project_modules (project_id, module_name, processing_status, review_status, input_hash, output_hash, updated_at_utc)
                    VALUES (?, ?, ?, ?, ?, ?, ?);
                    """, (project_id, mod_name, details["processing_status"], details["review_status"], details.get("input_hash"), details.get("output_hash"), now))
        finally:
            conn.close()

if __name__ == "__main__":
    # Test nhanh
    import core.runtime.db
    core.runtime.db.init_db()
    
    p_id = "test_prj_1"
    
    # Clean up before testing
    try:
        conn = get_db_connection()
        with conn:
            conn.execute("DELETE FROM projects WHERE project_id = ?;", (p_id,))
        conn.close()
        # Delete file snapshot if exists
        p_json = ProjectManager.get_project_json_path(p_id)
        if p_json.exists():
            p_json.unlink()
    except Exception:
        pass

    try:
        ProjectManager.create_project(p_id)
        print("Create project test success!")
        
        # Thu cap nhat trang thai
        ProjectManager.update_workflow_status(
            project_id=p_id,
            module_name="audio",
            processing_status="verified",
            review_status="approved",
            input_hash="audio_sha256_mock_hash",
            reason="Nhac da duoc chuan hoa",
            actor="test_runner"
        )
        print("Update module status success!")
        
        # Load lai
        p = ProjectManager.load_project(p_id)
        print("Workflow status after update:", p["workflow_status"])
        
        # Cleanup
        conn = get_db_connection()
        with conn:
            conn.execute("DELETE FROM projects WHERE project_id = ?;", (p_id,))
        conn.close()
        p_json = ProjectManager.get_project_json_path(p_id)
        if p_json.exists():
            p_json.unlink()
        print("Cleanup test project success!")
        
    except Exception as e:
        print("Test failed:", str(e))
        # Clean up
        try:
            conn = get_db_connection()
            with conn:
                conn.execute("DELETE FROM projects WHERE project_id = ?;", (p_id,))
            conn.close()
            p_json = ProjectManager.get_project_json_path(p_id)
            if p_json.exists():
                p_json.unlink()
        except Exception:
            pass
