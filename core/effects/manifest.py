"""
AI FILE NOTE - EFFECTS MANIFEST (SỔ ĐĂNG KÝ HIỆU ỨNG VIDEO)
Chức năng chính:
- Registry metadata bền vững, tự phục hồi cho các file hiệu ứng video cục bộ (manifest.json).
- Ghi file atomic (mkstemp + os.replace + fsync) để rerun Streamlit không để lại JSON dở dang.
- Đối chiếu (reconcile) manifest với file thực trên đĩa: thêm mới, cập nhật, đánh dấu missing, phát hiện trùng theo sha256.
- Suy luận metadata cho asset cũ (pixabay_/pexels_/built-in) và chuẩn hóa entry.
Đầu vào chính:
- effects_dir (thư mục chứa video hiệu ứng); dict metadata khi đăng ký; đường dẫn file hiệu ứng.
Đầu ra chính:
- File manifest.json (schema_version, updated_at, effects[]); dict/list bản ghi hiệu ứng; dict thống kê reconcile.
API được file khác sử dụng:
- load_manifest(), save_manifest(), register_effect(), reconcile_manifest(), remove_missing_entries(),
  get_effect_metadata(), list_effect_records(), manifest_path(), sha256_file(); hằng SCHEMA_VERSION, SUPPORTED_EXTENSIONS.
Phụ thuộc quan trọng:
- Chỉ thư viện chuẩn: hashlib, json, os, re, shutil, tempfile, time, datetime, pathlib.
Lưu ý khi sửa:
- Khi manifest hỏng, KHÔNG chặn app: giữ bản sao manifest.invalid.<ts>.json rồi trả về manifest rỗng.
- File "effect_off.mp4" luôn được bỏ qua khi quét/reconcile.
- Đổi cấu trúc dữ liệu nhớ tăng SCHEMA_VERSION; save_manifest tự sort effects theo file_name (lowercase).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 2
MANIFEST_NAME = "manifest.json"
SUPPORTED_EXTENSIONS = {".mp4", ".mov", ".webm", ".mkv"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _empty() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "updated_at": _utc_now(), "effects": []}


def manifest_path(effects_dir: str | Path) -> Path:
    return Path(effects_dir) / MANIFEST_NAME


def load_manifest(effects_dir: str | Path) -> dict[str, Any]:
    """Load manifest. Preserve a corrupt copy instead of blocking the app."""
    path = manifest_path(effects_dir)
    if not path.is_file():
        return _empty()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("effects"), list):
            raise ValueError("Manifest không đúng cấu trúc")
        data["schema_version"] = SCHEMA_VERSION
        data.setdefault("updated_at", _utc_now())
        return data
    except Exception:
        backup = path.with_name(f"manifest.invalid.{int(time.time())}.json")
        try:
            shutil.copy2(path, backup)
        except OSError:
            pass
        return _empty()


def save_manifest(effects_dir: str | Path, data: dict[str, Any]) -> Path:
    """Write atomically so a Streamlit rerun cannot leave half-written JSON."""
    directory = Path(effects_dir)
    directory.mkdir(parents=True, exist_ok=True)
    payload = dict(data)
    payload["schema_version"] = SCHEMA_VERSION
    payload["updated_at"] = _utc_now()
    payload["effects"] = sorted(payload.get("effects", []), key=lambda item: str(item.get("file_name", "")).lower())
    path = manifest_path(directory)
    fd, temporary = tempfile.mkstemp(prefix="effect_manifest_", suffix=".tmp", dir=directory)
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


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tags_from_name(file_name: str) -> list[str]:
    ignored = {"effect", "default", "pixabay", "pexels", "video", "overlay"}
    tokens = re.sub(r"[^a-z0-9]+", " ", Path(file_name).stem.lower()).split()
    return [token for token in tokens if token not in ignored and not token.isdigit()]


def _infer_legacy_metadata(path: Path) -> dict[str, Any]:
    name = path.name
    lower = name.lower()
    provider = "built-in"
    license_name = "Hiệu ứng tích hợp sẵn"
    source_page_url = None
    provider_asset_id = None
    if lower.startswith("pixabay_"):
        provider = "pixabay"
        license_name = "Pixabay Content License"
        source_page_url = "https://pixabay.com/videos/"
        match = re.search(r"pixabay_(\d+)", lower)
        provider_asset_id = int(match.group(1)) if match else None
    elif lower.startswith("pexels_"):
        provider = "pexels"
        license_name = "Pexels License - cần kiểm tra nguồn gốc"
        source_page_url = "https://www.pexels.com/videos/"
    return {
        "file_name": name,
        "provider": provider,
        "provider_asset_id": provider_asset_id,
        "source_page_url": source_page_url,
        "license_name": license_name,
        "tags": _tags_from_name(name),
        "status": "ready",
        "metadata_origin": "local_scan",
    }


def _normalize_entry(entry: dict[str, Any], effects_dir: Path) -> dict[str, Any]:
    item = dict(entry)
    name = Path(str(item.get("file_name") or "")).name
    item["file_name"] = name
    path = effects_dir / name
    item.setdefault("provider", "local")
    item.setdefault("license_name", "Chưa xác định")
    item.setdefault("tags", _tags_from_name(name))
    item.setdefault("registered_at", _utc_now())
    if path.is_file():
        stat = path.stat()
        item["status"] = "ready"
        item["file_size"] = stat.st_size
        item["modified_at_ns"] = stat.st_mtime_ns
    else:
        item["status"] = "missing"
    return item


def register_effect(effects_dir: str | Path, metadata: dict[str, Any]) -> dict[str, Any]:
    directory = Path(effects_dir)
    data = load_manifest(directory)
    file_name = Path(str(metadata.get("file_name") or "")).name
    if not file_name:
        raise ValueError("Metadata hiệu ứng thiếu file_name")
    previous = next((entry for entry in data["effects"] if entry.get("file_name") == file_name), {})
    item = dict(previous)
    item.update(metadata)
    item["file_name"] = file_name
    item.setdefault("registered_at", _utc_now())
    item = _normalize_entry(item, directory)
    data["effects"] = [entry for entry in data["effects"] if entry.get("file_name") != file_name]
    data["effects"].append(item)
    save_manifest(directory, data)
    return item


def reconcile_manifest(effects_dir: str | Path, calculate_hashes: bool = False) -> dict[str, Any]:
    """Sync manifest with files, migrate legacy assets, and flag missing entries."""
    directory = Path(effects_dir)
    directory.mkdir(parents=True, exist_ok=True)
    data = load_manifest(directory)
    by_name = {
        Path(str(entry.get("file_name") or "")).name: dict(entry)
        for entry in data.get("effects", []) if entry.get("file_name")
    }
    added = updated = missing = duplicate_hashes = 0
    files = sorted(path for path in directory.iterdir() if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS)
    for path in files:
        if path.name == "effect_off.mp4":
            continue
        old = by_name.get(path.name)
        item = dict(old or _infer_legacy_metadata(path))
        if old is None:
            added += 1
        stat = path.stat()
        if old and (old.get("file_size") != stat.st_size or old.get("modified_at_ns") != stat.st_mtime_ns):
            updated += 1
        item = _normalize_entry(item, directory)
        if calculate_hashes or not item.get("sha256"):
            item["sha256"] = sha256_file(path)
        by_name[path.name] = item

    for name, entry in list(by_name.items()):
        path = directory / name
        if name != "effect_off.mp4" and not path.is_file():
            entry = _normalize_entry(entry, directory)
            by_name[name] = entry
            missing += 1

    seen_hashes: dict[str, str] = {}
    for item in by_name.values():
        digest = str(item.get("sha256") or "")
        if not digest or item.get("status") != "ready":
            continue
        if digest in seen_hashes:
            item["duplicate_of"] = seen_hashes[digest]
            duplicate_hashes += 1
        else:
            item.pop("duplicate_of", None)
            seen_hashes[digest] = item["file_name"]

    data["effects"] = list(by_name.values())
    save_manifest(directory, data)
    return {
        "manifest_path": str(manifest_path(directory)),
        "total": len(data["effects"]),
        "ready": sum(1 for item in data["effects"] if item.get("status") == "ready"),
        "missing": missing,
        "added": added,
        "updated": updated,
        "duplicate_hashes": duplicate_hashes,
    }


def remove_missing_entries(effects_dir: str | Path) -> int:
    directory = Path(effects_dir)
    data = load_manifest(directory)
    before = len(data["effects"])
    data["effects"] = [entry for entry in data["effects"] if (directory / Path(str(entry.get("file_name") or "")).name).is_file()]
    save_manifest(directory, data)
    return before - len(data["effects"])


def get_effect_metadata(effects_dir: str | Path, effect_path: str | Path) -> dict[str, Any]:
    name = Path(effect_path).name
    for entry in load_manifest(effects_dir).get("effects", []):
        if entry.get("file_name") == name:
            return dict(entry)
    return _infer_legacy_metadata(Path(effect_path))


def list_effect_records(effects_dir: str | Path, include_missing: bool = False) -> list[dict[str, Any]]:
    records = load_manifest(effects_dir).get("effects", [])
    if include_missing:
        return [dict(item) for item in records]
    return [dict(item) for item in records if item.get("status") == "ready"]
