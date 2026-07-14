"""
Bước 5 - Tự động upload và đặt lịch phát sóng qua YouTube Data API v3.
"""
import logging
import random
from datetime import datetime, timedelta
from pathlib import Path

import config
from utils.metadata_store import MetadataStore

logger = logging.getLogger("lofi_automation")
store = MetadataStore(config.METADATA_DIR)


def get_authenticated_service():
    """
    Xác thực OAuth2 với YouTube Data API.
    Cần file client_secret.json tải từ Google Cloud Console.
    """
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    scopes = ["https://www.googleapis.com/auth/youtube.upload"]
    creds = None
    if config.YOUTUBE_TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(config.YOUTUBE_TOKEN_FILE), scopes)
    
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not config.YOUTUBE_CLIENT_SECRETS_FILE.exists():
                raise FileNotFoundError(
                    f"Không tìm thấy file client_secret.json tại: {config.YOUTUBE_CLIENT_SECRETS_FILE}. "
                    "Vui lòng tạo OAuth client ID trên Google Cloud Console, tải về cấu hình json và đặt tại đường dẫn trên."
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                str(config.YOUTUBE_CLIENT_SECRETS_FILE), scopes
            )
            creds = flow.run_local_server(port=0)
        
        # Lưu token cho lần chạy tiếp theo
        config.YOUTUBE_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        config.YOUTUBE_TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
        
    return build("youtube", "v3", credentials=creds)


def build_video_metadata(track_id: str, video_index: int) -> dict:
    """Sinh title/description/tags tự động, kèm credit từ metadata Bước 1."""
    credit_text = store.build_credit_text(track_id)
    title = f"Lofi Chill Beats to Study/Relax #{video_index} - 1 Hour"
    description = (
        "1 hour of chill lofi beats for studying, relaxing, or working.\n\n"
        f"{credit_text}\n\n"
        "#lofi #chillbeats #studymusic"
    )
    tags = ["lofi", "chill beats", "study music", "relax music", "1 hour lofi"]
    return {"title": title, "description": description, "tags": tags}


def pick_schedule_time() -> datetime:
    """Chọn 1 khung giờ traffic cao còn trống trong ngày để đặt lịch."""
    hour = random.choice(config.SCHEDULE_HOURS)
    target = datetime.now().replace(hour=hour, minute=0, second=0, microsecond=0)
    if target < datetime.now():
        target += timedelta(days=1)
    return target


def upload_video(video_path: Path, track_id: str, video_index: int, title: str = None, description: str = None, tags: list = None, privacy: str = None) -> str:
    """
    Upload video ở trạng thái Private, sau đó chuyển Scheduled.
    """
    from googleapiclient.http import MediaFileUpload
    
    service = get_authenticated_service()
    default_metadata = build_video_metadata(track_id, video_index)
    
    final_title = title if title is not None else default_metadata["title"]
    final_description = description if description is not None else default_metadata["description"]
    final_tags = tags if tags is not None else default_metadata["tags"]
    final_privacy = privacy if privacy is not None else config.UPLOAD_PRIVACY_INITIAL
    
    schedule_time = pick_schedule_time()
    schedule_iso = schedule_time.astimezone().isoformat()
    
    logger.info(f"Upload '{final_title}', trạng thái: {final_privacy}")
    
    body = {
        "snippet": {
            "title": final_title,
            "description": final_description,
            "tags": final_tags,
            "categoryId": "10",
        },
        "status": {
            "privacyStatus": final_privacy,
            "selfDeclaredMadeForKids": False,
        }
    }
    # Chỉ đặt lịch nếu để chế độ private mặc định và không chỉ định ghi đè privacy công khai trực tiếp
    if final_privacy == "private" and not privacy:
        body["status"]["publishAt"] = schedule_iso
    
    media = MediaFileUpload(
        str(video_path),
        mimetype="video/mp4",
        chunksize=1024 * 1024 * 10,
        resumable=True
    )
    
    request = service.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media
    )
    
    logger.info("Bắt đầu upload video lên YouTube...")
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            logger.info(f"Đã upload {int(status.progress() * 100)}%...")
            
    video_id = response.get("id")
    logger.info(f"Upload thành công! Video ID: {video_id}")
    return video_id
