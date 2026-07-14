"""
Decorator retry dùng chung - tránh pipeline chết ngang vì 1 lần API lỗi tạm thời.
"""
import time
import functools
import logging

logger = logging.getLogger("lofi_automation")


def retry(max_attempts: int = 3, delay_seconds: float = 2.0, backoff: float = 2.0):
    """
    Retry hàm khi có exception, tăng dần thời gian chờ giữa các lần (exponential backoff).
    Dùng cho: gọi Pollinations, gọi SD local, gọi YouTube API.
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            attempt = 0
            wait = delay_seconds
            while attempt < max_attempts:
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    attempt += 1
                    logger.warning(f"[retry] {func.__name__} lỗi lần {attempt}/{max_attempts}: {e}")
                    if attempt >= max_attempts:
                        raise
                    time.sleep(wait)
                    wait *= backoff
        return wrapper
    return decorator
