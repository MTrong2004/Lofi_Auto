"""
AI FILE NOTE - TEXT EFFECT MANIFEST (lưu profile chữ theo project)

Chức năng chính:
- Lưu/đọc profile chữ đã duyệt theo project_id, ghi file nguyên tử (write -> fsync -> os.replace).
- Không lưu API key. Nâng cấp schema an toàn.

API được file khác sử dụng:
- save_text_profile(), load_text_profile()

Lưu ý khi sửa:
- Theo đúng khuôn ghi nguyên tử của core/effect_manifest.py.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config

SCHEMA_VERSION = 1


def _manifest_path(project_id: str) -> Path:
    safe = "".join(ch for ch in str(project_id or "default") if ch.isalnum() or ch in "-_") or "default"
    return Path(config.METADATA_DIR) / f"{safe}_text_effect.json"


def save_text_profile(project_id: str, profile: dict[str, Any]) -> Path:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "project_id": str(project_id),
        "profile": dict(profile or {}),
    }
    payload["profile"].pop("api_key", None)  # phòng xa: không bao giờ lưu key
    path = _manifest_path(project_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="text_effect_", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return path


def load_text_profile(project_id: str) -> dict[str, Any] | None:
    path = _manifest_path(project_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        profile = data.get("profile")
        return profile if isinstance(profile, dict) else None
    except Exception:
        return None
