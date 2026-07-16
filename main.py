"""
Điều phối toàn bộ pipeline: Bước 1 -> 2 -> (Duyệt qua Streamlit) -> 3 -> 4 -> 5.
Chạy bởi Cron Job / Task Scheduler vào khung giờ 1h-5h sáng.

Lưu ý: main.py này giả định việc duyệt đã được thực hiện riêng qua
`streamlit run review_app.py`. Muốn tự động hoàn toàn (bỏ qua duyệt tay),
xóa lời gọi duyệt và để pipeline chạy thẳng 1 -> 2 -> 3 -> 4 -> 5.
"""
import logging

import config
import step1_music_hunter
import step2_image_provider
import step3_effect_provider
import step4_render
import step5_uploader

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("lofi_automation")


def cleanup_temp_files(*paths):
    """Xóa file tạm sau khi upload thành công, tránh đầy ổ đĩa."""
    for p in paths:
        try:
            p.unlink(missing_ok=True)
        except Exception as e:
            logger.warning(f"Không xóa được {p}: {e}")


def run_pipeline_once(video_index: int):
    logger.info(f"=== Bắt đầu video #{video_index} ===")

    from datetime import datetime, timezone
    import core.runtime.db
    from core.runtime.project_manager import ProjectManager
    
    # 1. Khởi tạo DB
    core.runtime.db.init_db()
    
    # 2. Tạo mã dự án ngẫu nhiên và đăng ký
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    project_id = f"lofi_proj_{video_index}_{timestamp}"
    ProjectManager.create_project(project_id)

    audio_result = step1_music_hunter.run_step1(project_id=project_id)
    image_path = step2_image_provider.get_background_image(index=video_index, project_id=project_id)
    effect_path = step3_effect_provider.pick_effect_video()

    text_profile = None
    if getattr(config, "TEXT_EFFECT_AI_ENABLED", True):
        try:
            from core.text.effect_manifest import load_text_profile, save_text_profile
            from core.text import provider as text_effect_provider
            text_profile = load_text_profile(project_id)
            if not text_profile:
                logger.info("Đang sinh gợi ý chữ động bằng AI...")
                text_profile = text_effect_provider.build_ai_text_profile(
                    track=audio_result,
                    music_tags=[],
                    image_context=image_path.name,
                    content="",
                )
                save_text_profile(project_id, text_profile)
        except Exception as exc:
            logger.warning(f"Không tạo được profile chữ động tự động: {exc}")

    final_video = step4_render.run_step4(
        project_id=project_id,
        audio_path=audio_result["audio_path"],
        image_path=image_path,
        effect_path=effect_path,
        text_profile=text_profile,
    )

    if not getattr(config, "ENABLE_YOUTUBE_UPLOAD", True):
        logger.info("Bỏ qua bước upload YouTube theo cấu hình config.ENABLE_YOUTUBE_UPLOAD.")
        logger.info(f"Video thành phẩm đã được lưu tại: {final_video}")
        cleanup_temp_files(audio_result["audio_path"], image_path)
        logger.info(f"=== Hoàn tất video #{video_index} ===")
        return

    try:
        youtube_video_id = step5_uploader.upload_video(
            video_path=final_video,
            track_id=audio_result["track_id"],
            video_index=video_index,
        )
        logger.info(f"Upload xong: {youtube_video_id}")
        cleanup_temp_files(audio_result["audio_path"], image_path, final_video)
    except FileNotFoundError as e:
        logger.warning(f"Bỏ qua bước upload YouTube: {e}")
        logger.info(f"Video thành phẩm đã được lưu tại: {final_video}")
        cleanup_temp_files(audio_result["audio_path"], image_path)
    logger.info(f"=== Hoàn tất video #{video_index} ===")


def run_batch(count: int = config.DAILY_VIDEO_TARGET):
    for i in range(1, count + 1):
        try:
            run_pipeline_once(video_index=i)
        except Exception as e:
            logger.error(f"Video #{i} lỗi, bỏ qua và tiếp tục batch: {e}")


if __name__ == "__main__":
    import sys
    if "--test" in sys.argv:
        logger.info("Chạy pipeline ở chế độ TEST (1 video, 10 giây)...")
        config.VIDEO_DURATION_SECONDS = 10
        config.ENABLE_YOUTUBE_UPLOAD = False
        
        # Mock is_track_used trong test để tránh lỗi hết track trong batch
        import step1_music_hunter
        original_is_track_used = step1_music_hunter.store.is_track_used
        step1_music_hunter.store.is_track_used = lambda x: False
        
        try:
            run_batch(count=1)
        finally:
            step1_music_hunter.store.is_track_used = original_is_track_used
    else:
        run_batch()
