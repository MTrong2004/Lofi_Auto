"""
Cache Manager Module.
Provides SHA-256 asset content hash caching, validation, deduplication, and database tracking for generated assets.
"""
import os
import sys
import json
import hashlib
from pathlib import Path
from datetime import datetime, timezone
import sqlite3

# Đảm bảo import được config.py từ thư mục cha
sys.path.append(str(Path(__file__).parent.parent))
import config
from core.runtime.db import get_db_connection
from core.runtime.project_manager import ProjectManager

class CacheManager:
    @staticmethod
    def get_file_sha256(file_path: Path) -> str:
        """Tính toán mã SHA-256 của file."""
        if not file_path.exists():
            return ""
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256.update(chunk)
        return sha256.hexdigest()

    @classmethod
    def calculate_input_hash(cls, file_paths: list[Path], config_dict: dict, 
                             producer_version: str = "4.5.0", model_hashes: list[str] = None) -> str:
        """
        Tính toán input_hash đại diện cho đầu vào và cấu hình của một bước.
        Bao gồm: SHA-256 các file đầu vào + cấu hình tham số + phiên bản code + hash của model.
        """
        hasher = hashlib.sha256()
        
        # 1. Hash nội dung file đầu vào
        for path in sorted(file_paths):
            sha = cls.get_file_sha256(path)
            hasher.update(sha.encode("utf-8"))
            
        # 2. Hash cấu hình (sắp xếp key để đảm bảo tính nhất quán)
        config_str = json.dumps(config_dict, sort_keys=True, ensure_ascii=False)
        hasher.update(config_str.encode("utf-8"))
        
        # 3. Hash thông số môi trường
        hasher.update(producer_version.encode("utf-8"))
        
        # 4. Hash các model liên quan
        if model_hashes:
            for mh in sorted(model_hashes):
                if mh:
                    hasher.update(mh.encode("utf-8"))
                    
        return hasher.hexdigest()

    @staticmethod
    def get_downstream_modules(module_name: str) -> list[str]:
        """Định nghĩa đồ thị phụ thuộc giữa các module."""
        # Bản đồ các module phụ thuộc hạ nguồn (downstream mapping)
        mapping = {
            "audio": ["preview", "render", "output"],
            "image": ["layers", "preview", "render", "output"],
            "layers": ["preview", "render", "output"],
            "preview": ["render", "output"],
            "render": ["output"]
        }
        return mapping.get(module_name, [])

    @classmethod
    def invalidate_module(cls, project_id: str, module_name: str, reason: str = "Input changed", actor: str = "system"):
        """
        Vô hiệu hóa một module (Invalidation) và lan truyền đệ quy theo đồ thị phụ thuộc.
        Chuyển module đó sang trạng thái 'invalidated' và thu hồi quyết định duyệt 'revoked'.
        """
        conn = get_db_connection()
        try:
            # Lấy các module cần vô hiệu hóa
            modules_to_invalidate = [module_name]
            visited = set()
            
            queue = [module_name]
            while queue:
                curr = queue.pop(0)
                if curr not in visited:
                    visited.add(curr)
                    for ds in cls.get_downstream_modules(curr):
                        if ds not in visited:
                            modules_to_invalidate.append(ds)
                            queue.append(ds)
            
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            
            with conn:
                for mod in modules_to_invalidate:
                    # Đọc trạng thái cũ
                    cursor = conn.cursor()
                    cursor.execute("""
                    SELECT processing_status, review_status FROM project_modules
                    WHERE project_id = ? AND module_name = ?;
                    """, (project_id, mod))
                    row = cursor.fetchone()
                    
                    if row:
                        old_proc, old_rev = row
                        
                        # Chỉ cập nhật nếu trạng thái chưa bị vô hiệu
                        if old_proc != "invalidated" or old_rev != "revoked":
                            conn.execute("""
                            UPDATE project_modules
                            SET processing_status = 'invalidated', review_status = 'revoked', updated_at_utc = ?
                            WHERE project_id = ? AND module_name = ?;
                            """, (now, project_id, mod))
                            
                            # Ghi State History
                            if old_proc != "invalidated":
                                conn.execute("""
                                INSERT INTO state_history (history_id, project_id, module, state_dimension, from_state, to_state, reason, actor, changed_at_utc)
                                VALUES (?, ?, ?, 'processing_status', ?, 'invalidated', ?, ?, ?);
                                """, (os.urandom(16).hex(), project_id, mod, old_proc, reason, actor, now))
                                
                            if old_rev != "revoked" and old_rev != "not_required":
                                conn.execute("""
                                INSERT INTO state_history (history_id, project_id, module, state_dimension, from_state, to_state, reason, actor, changed_at_utc)
                                VALUES (?, ?, ?, 'review_status', ?, 'revoked', ?, ?, ?);
                                """, (os.urandom(16).hex(), project_id, mod, old_rev, reason, actor, now))
                                
                            print(f"[Invalidation] Invalidated module '{mod}' for project '{project_id}' due to: {reason}")
                            
            # Cập nhật project snapshot JSON
            ProjectManager.sync_and_save_snapshot(project_id)
            
        finally:
            conn.close()

if __name__ == "__main__":
    # Test nhanh cơ chế Invalidation
    import core.runtime.db
    core.runtime.db.init_db()

    p_id = "test_invalidate_prj"
    try:
        # 1. Tạo dự án
        ProjectManager.create_project(p_id)
        
        # 2. Đặt trạng thái các module đã duyệt
        conn = get_db_connection()
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with conn:
            for mod in ["audio", "image", "layers", "preview", "render", "output"]:
                conn.execute("""
                UPDATE project_modules
                SET processing_status = 'verified', review_status = 'approved', updated_at_utc = ?
                WHERE project_id = ? AND module_name = ?;
                """, (now, p_id, mod))
        conn.close()
        
        p = ProjectManager.load_project(p_id)
        print("Trang thai truoc khi invalidate module 'image':")
        print("image:", p["workflow_status"]["image"])
        print("layers:", p["workflow_status"]["layers"])
        print("render:", p["workflow_status"]["render"])
        
        # 3. Vô hiệu hóa ảnh nguồn (do đổi ảnh nguồn)
        CacheManager.invalidate_module(p_id, "image", reason="User changed input image", actor="test_runner")
        
        p_after = ProjectManager.load_project(p_id)
        print("\nTrang thai sau khi invalidate module 'image':")
        print("audio:", p_after["workflow_status"]["audio"]) # Giữ nguyên
        print("image:", p_after["workflow_status"]["image"]) # Bị invalidated
        print("layers:", p_after["workflow_status"]["layers"]) # Bị lan truyền sang layers
        print("render:", p_after["workflow_status"]["render"]) # Bị lan truyền sang render
        
        # Cleanup
        conn = get_db_connection()
        with conn:
            conn.execute("DELETE FROM projects WHERE project_id = ?;", (p_id,))
        conn.close()
        p_json = ProjectManager.get_project_json_path(p_id)
        if p_json.exists():
            p_json.unlink()
        print("\nCleanup success!")
        
    except Exception as e:
        print("Test failed:", str(e))
        # Cleanup
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
