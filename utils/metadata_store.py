"""
Ghi và đọc metadata (license nhạc, nguồn ảnh, lịch sử track đã dùng).
Dùng file JSON đơn giản - đủ cho quy mô vài video/ngày, không cần DB.
"""
import json
from datetime import datetime
from pathlib import Path


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
