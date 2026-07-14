import os
import sys
import unittest
import tempfile
import shutil
import time
import subprocess
import hashlib
from pathlib import Path
from datetime import datetime, timezone, timedelta

# Đảm bảo import được các module từ thư mục hiện hành
sys.path.append(str(Path(__file__).parent))
import config
import core.db
from core.schemas import validate_data_schema, SchemaValidationError
from core.project_manager import ProjectManager
from core.lock_manager import LockManager, ResourceLock, LockAcquisitionError
from core.media_probe import MediaProbe
from core.cache_manager import CacheManager
from core.resource_scheduler import ResourceScheduler
from core.render_worker import RenderWorker, JobCancelledError
from core.output_verifier import OutputVerifier, OutputVerificationError

class TestCorePlatformV45(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Thiết lập cơ sở dữ liệu kiểm thử
        core.db.init_db()

    def test_01_db_tables(self):
        """Kiểm tra sự tồn tại của tất cả 8 bảng dữ liệu trong SQLite (Mục 5)."""
        conn = core.db.get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [row[0] for row in cursor.fetchall()]
        conn.close()
        
        expected_tables = ["projects", "project_modules", "jobs", "resource_locks", 
                           "assets", "asset_provenance", "state_history", "error_records"]
        for table in expected_tables:
            self.assertIn(table, tables, f"Thiếu bảng dữ liệu: {table}")

    def test_02_schema_validation(self):
        """Kiểm tra cơ chế xác thực JSON Schema (Mục 6)."""
        # Thử nghiệm dữ liệu hợp lệ
        valid_track = {
            "schema_name": "track_metadata",
            "schema_version": 1,
            "track_id": "test_track_1",
            "title": "Cozy Beat",
            "author": "Lofi Artist",
            "source": "SoundCloud",
            "url": "https://soundcloud.com/test",
            "duration_seconds": 180.5,
            "license": "CC-BY",
            "views": 1000,
            "likes": 50,
            "relevance_score": 8.5,
            "source_trust_score": 90.0,
            "risk_reasons": [],
            "download_status": "downloaded"
        }
        # Không quăng lỗi là đạt
        validate_data_schema(valid_track, "track_metadata")
        
        # Thử nghiệm dữ liệu thiếu trường bắt buộc
        invalid_track = {
            "schema_name": "track_metadata",
            "schema_version": 1,
            "track_id": "test_track_1"
        }
        with self.assertRaises(SchemaValidationError):
            validate_data_schema(invalid_track, "track_metadata")

    def test_03_project_manager(self):
        """Kiểm tra Project Manager CRUD và cơ chế ghi an toàn Atomic Writer (Mục 7)."""
        p_id = "test_pm_suite_prj"
        
        # Dọn dẹp cũ nếu có
        conn = core.db.get_db_connection()
        with conn:
            conn.execute("DELETE FROM project_modules WHERE project_id = ?;", (p_id,))
            conn.execute("DELETE FROM projects WHERE project_id = ?;", (p_id,))
        conn.close()
        p_json_path = ProjectManager.get_project_json_path(p_id)
        if p_json_path.exists():
            p_json_path.unlink()
            
        # Tạo project
        ProjectManager.create_project(p_id)
        self.assertTrue(p_json_path.exists(), "File project.json không được tạo.")
        
        # Đọc project
        p_data = ProjectManager.load_project(p_id)
        self.assertEqual(p_data["project_id"], p_id)
        self.assertEqual(p_data["modules_detail"]["audio"]["processing_status"], "not_started")
        
        # Cập nhật trạng thái workflow
        ProjectManager.update_workflow_status(
            project_id=p_id,
            module_name="audio",
            processing_status="verified",
            review_status="approved",
            input_hash="hash123",
            output_hash="hash123",
            reason="Imported fine",
            actor="test"
        )
        
        p_data_updated = ProjectManager.load_project(p_id)
        self.assertEqual(p_data_updated["modules_detail"]["audio"]["processing_status"], "verified")
        self.assertEqual(p_data_updated["modules_detail"]["audio"]["review_status"], "approved")
        
        # Dọn dẹp
        p_json_path.unlink()

    def test_04_lock_manager(self):
        """Kiểm tra Fencing Token và cơ chế heartbeat/recovery (Mục 8)."""
        resource = "test_gpu_render"
        owner_a = "worker_a"
        owner_b = "worker_b"
        
        # Dọn sạch khóa cũ
        conn = core.db.get_db_connection()
        with conn:
            conn.execute("DELETE FROM resource_locks WHERE resource_id = ?;", (resource,))
        conn.close()
        
        # Owner A lấy khóa
        token_a = LockManager.acquire_lock("gpu", resource, owner_a, lease_seconds=2)
        self.assertIsNotNone(token_a, "Owner A không lấy được khóa.")
        
        # Owner B cố lấy khóa trùng -> Phải quăng lỗi LockAcquisitionError
        with self.assertRaises(LockAcquisitionError):
            LockManager.acquire_lock("gpu", resource, owner_b, lease_seconds=2)
        
        # Heartbeat của Owner A
        success = LockManager.renew_lock("gpu", resource, owner_a, lease_seconds=2)
        self.assertTrue(success, "Gia hạn lease cho Owner A thất bại.")
        
        # Giải phóng khóa Owner A
        LockManager.release_lock("gpu", resource, owner_a)
        
        # Owner B giờ phải lấy khóa thành công
        token_b_ok = LockManager.acquire_lock("gpu", resource, owner_b, lease_seconds=2)
        self.assertIsNotNone(token_b_ok, "Owner B không lấy được khóa sau khi giải phóng.")
        LockManager.release_lock("gpu", resource, owner_b)

    def test_05_scheduler_and_worker(self):
        """Kiểm tra scheduler Claim Job an toàn và Worker chạy nền / Hủy tiến trình con (Mục 22, 23)."""
        p_id = "test_scheduler_suite_prj"
        ProjectManager.create_project(p_id)
        
        # Submit job vào Queue
        job_info = ResourceScheduler.submit_job(p_id, "test_job", "sched_test_key", {}, {})
        job_id = job_info["job_id"]
        
        # Worker claim job
        worker = RenderWorker("suite_worker")
        claimed_job = ResourceScheduler.claim_job(worker.worker_id)
        self.assertIsNotNone(claimed_job, "Worker không claim được job vừa gửi.")
        self.assertEqual(claimed_job["job_id"], job_id)
        
        # Hủy job
        conn = core.db.get_db_connection()
        with conn:
            conn.execute("UPDATE jobs SET job_status = 'cancelling' WHERE job_id = ?;", (job_id,))
        conn.close()
        
        # Chạy kiểm tra hủy trong worker
        is_cancelled = worker.check_cancellation(job_id)
        self.assertTrue(is_cancelled, "Worker không phát hiện tín hiệu hủy.")
        
        # Thử chạy lệnh mẫu và xem nó có quăng JobCancelledError hay không
        cmd = ["ping", "127.0.0.1", "-n", "10"] if sys.platform == "win32" else ["sleep", "10"]
        with self.assertRaises(JobCancelledError):
            worker.execute_command_with_cancellation_check(cmd, job_id)
            
        # Dọn dẹp
        conn = core.db.get_db_connection()
        with conn:
            conn.execute("DELETE FROM jobs WHERE project_id = ?;", (p_id,))
            conn.execute("DELETE FROM projects WHERE project_id = ?;", (p_id,))
        conn.close()
        p_json = ProjectManager.get_project_json_path(p_id)
        if p_json.exists():
            p_json.unlink()

    def test_06_output_verifier(self):
        """Kiểm tra Output Verifier với kiểm định đen/yên lặng và tạo manifest (Mục 25.3)."""
        temp_dir = Path(tempfile.gettempdir())
        test_audio = temp_dir / "test_verifier_audio_suite.mp3"
        test_video = temp_dir / "test_verifier_video_suite.mp4"
        
        # Sinh file audio test
        cmd_audio = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "sine=frequency=1000:duration=3",
            "-ac", "2", "-ar", "48000",
            test_audio.as_posix()
        ]
        # Sinh file video 1920x1080 test
        cmd_video = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "color=c=blue:s=1920x1080:d=3",
            "-i", test_audio.as_posix(),
            "-c:v", "libx264", "-r", "24", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k",
            test_video.as_posix()
        ]
        
        try:
            shutil.rmtree(str(temp_dir / "test_verifier_video_suite_manifest.json"), ignore_errors=True)
            subprocess.run(cmd_audio, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            subprocess.run(cmd_video, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            
            # Chạy verify
            manifest = OutputVerifier.verify_video(
                video_path=test_video,
                project_id="test_verifier_suite_prj",
                expected_duration=3.0,
                track_id="mock_suite_track"
            )
            self.assertEqual(manifest["project_id"], "test_verifier_suite_prj")
            self.assertEqual(manifest["resolution"], "1920x1080")
            self.assertEqual(manifest["fps"]["numerator"], 24)
            self.assertEqual(manifest["video_codec"], "h264")
            self.assertEqual(manifest["audio_codec"], "aac")
            
        finally:
            test_audio.unlink(missing_ok=True)
            test_video.unlink(missing_ok=True)
            test_video.with_name(f"{test_video.stem}_manifest.json").unlink(missing_ok=True)

if __name__ == "__main__":
    unittest.main()
