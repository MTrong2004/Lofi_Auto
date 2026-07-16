import functools
import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path
import requests

import config

logger = logging.getLogger("lofi_automation")

# =====================================================================
# Retry Helper
# =====================================================================

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


# =====================================================================
# Metadata Store
# =====================================================================

class MetadataStore:
    def __init__(self, metadata_dir: Path):
        self.metadata_dir = metadata_dir
        self.used_tracks_file = metadata_dir / "used_tracks.json"
        if not self.used_tracks_file.exists():
            self.used_tracks_file.write_text("[]", encoding="utf-8")

    def save_track_metadata(self, track_id: str, source: str, license_type: str,
                             author: str, original_url: str) -> Path:
        """Lưu metadata cho 1 track vừa tải, kèm timestamp."""
        record = {
            "track_id": track_id,
            "source": source,
            "license": license_type,
            "author": author,
            "original_url": original_url,
            "downloaded_at": datetime.now().isoformat(),
        }
        out_path = self.metadata_dir / f"{track_id}.json"
        out_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        self._append_used_track(track_id)
        return out_path

    def _append_used_track(self, track_id: str):
        used = json.loads(self.used_tracks_file.read_text(encoding="utf-8"))
        if track_id not in used:
            used.append(track_id)
            self.used_tracks_file.write_text(json.dumps(used, ensure_ascii=False, indent=2), encoding="utf-8")

    def is_track_used(self, track_id: str) -> bool:
        used = json.loads(self.used_tracks_file.read_text(encoding="utf-8"))
        return track_id in used

    def build_credit_text(self, track_id: str) -> str:
        """Sinh đoạn credit chuẩn Creative Commons để chèn vào description YouTube."""
        meta_path = self.metadata_dir / f"{track_id}.json"
        if not meta_path.exists():
            return ""
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        return (
            f"Music: \"{meta['track_id']}\" by {meta['author']}\n"
            f"License: {meta['license']}\n"
            f"Source: {meta['original_url']}"
        )


# =====================================================================
# Prompt Generator
# =====================================================================

_SYSTEM_INSTRUCTION = (
    "You write image-generation prompts for lofi anime background art used in chill music videos. "
    "Given a song title, respond with EXACTLY ONE prompt in English (45-60 words), describing a single "
    "cozy anime scene that matches the song's mood. The scene MUST have three explicit depth layers, "
    "and each layer should contain elements that animate well in a 2.5D parallax video:\n"
    "- near foreground: prefer a potted plant, leaves, grass or flowers close to the camera (they can sway)\n"
    "- midground: the main static subject (desk, room, house, cafe table...)\n"
    "- far background: prefer drifting clouds / open sky, OR a distant city skyline with glowing window "
    "and street lights (they can flicker)\n"
    "No people's faces close-up, no text, no logo, no camera brands. "
    "Respond with the prompt only - no quotes, no explanations, no markdown."
)


def _call_llm(user_message: str, timeout: int = 40) -> str:
    api_url = getattr(config, "PROMPT_API_URL", "https://text.pollinations.ai/openai")
    api_key = getattr(config, "PROMPT_API_KEY", "")
    model = getattr(config, "PROMPT_API_MODEL", "openai")

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM_INSTRUCTION},
            {"role": "user", "content": user_message},
        ],
        "temperature": 1.0,
        "max_tokens": 220,
    }
    r = requests.post(api_url, headers=headers, json=payload, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
    return content.strip()


def _clean_prompt(text: str) -> str:
    """Làm sạch output LLM: bỏ markdown/quote/tiền tố thừa, ép về 1 dòng."""
    text = (text or "").strip()
    text = re.sub(r"^```.*?$", "", text, flags=re.MULTILINE)
    text = text.replace("\n", " ").strip()
    text = text.strip('"').strip("'").strip()
    text = re.sub(r"^(prompt\s*:\s*)", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s{2,}", " ", text)
    return text


def generate_prompt_from_track(track: dict, avoid: list = None) -> str:
    """
    Sinh prompt ảnh mới theo bài nhạc. Truyền `avoid` (các prompt đã dùng)
    để mỗi lần bấm ra một bối cảnh KHÁC nhưng vẫn hợp mood bài hát.
    Ném exception nếu LLM lỗi - bên gọi tự fallback heuristic.
    """
    title = (track or {}).get("title") or "lofi chill music"
    author = (track or {}).get("author") or ""

    user_message = f'Song title: "{title}"'
    if author:
        user_message += f' by {author}'

    avoid = [a for a in (avoid or []) if a]
    if avoid:
        recent = "\n".join(f"- {a[:160]}" for a in avoid[-5:])
        user_message += (
            "\n\nI already used these scenes for this song. Give me a DIFFERENT scene "
            f"(different location/time/weather) that still fits the mood:\n{recent}"
        )

    result = _clean_prompt(_call_llm(user_message))
    if len(result) < 30:
        raise ValueError(f"LLM trả về prompt quá ngắn: '{result}'")
    logger.info(f"[PromptGenerator] Đã sinh prompt LLM cho bài '{title}'.")
    return result
