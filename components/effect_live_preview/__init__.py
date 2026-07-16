"""Browser-side live preview for image motion and video overlays.

This component intentionally performs no FFmpeg rendering. It embeds local assets
as data URLs so the browser can composite them immediately.

Compositing modes (đồng bộ khái niệm với core/effect_compositor.py):
- chroma_key: WebGL shader tách phông xanh thật (CSS blend không làm được),
  có despill, similarity/softness và chế độ xem matte.
- screen_black/alpha/normal: CSS blending như trước (đủ chính xác cho preview).
"""
from __future__ import annotations

import base64
import json
import mimetypes
from functools import lru_cache
from pathlib import Path

import streamlit.components.v1 as components

_FRONTEND_DIR = Path(__file__).parent / "frontend"
_MAX_EMBED_BYTES = 40 * 1024 * 1024


@lru_cache(maxsize=32)
def _file_to_data_url(path_text: str, modified_ns: int, file_size: int) -> str:
    path = Path(path_text)
    if file_size > _MAX_EMBED_BYTES:
        raise ValueError("File hiệu ứng vượt 40 MB, không phù hợp Live Preview trong trình duyệt.")
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _data_url(path_value: str | Path) -> str:
    path = Path(path_value).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Không tìm thấy asset Live Preview: {path}")
    stat = path.stat()
    return _file_to_data_url(str(path), stat.st_mtime_ns, stat.st_size)


_FONT_STACKS = {
    "sans": "'Segoe UI','Microsoft YaHei',Arial,sans-serif",
    "serif": "'Times New Roman','SimSun',Georgia,serif",
    "display": "'Arial Black','Microsoft YaHei',Impact,sans-serif",
}


def _text_payload(profile: dict | None) -> dict | None:
    if not profile or not profile.get("enabled"):
        return None
    try:
        from core.text.provider import normalize_text_profile
        p = normalize_text_profile(profile)
        content = p.get("content", "").strip()
        if not content:
            return None
        return {
            "content": content,
            "position": p["position"],
            "textColor": p["text_color"],
            "outlineColor": p["outline_color"],
            "outlineWidth": float(p["outline_width"]),
            "fontSize": int(p["font_size"]),
            "bold": bool(p["bold"]),
            "fontStyle": p["font_style"],
            "introEffect": p.get("intro_effect") or "fade",
            "holdEffect": p.get("hold_effect") or "none",
            "outroEffect": p.get("outro_effect") or "fade",
            "introDuration": float(p.get("intro_duration") or 0.8),
            "outroDuration": float(p.get("outro_duration") or 1.0),
            "startSeconds": p.get("start_seconds"),
            "endSeconds": p.get("end_seconds"),
        }
    except Exception:
        return None


def effect_live_preview(
    image_path: str | Path,
    effect_path: str | Path,
    *,
    opacity: float = 0.55,
    speed: float = 1.0,
    blend_mode: str = "screen",
    motion_mode: str = "smooth_zoom",
    effect_type: str = "screen_black",
    key_color: str = "#00FF00",
    chroma_similarity: float = 0.18,
    chroma_softness: float = 0.08,
    despill: float = 0.35,
    show_matte: bool = False,
    quality: str = "fast",
    text_profile: dict | None = None,
    height: int = 470,
) -> None:
    """Render a fast browser preview without producing a temporary MP4."""
    safe_blends = {"screen", "lighten", "overlay", "soft-light", "normal"}
    safe_motions = {"smooth_zoom", "parallax"}
    safe_types = {"screen_black", "chroma_key", "alpha", "normal"}
    key = str(key_color or "#00FF00").lstrip("#")
    if len(key) != 6:
        key = "00FF00"
    payload = {
        "imageSrc": _data_url(image_path),
        "effectSrc": _data_url(effect_path),
        "opacity": max(0.0, min(float(opacity), 1.0)),
        "speed": max(0.25, min(float(speed), 2.0)),
        "blendMode": blend_mode if blend_mode in safe_blends else "screen",
        "motionMode": motion_mode if motion_mode in safe_motions else "smooth_zoom",
        "effectType": effect_type if effect_type in safe_types else "screen_black",
        "keyColor": [int(key[0:2], 16) / 255.0, int(key[2:4], 16) / 255.0, int(key[4:6], 16) / 255.0],
        "similarity": max(0.01, min(float(chroma_similarity), 0.6)),
        "softness": max(0.0, min(float(chroma_softness), 0.5)),
        "despill": max(0.0, min(float(despill), 1.0)),
        "showMatte": bool(show_matte),
        # fast: 640x360 (nhẹ máy), sharp: 960x540 (rõ nét hơn)
        "canvasWidth": 960 if quality == "sharp" else 640,
        "canvasHeight": 540 if quality == "sharp" else 360,
        "text": _text_payload(text_profile),
    }
    template = (_FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
    styles = (_FRONTEND_DIR / "preview.css").read_text(encoding="utf-8")
    script = (_FRONTEND_DIR / "preview.js").read_text(encoding="utf-8")
    payload_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    document = (
        f"<style>{styles}</style>"
        f"{template}"
        f'<script id="live-preview-payload" type="application/json">{payload_json}</script>'
        f"<script>{script}</script>"
    )
    components.html(document, height=max(280, int(height)), scrolling=False)
