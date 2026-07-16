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
import core.runtime.db
from core.runtime.schemas import validate_data_schema, SchemaValidationError
from core.runtime.project_manager import ProjectManager
from core.runtime.lock_manager import LockManager, ResourceLock, LockAcquisitionError
from core.media.probe import MediaProbe
from core.runtime.cache_manager import CacheManager
from core.runtime.resource_scheduler import ResourceScheduler
from core.runtime.render_worker import RenderWorker, JobCancelledError
from core.media.output_verifier import OutputVerifier, OutputVerificationError

class TestCorePlatformV45(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Thiết lập cơ sở dữ liệu kiểm thử
        core.runtime.db.init_db()

    def test_01_db_tables(self):
        """Kiểm tra sự tồn tại của tất cả 8 bảng dữ liệu trong SQLite (Mục 5)."""
        conn = core.runtime.db.get_db_connection()
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
        conn = core.runtime.db.get_db_connection()
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
        conn = core.runtime.db.get_db_connection()
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
        conn = core.runtime.db.get_db_connection()
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
        conn = core.runtime.db.get_db_connection()
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

    def test_07_stable_diffusion_gates(self):
        """Kiểm tra SDAdapter, SDModelManager, SDHealthChecker bằng Mocking (Gate G3/G4)."""
        from unittest.mock import patch, MagicMock
        from core.image.sd_manager import SDAdapter, SDHealthChecker, SDModelManager
        
        api_url = "http://127.0.0.1:7860"
        
        # Thiết lập các mock responses
        mock_openapi = {"openapi": "3.0.0", "info": {"title": "Stable Diffusion WebUI API"}}
        mock_models = [{"title": "sd_v1.5_anime.safetensors [5efc1a7d]", "model_name": "sd_v1.5_anime.safetensors"}]
        mock_samplers = [{"name": "Euler a"}]
        mock_options = {"sd_model_checkpoint": "sd_v1.5_anime.safetensors [5efc1a7d]"}
        mock_txt2img_res = {
            "images": [
                "YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWE="
            ]
        }
        
        def mock_request(url, *args, **kwargs):
            mock_res = MagicMock()
            mock_res.status_code = 200
            if "openapi.json" in url:
                mock_res.json.return_value = mock_openapi
            elif "sd-models" in url:
                mock_res.json.return_value = mock_models
            elif "samplers" in url:
                mock_res.json.return_value = mock_samplers
            elif "options" in url:
                mock_res.json.return_value = mock_options
            elif "txt2img" in url:
                mock_res.json.return_value = mock_txt2img_res
                mock_res.content = b"mock content"
            return mock_res
            
        with patch("requests.get", side_effect=mock_request) as mock_get, \
             patch("requests.post", side_effect=mock_request) as mock_post:
             
            # Test SDAdapter discover và capability checks
            adapter = SDAdapter(api_url)
            self.assertTrue(adapter.capability_check())
            
            # Test txt2img
            img_b64 = adapter.txt2img({"prompt": "lofi scenery", "width": 256, "height": 256})
            self.assertIsNotNone(img_b64)
            
            # Test model manager load checkpoint
            load_ok = SDModelManager.load_checkpoint(api_url, "sd_v1.5_anime")
            self.assertTrue(load_ok)
            
            # Test Health checker
            import tempfile
            temp_report_path = Path(tempfile.gettempdir()) / "sd_health_report_suite.json"
            try:
                report = SDHealthChecker.run_health_check(api_url, temp_report_path)
                self.assertEqual(report["api_check"], "passed")
                self.assertEqual(report["model_load_check"], "passed")
                self.assertEqual(report["generation_check"], "passed")
                self.assertTrue(temp_report_path.exists())
            finally:
                if temp_report_path.exists():
                    temp_report_path.unlink()

    def test_08_sd_installer_preflight(self):
        """Kiểm tra SDInstaller.run_preflight() hoạt động chính xác với các tham số hệ thống giả lập."""
        from core.image.sd_manager import SDInstaller
        from unittest.mock import patch
        
        # Test case: Everything passes
        with patch("sys.platform", "win32"), \
             patch("shutil.which", return_value="C:\\NVIDIA\\nvidia-smi"), \
             patch("subprocess.run") as mock_run, \
             patch("psutil.virtual_memory") as mock_mem:
             
            # mock nvidia-smi return (4000 MB VRAM)
            mock_run.return_value.stdout = "4000\n"
            # mock RAM (16 GB)
            mock_mem.return_value.total = 16 * 1024**3
            mock_mem.return_value.available = 8 * 1024**3
            
            with tempfile.TemporaryDirectory() as temp_dir:
                res = SDInstaller.run_preflight(Path(temp_dir), port=7860)
                self.assertIn(res["overall"], ["passed", "warning"])
                self.assertEqual(res["os_check"], "passed")
                self.assertEqual(res["gpu_check"], "passed")
                self.assertEqual(res["ram_check"], "passed")

    def test_09_sd_installer_staging_and_rollback(self):
        """Kiểm tra quy trình cài đặt qua Staging, Promote thành công và Rollback khi gặp lỗi."""
        from core.image.sd_manager import SDInstaller
        from unittest.mock import patch, MagicMock
        import json
        
        with tempfile.TemporaryDirectory() as base_dir:
            install_dir = Path(base_dir) / "sd_install"
            
            # 1. Test cài đặt thành công (Promotion)
            with patch("sys.platform", "win32"), \
                 patch("shutil.which", return_value="mock_path"), \
                 patch("subprocess.run") as mock_run, \
                 patch("psutil.virtual_memory") as mock_mem:
                 
                mock_run.return_value = MagicMock(returncode=0, stdout="4000\n")
                mock_mem.return_value.total = 16 * 1024**3
                
                # Mock git clone và tạo thư mục để mô phỏng git, venv, requirements
                def mock_run_side_effect(cmd, *args, **kwargs):
                    if "clone" in cmd:
                        # cmd[-1] là staging path của webui
                        webui_path = Path(cmd[-1])
                        webui_path.mkdir(parents=True, exist_ok=True)
                        (webui_path / "launch.py").write_text("launch content")
                        (webui_path / "requirements.txt").write_text("reqs")
                        (webui_path / "extensions").mkdir(parents=True, exist_ok=True)
                    elif "venv" in cmd:
                        # venv_dir là cmd[-1]
                        venv_path = Path(cmd[-1])
                        scripts_path = venv_path / "Scripts"
                        scripts_path.mkdir(parents=True, exist_ok=True)
                        (scripts_path / "pip.exe").write_text("pip")
                    return MagicMock(returncode=0)
                    
                mock_run.side_effect = mock_run_side_effect
                
                success = SDInstaller.install(install_dir, port=7860)
                self.assertTrue(success)
                
                # Xác minh promotion đã xảy ra: staging đã bị xóa, active folder đã xuất hiện
                active_webui = install_dir / "stable-diffusion-webui"
                active_runtime = install_dir / "runtime"
                self.assertTrue(active_webui.exists())
                self.assertTrue(active_runtime.exists())
                self.assertTrue((install_dir / "install_state.json").exists())
                
                # Đọc install_state.json và kiểm tra trạng thái
                with open(install_dir / "install_state.json", "r", encoding="utf-8") as f:
                    state_data = json.load(f)
                    self.assertEqual(state_data["state"], "ready")
                    self.assertTrue(state_data["installed"])

            # 2. Test Rollback khi promote gặp lỗi
            with patch("sys.platform", "win32"), \
                 patch("shutil.which", return_value="mock_path"), \
                 patch("subprocess.run") as mock_run, \
                 patch("psutil.virtual_memory") as mock_mem, \
                 patch.object(SDInstaller, "_safe_rename", side_effect=PermissionError("Mock Permission Error")):
                 
                mock_run.return_value = MagicMock(returncode=0, stdout="4000\n")
                mock_run.side_effect = mock_run_side_effect
                mock_mem.return_value.total = 16 * 1024**3
                
                with self.assertRaises(Exception):
                    SDInstaller.install(install_dir, port=7860)

    def test_10_sd_installer_extension_allowlist(self):
        """Kiểm tra chức năng lọc Extension Allowlist, vô hiệu hóa các extension lạ."""
        from core.image.sd_manager import SDInstaller
        from unittest.mock import patch, MagicMock
        
        with tempfile.TemporaryDirectory() as base_dir:
            install_dir = Path(base_dir) / "sd_install"
            
            with patch("sys.platform", "win32"), \
                 patch("shutil.which", return_value="mock_path"), \
                 patch("subprocess.run") as mock_run, \
                 patch("psutil.virtual_memory") as mock_mem:
                 
                mock_run.return_value = MagicMock(returncode=0, stdout="4000\n")
                mock_mem.return_value.total = 16 * 1024**3
                
                def mock_run_side_effect(cmd, *args, **kwargs):
                    if "clone" in cmd:
                        webui_path = Path(cmd[-1])
                        webui_path.mkdir(parents=True, exist_ok=True)
                        (webui_path / "launch.py").write_text("launch content")
                        ext_dir = webui_path / "extensions"
                        ext_dir.mkdir(parents=True, exist_ok=True)
                        
                        # Tạo 1 extension hợp lệ và 1 extension không hợp lệ
                        (ext_dir / "sd-webui-controlnet").mkdir(parents=True, exist_ok=True)
                        (ext_dir / "malicious-extension").mkdir(parents=True, exist_ok=True)
                        
                    elif "venv" in cmd:
                        venv_path = Path(cmd[-1])
                        scripts_path = venv_path / "Scripts"
                        scripts_path.mkdir(parents=True, exist_ok=True)
                        (scripts_path / "pip.exe").write_text("pip")
                    return MagicMock(returncode=0)
                    
                mock_run.side_effect = mock_run_side_effect
                
                success = SDInstaller.install(install_dir, port=7860)
                self.assertTrue(success)
                
                # Sau khi promote, kiểm tra extensions trong active folder
                active_ext_dir = install_dir / "stable-diffusion-webui" / "extensions"
                self.assertTrue((active_ext_dir / "sd-webui-controlnet").exists())
                self.assertFalse((active_ext_dir / "malicious-extension").exists())
                self.assertTrue((active_ext_dir / "malicious-extension.disabled").exists())

    def test_11_audio_normalization_and_vibe(self):
        """Kiểm tra xử lý chuẩn hóa LUFS, lặp và sinh bản nghe thử (Preview)."""
        from core.media.audio_processor import AudioProcessor
        
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_audio = temp_path / "test_music.m4a"
            
            # Sinh file audio giả lập (sine wave 3 giây)
            cmd_audio = [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", "sine=frequency=1000:duration=3",
                "-c:a", "aac", "-b:a", "128k",
                input_audio.as_posix()
            ]
            subprocess.run(cmd_audio, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            
            # 1. Test normalize
            norm_output = temp_path / "normalized.m4a"
            AudioProcessor.normalize_audio(input_audio, norm_output, target_lufs=-15.0)
            self.assertTrue(norm_output.exists())
            
            # 2. Test loop
            loop_output = temp_path / "looped.m4a"
            AudioProcessor.loop_audio(input_audio, loop_output, target_duration=8.0, crossfade_seconds=1.0)
            self.assertTrue(loop_output.exists())
            
            # 3. Test generate previews
            # Mượn chính tệp sine làm tiếng ambience giả lập
            rain_mock = Path("data/effects/rain_ambience.mp3")
            rain_mock.parent.mkdir(parents=True, exist_ok=True)
            if not rain_mock.exists():
                shutil.copy(str(input_audio), str(rain_mock))
                
            preview_dir = temp_path / "previews"
            res = AudioProcessor.generate_previews(input_audio, preview_dir, duration=2.0)
            self.assertTrue(res["clean"].exists())
            self.assertTrue(res["light"].exists())
            self.assertTrue(res["rich"].exists())

    def test_12_image_upscale_fallback(self):
        """Kiểm tra upscaler tự động fallback sang Lanczos 1920x1080 khi offline/lỗi API."""
        from core.image.upscaler import ImageUpscaler
        from PIL import Image
        
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_img = temp_path / "source_img.png"
            output_img = temp_path / "upscaled_img.png"
            
            # Sinh ảnh gốc nhỏ (960x540)
            img = Image.new("RGB", (960, 540), color="blue")
            img.save(input_img, "PNG")
            
            # Chạy upscale không có API WebUI -> Phải tự fallback sang Lanczos
            metadata = ImageUpscaler.upscale_image(input_img, output_img, api_url=None)
            self.assertTrue(output_img.exists())
            self.assertEqual(metadata["upscale_method"], "lanczos_fallback")
            
            # Đọc lại kích thước để xác nhận phóng to đúng 1920x1080
            with Image.open(output_img) as up_img:
                self.assertEqual(up_img.size, (1920, 1080))

    def test_13_parallax_rendering(self):
        """Kiểm tra phân tách lớp hình ảnh và sinh FFmpeg filter cho Parallax 2.5D."""
        from core.image.parallax_processor import ParallaxProcessor
        from PIL import Image
        
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_img = temp_path / "source_img.png"
            layers_dir = temp_path / "layers"
            
            # Sinh ảnh gốc 960x540
            img = Image.new("RGB", (960, 540), color="red")
            img.save(input_img, "PNG")
            
            # 1. Test phân tách lớp
            manifest = ParallaxProcessor.split_layers(input_img, layers_dir)
            # Ảnh đỏ phẳng không có chiều sâu -> fallback mask hình học;
            # ảnh thật sẽ ra "three_layer_depth"
            self.assertIn(manifest["layer_mode"], ("three_layer_depth", "three_layer_geometric"))
            self.assertTrue((layers_dir / "background_filled.png").exists())
            self.assertTrue((layers_dir / "midground.png").exists())
            self.assertTrue((layers_dir / "foreground.png").exists())
            self.assertTrue((layers_dir / "layers.json").exists())
            
            # 2. Test sinh filter complex cho FFmpeg
            filter_str = ParallaxProcessor.build_parallax_filter_complex(
                bg_w=2000, bg_h=1125, start_frame=0, fps=24, period_seconds=30.0
            )
            self.assertIn("overlay", filter_str)
            self.assertIn("sin", filter_str)
            self.assertIn("cos", filter_str)

    def test_14_karaoke_subtitles(self):
        """Kiểm tra chức năng sinh phụ đề Karaoke ASS và lưu trữ manifest."""
        from core.text.ass_renderer import generate_ass_file, load_subtitle_manifest, save_subtitle_manifest, DEFAULT_SUBTITLE_STYLE
        
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            manifest_data = {
                "enabled": True,
                "reviewed": True,
                "language": "zh",
                "style": dict(DEFAULT_SUBTITLE_STYLE),
                "lyrics": [
                    {
                        "start": 1.0,
                        "end": 4.5,
                        "text": "你好世界",
                        "pinyin": "nǐ hǎo shì jiè",
                        "vietnamese": "Xin chào thế giới",
                        "words": [
                            {"word": "nǐ", "start": 1.0, "end": 1.8},
                            {"word": "hǎo", "start": 1.8, "end": 2.5},
                            {"word": "shì", "start": 2.5, "end": 3.2},
                            {"word": "jiè", "start": 3.2, "end": 4.5}
                        ]
                    }
                ]
            }
            
            # Test manifest save/load
            manifest_file = temp_path / "test_project_subtitle_manifest.json"
            # Override function or write direct JSON to test renderer
            ass_output = temp_path / "test_lyrics.ass"
            
            generate_ass_file(manifest_data["lyrics"], manifest_data["style"], ass_output)
            
            self.assertTrue(ass_output.is_file(), "File phụ đề ASS không được tạo.")
            
            content = ass_output.read_text(encoding="utf-8")
            self.assertIn("[Script Info]", content)
            self.assertIn("Style: Original", content)
            self.assertIn("Style: Translation", content)
            self.assertIn("Dialogue: 0,0:00:01.00,0:00:04.50,Original", content)
            self.assertIn("Dialogue: 0,0:00:01.00,0:00:04.50,Translation", content)
            # Kiểm tra tag karaoke của libass {\kf...}
            self.assertIn(r"{\kf", content)

if __name__ == "__main__":
    unittest.main()
