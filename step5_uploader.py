"""
AI FILE NOTE - STEP 5: YOUTUBE UPLOADER AND SCHEDULER

Chức năng chính:
- Xác thực OAuth2 với YouTube Data API v3 và lưu token để tái sử dụng.
- Sinh title, description, tags và credit từ metadata nhạc của Bước 1.
- Chọn thời điểm phát theo config.SCHEDULE_HOURS.
- Upload MP4 theo kiểu resumable, theo dõi phần trăm và trả về YouTube video ID.
- Có thể đặt privacy thủ công; mặc định private có thể kèm publishAt để lên lịch.

Đầu vào chính:
- video_path, track_id, video_index và metadata/privacy ghi đè tùy chọn.

Đầu ra chính:
- Chuỗi YouTube video ID sau khi upload thành công.

API được file khác sử dụng:
- get_authenticated_service()
- build_video_metadata(), pick_schedule_time()
- upload_video() (hỗ trợ progress_callback và publish_at)
- get_upload_prerequisites() (UI kiểm tra thư viện Google + client_secret trước khi upload)

Phụ thuộc quan trọng:
- google-auth-oauthlib, google-api-python-client, config, MetadataStore.
- Cần client_secret.json hợp lệ; token OAuth được lưu tại config.YOUTUBE_TOKEN_FILE.

Lưu ý khi sửa:
- Không ghi API secret hoặc token trực tiếp vào mã nguồn/log.
- Phải kiểm tra video_path tồn tại trước khi upload và giữ resumable upload cho file lớn.
- publishAt chỉ hợp lệ với video private; cần xử lý múi giờ rõ ràng khi đổi logic lịch đăng.
- Giữ cấu trúc metadata và credit để tránh mất thông tin nguồn nhạc.
"""
import logging
import random
from datetime import datetime, timedelta
from pathlib import Path

import config
from utils.helpers import MetadataStore

logger = logging.getLogger("lofi_automation")
store = MetadataStore(config.METADATA_DIR)


def get_upload_prerequisites() -> dict:
    """Kiểm tra điều kiện upload để UI báo thiếu gì thay vì lỗi giữa chừng."""
    try:
        import googleapiclient  # noqa: F401
        import google_auth_oauthlib  # noqa: F401
        google_libs = True
    except ImportError:
        google_libs = False
    return {
        "google_libs": google_libs,
        "client_secret": config.YOUTUBE_CLIENT_SECRETS_FILE.exists(),
        "token": config.YOUTUBE_TOKEN_FILE.exists(),
        "client_secret_path": str(config.YOUTUBE_CLIENT_SECRETS_FILE),
        "install_hint": "pip install google-auth-oauthlib google-api-python-client",
    }


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


def upload_video(
    video_path: Path,
    track_id: str,
    video_index: int,
    title: str = None,
    description: str = None,
    tags: list = None,
    privacy: str = None,
    publish_at: datetime = None,
    progress_callback=None,
) -> str:
    """
    Upload video resumable lên YouTube.
    - privacy=None: private + tự đặt lịch theo khung giờ traffic cao (hành vi cũ).
    - publish_at: thời điểm đăng cụ thể (chỉ hợp lệ khi privacy là private).
    - progress_callback(percent 0.0-1.0): UI hiển thị tiến độ.
    """
    from googleapiclient.http import MediaFileUpload

    video_path = Path(video_path)
    if not video_path.is_file():
        raise FileNotFoundError(f"Không tìm thấy file video để upload: {video_path}")

    service = get_authenticated_service()
    default_metadata = build_video_metadata(track_id, video_index)

    final_title = title if title is not None else default_metadata["title"]
    final_description = description if description is not None else default_metadata["description"]
    final_tags = tags if tags is not None else default_metadata["tags"]
    final_privacy = privacy if privacy is not None else config.UPLOAD_PRIVACY_INITIAL

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
    if final_privacy == "private":
        if publish_at is not None:
            body["status"]["publishAt"] = publish_at.astimezone().isoformat()
        elif not privacy:
            # Hành vi cũ: private mặc định thì tự đặt lịch khung giờ traffic cao.
            body["status"]["publishAt"] = pick_schedule_time().astimezone().isoformat()

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
            percent = float(status.progress())
            logger.info(f"Đã upload {int(percent * 100)}%...")
            if progress_callback:
                try:
                    progress_callback(percent)
                except Exception:
                    pass

    video_id = response.get("id")
    if progress_callback:
        try:
            progress_callback(1.0)
        except Exception:
            pass
    logger.info(f"Upload thành công! Video ID: {video_id}")
    return video_id
