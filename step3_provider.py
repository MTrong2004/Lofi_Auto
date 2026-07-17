"""
AI FILE NOTE - STEP 3 PROVIDER (LYRIC + EFFECT)
Chức năng chính:
- Gộp hai nhóm chức năng của Step 3: (1) xử lý vocal, nhận dạng lời (Whisper), tìm lời online (LRCLIB/Musixmatch), dịch tiếng Việt + Pinyin và xuất phụ đề .ass; (2) tìm/tải/tạo/phân tích và đề xuất hiệu ứng overlay (Pexels, Pixabay, bộ hiệu ứng dựng bằng FFmpeg).
- Chuẩn hóa, xếp hạng candidate lời theo metadata; đề xuất hiệu ứng local-first rồi mới gọi API online.
Đầu vào chính:
- audio/vocal path, thông tin bài (title/artist/album/duration), segments lời; từ khóa và API key (Pexels/Pixabay/Musixmatch); dict profile hiệu ứng từ AI.
Đầu ra chính:
- File vocal/instrumental, list segments (kèm words/pinyin/vietnamese), file .ass; file .mp4 hiệu ứng trong data/effects kèm manifest + credit.
API được file khác sử dụng:
- run_vocal_separation, run_transcription, search_online_lyrics, find_online_lyrics, auto_translate_and_pinyin, generate_subtitles_file, load/save_project_subtitles, list_effect_videos, download_pexels_effect, create_builtin_effect_pack, pick_effect_video, search_pixabay_effects, download_pixabay_effect, recommend_effects, build_ai_effect_profile, analyze_effect_type.
Phụ thuộc quan trọng:
- config, requests, subprocess (FFmpeg/ffprobe); core.lyrics.*, core.text.ass_renderer (được reload), core.effects.* (manifest/recommender/analyzer).
Lưu ý khi sửa:
- URL tải Pixabay phải qua `_allowed_pixabay_url` (chỉ domain Pixabay, https); giữ giới hạn dung lượng EFFECT_MAX_DOWNLOAD_MB và tải qua file .part rồi replace.
- Lời bài hát chỉ lấy/chấm điểm theo metadata, KHÔNG dùng AI sáng tác/sửa lời.
- renderer được importlib.reload ngay khi import; đừng bỏ dòng reload nếu chưa chắc cache Streamlit.
"""
from __future__ import annotations

import importlib
import logging
import os
import random
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import requests

# Đảm bảo import được config.py từ thư mục cha.
sys.path.append(str(Path(__file__).parent.parent))
import config

# Các module cốt lõi xử lý lyric và phụ đề.
import core.lyrics.vocal_separator as separator
import core.lyrics.transcriber as transcriber
import core.lyrics.translator as translator
import core.text.ass_renderer as renderer

# Streamlit giữ module trong cache giữa các lần rerun. Reload để bản renderer
# vừa gộp manifest được dùng ngay, thay vì bản cũ thiếu các hàm manifest.
renderer = importlib.reload(renderer)

logger = logging.getLogger("lofi.step3_provider")
_LRCLIB_GET_URL = "https://lrclib.net/api/get"
_LRCLIB_SEARCH_URL = "https://lrclib.net/api/search"
_MUSIXMATCH_SUBTITLE_URL = "https://api.musixmatch.com/ws/1.1/matcher.subtitle.get"
_LYRICS_HEADERS = {"User-Agent": "LoFi-Studio/1.0"}
_TIMED_LINE = re.compile(r"^\[(?P<minutes>\d{1,3}):(?P<seconds>\d{2}(?:\.\d{1,3})?)\](?P<text>.*)$")

def get_vocals_dir() -> Path:
    """Trả về thư mục lưu trữ file vocal tách và phụ đề."""
    path = Path("data/subtitles")
    path.mkdir(parents=True, exist_ok=True)
    return path

def run_vocal_separation(
    audio_path: Path,
    model: str = "htdemucs",
    progress_callback = None
) -> tuple[Path, Path]:
    """Tách vocal và instrumental."""
    return separator.separate_vocals(
        input_audio=audio_path,
        output_dir=get_vocals_dir(),
        model=model,
        progress_callback=progress_callback
    )

def run_transcription(
    vocal_path: Path,
    model_name: str = "base",
    language: str | None = None,
    progress_callback = None
) -> list[dict[str, Any]]:
    """Nhận diện lời từ vocal."""
    return transcriber.transcribe_vocals(
        vocal_path=vocal_path,
        model_name=model_name,
        language=language,
        progress_callback=progress_callback
    )

def _lyrics_candidate(item: dict[str, Any], duration: float, source: str) -> dict[str, Any] | None:
    """Chuẩn hóa một kết quả lyric thành candidate dùng chung cho giao diện."""
    synced = item.get("syncedLyrics") or item.get("subtitle_body")
    plain = item.get("plainLyrics") or item.get("lyrics_body")
    segments = _segments_from_synced(synced, duration) if synced else _segments_from_plain(plain or "", duration)
    if not segments:
        return None
    return {
        "source": source,
        "track_name": item.get("trackName") or item.get("track_name") or "",
        "artist_name": item.get("artistName") or item.get("artist_name") or "",
        "album_name": item.get("albumName") or item.get("album_name") or "",
        "duration": item.get("duration") or duration,
        "segments": segments,
        "timing": "synced" if synced else "estimated",
    }


def _lrclib_exact(title: str, artist: str, album: str, duration: float) -> dict[str, Any] | None:
    """Tìm exact match qua /api/get. Chỉ gọi khi đủ album theo yêu cầu LRCLIB."""
    if not title.strip() or not artist.strip() or not album.strip() or duration <= 0:
        return None
    response = requests.get(
        _LRCLIB_GET_URL,
        params={
            "track_name": title.strip(), "artist_name": artist.strip(),
            "album_name": album.strip(), "duration": int(round(duration)),
        },
        headers=_LYRICS_HEADERS, timeout=18,
    )
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return _lyrics_candidate(response.json(), duration, "LRCLIB exact")


def _normalize_match_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").casefold()).strip()


def _rank_lyric_candidate(item: dict[str, Any], title: str, artist: str, album: str, duration: float) -> float:
    """Chấm điểm metadata, không dùng AI để sáng tác hoặc sửa lời."""
    wanted_title = _normalize_match_text(title)
    wanted_artist = _normalize_match_text(artist)
    wanted_album = _normalize_match_text(album)
    got_title = _normalize_match_text(str(item.get("trackName") or ""))
    got_artist = _normalize_match_text(str(item.get("artistName") or ""))
    got_album = _normalize_match_text(str(item.get("albumName") or ""))
    score = 0.0
    score += 55 if got_title == wanted_title and wanted_title else 25 if wanted_title and wanted_title in got_title else 0
    score += 25 if got_artist == wanted_artist and wanted_artist else 12 if wanted_artist and wanted_artist in got_artist else 0
    score += 8 if wanted_album and got_album == wanted_album else 0
    try:
        gap = abs(float(item.get("duration") or duration) - duration)
    except (TypeError, ValueError):
        gap = 999.0
    score += max(0.0, 12.0 - min(gap, 12.0))
    score += 8 if item.get("syncedLyrics") else 0
    return score


def _lrclib_search(title: str, artist: str, album: str, duration: float, limit: int) -> list[dict[str, Any]]:
    response = requests.get(
        _LRCLIB_SEARCH_URL,
        params={"track_name": title.strip(), "artist_name": artist.strip()},
        headers=_LYRICS_HEADERS, timeout=15,
    )
    response.raise_for_status()
    results = response.json() or []
    if not results:
        response = requests.get(
            _LRCLIB_SEARCH_URL, params={"q": f"{title} {artist}".strip()},
            headers=_LYRICS_HEADERS, timeout=15,
        )
        response.raise_for_status()
        results = response.json() or []
    ranked = sorted(
        results,
        key=lambda item: _rank_lyric_candidate(item, title, artist, album, duration),
        reverse=True,
    )
    candidates = []
    for item in ranked[:max(1, limit)]:
        candidate = _lyrics_candidate(item, duration, "LRCLIB search")
        if candidate:
            candidate["match_score"] = round(_rank_lyric_candidate(item, title, artist, album, duration), 1)
            candidates.append(candidate)
    return candidates


def _musixmatch_candidate(title: str, artist: str, duration: float, api_key: str) -> dict[str, Any] | None:
    if not api_key.strip():
        return None
    response = requests.get(
        _MUSIXMATCH_SUBTITLE_URL,
        params={
            "apikey": api_key.strip(), "q_track": title.strip(),
            "q_artist": artist.strip(), "f_subtitle_length": int(round(duration)),
            "f_subtitle_length_max_deviation": 3,
        },
        headers=_LYRICS_HEADERS, timeout=18,
    )
    response.raise_for_status()
    message = (response.json() or {}).get("message") or {}
    if int((message.get("header") or {}).get("status_code") or 0) != 200:
        return None
    subtitle = ((message.get("body") or {}).get("subtitle") or {})
    body = str(subtitle.get("subtitle_body") or "").strip()
    if not body:
        return None
    return _lyrics_candidate({
        "track_name": title, "artist_name": artist,
        "duration": duration, "subtitle_body": body,
    }, duration, "Musixmatch")


def search_online_lyrics(
    title: str,
    artist: str,
    duration: float = 180.0,
    limit: int = 5,
    album: str = "",
    musixmatch_api_key: str = "",
) -> dict[str, Any]:
    """Exact LRCLIB -> LRCLIB search/ranking -> Musixmatch. Whisper do UI xử lý cuối."""
    title, artist, album = str(title or ""), str(artist or ""), str(album or "")
    if not title.strip():
        return {"found": False, "reason": "Thiếu tên bài hát để tìm lời."}
    errors: list[str] = []
    candidates: list[dict[str, Any]] = []

    try:
        exact = _lrclib_exact(title, artist, album, duration)
        if exact:
            exact["match_score"] = 100.0
            candidates.append(exact)
    except (requests.RequestException, ValueError, TypeError) as exc:
        errors.append(f"LRCLIB exact: {exc}")

    try:
        for candidate in _lrclib_search(title, artist, album, duration, limit):
            signature = (candidate["track_name"], candidate["artist_name"], candidate["album_name"], candidate["timing"])
            if not any((x["track_name"], x["artist_name"], x["album_name"], x["timing"]) == signature for x in candidates):
                candidates.append(candidate)
    except (requests.RequestException, ValueError, TypeError) as exc:
        errors.append(f"LRCLIB search: {exc}")

    key = (musixmatch_api_key or getattr(config, "MUSIXMATCH_API_KEY", "") or os.getenv("MUSIXMATCH_API_KEY", "")).strip()
    if key and not any(item.get("timing") == "synced" and float(item.get("match_score") or 0) >= 90 for item in candidates):
        try:
            mxm = _musixmatch_candidate(title, artist, duration, key)
            if mxm:
                mxm["match_score"] = 90.0
                candidates.append(mxm)
        except (requests.RequestException, ValueError, TypeError) as exc:
            errors.append(f"Musixmatch: {exc}")

    if not candidates:
        reason = "Không tìm thấy lời online. Hãy dùng Whisper để nhận dạng từ audio."
        if errors:
            reason += " " + " | ".join(errors)[:300]
        return {"found": False, "reason": reason, "fallback": "whisper", "errors": errors}
    candidates.sort(key=lambda item: (item.get("timing") == "synced", float(item.get("match_score") or 0)), reverse=True)
    return {"found": True, "candidates": candidates[:max(1, limit)], "errors": errors}


def find_online_lyrics(
    title: str, artist: str, duration: float = 180.0,
    album: str = "", musixmatch_api_key: str = "",
) -> dict[str, Any]:
    """Tương thích API cũ, trả candidate tốt nhất từ pipeline mới."""
    result = search_online_lyrics(title, artist, duration, 5, album, musixmatch_api_key)
    if not result.get("found"):
        return result
    best = result["candidates"][0]
    return {
        "found": True, "segments": best["segments"], "source": best["source"],
        "timing": best["timing"], "match_score": best.get("match_score"),
    }

def _word_timing(text: str, start: float, end: float) -> list[dict[str, Any]]:
    words = text.split()
    chunk = max(0.05, end - start) / max(1, len(words))
    return [{"word": word, "start": start + index * chunk, "end": start + (index + 1) * chunk} for index, word in enumerate(words)]

def _segments_from_synced(text: str, duration: float) -> list[dict[str, Any]]:
    rows = []
    for raw_line in text.splitlines():
        match = _TIMED_LINE.match(raw_line.strip())
        if match and match.group("text").strip():
            rows.append((int(match.group("minutes")) * 60 + float(match.group("seconds")), match.group("text").strip()))
    segments = []
    for index, (start, line) in enumerate(rows):
        end = max(start + 0.5, rows[index + 1][0] if index + 1 < len(rows) else duration)
        segments.append({"start": start, "end": end, "text": line, "words": _word_timing(line, start, end), "pinyin": "", "vietnamese": ""})
    return segments

def _segments_from_plain(text: str, duration: float) -> list[dict[str, Any]]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    chunk = max(1.5, duration / max(1, len(lines)))
    return [
        {"start": index * chunk, "end": min(duration, (index + 1) * chunk), "text": line,
         "words": _word_timing(line, index * chunk, min(duration, (index + 1) * chunk)), "pinyin": "", "vietnamese": ""}
        for index, line in enumerate(lines)
    ]

def auto_translate_and_pinyin(
    segments: list[dict[str, Any]],
    source_language: str,
    song_info: dict | None = None
) -> list[dict[str, Any]]:
    """
    Tự động dịch nghĩa tiếng Việt cho toàn bộ phân đoạn và tạo phiên âm Pinyin (nếu là tiếng Trung).
    """
    if not segments:
        return []
        
    original_texts = [seg.get("text", "").strip() for seg in segments]
    
    # 1. Sinh Pinyin nếu là tiếng Trung
    pinyin_lines = []
    if source_language == "zh":
        pinyin_lines = translator.generate_pinyin(original_texts)
    else:
        pinyin_lines = [""] * len(original_texts)
        
    # 2. Dịch nghĩa tiếng Việt bằng LLM
    vietnamese_lines = translator.translate_lyrics_to_vietnamese(original_texts, song_info)
    
    # 3. Gộp ngược lại vào segments
    for idx, seg in enumerate(segments):
        if idx < len(pinyin_lines):
            seg["pinyin"] = pinyin_lines[idx]
        if idx < len(vietnamese_lines):
            seg["vietnamese"] = vietnamese_lines[idx]
            
    return segments

def generate_subtitles_file(
    project_id: str,
    segments: list[dict[str, Any]],
    style_config: dict[str, Any]
) -> Path:
    """Tạo file phụ đề .ass karaoke hoàn chỉnh cho dự án."""
    output_path = get_vocals_dir() / f"{project_id}_subtitles.ass"
    return renderer.generate_ass_file(segments, style_config, output_path)

def load_project_subtitles(project_id: str) -> dict[str, Any]:
    """Tải cấu hình phụ đề dự án từ manifest."""
    return renderer.load_subtitle_manifest(project_id)

def save_project_subtitles(project_id: str, data: dict[str, Any]) -> None:
    """Lưu cấu hình phụ đề dự án vào manifest."""
    renderer.save_subtitle_manifest(project_id, data)


# -----------------------------------------------------------------------------
# EFFECT PROVIDER AND OVERLAYS
# -----------------------------------------------------------------------------

def _safe_slug(text: str, max_len: int = 60) -> str:
    """Tạo tên file an toàn từ từ khóa tìm kiếm."""
    text = (text or "effect").lower().strip()
    text = re.sub(r"[^a-z0-9_-]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return (text or "effect")[:max_len]


def list_effect_videos() -> list[Path]:
    """Lấy danh sách file hiệu ứng trong data/effects."""
    config.EFFECTS_DIR.mkdir(parents=True, exist_ok=True)
    return sorted(config.EFFECTS_DIR.glob("*.mp4"))


def download_pexels_effect(query: str, api_key: str = "", max_results: int = 8) -> Path:
    """
    Tải 1 video hiệu ứng từ Pexels về data/effects.
    Cần Pexels API key. Có thể nhập trong UI hoặc đặt biến môi trường PEXELS_API_KEY.
    """
    api_key = (api_key or os.getenv("PEXELS_API_KEY", "")).strip()
    query = (query or "rain overlay").strip()
    if not api_key:
        raise ValueError("Chưa có Pexels API key. Hãy nhập API key Pexels trong giao diện hoặc đặt biến môi trường PEXELS_API_KEY.")

    headers = {"Authorization": api_key}
    params = {
        "query": query,
        "orientation": "landscape",
        "per_page": max(1, min(int(max_results), 20)),
    }
    response = requests.get("https://api.pexels.com/videos/search", headers=headers, params=params, timeout=30)
    response.raise_for_status()
    data = response.json()
    videos = data.get("videos") or []
    if not videos:
        raise ValueError(f"Không tìm thấy hiệu ứng online cho từ khóa: {query}")

    best_video_file = None
    best_video = None
    for video in videos:
        video_files = video.get("video_files") or []
        candidates = [
            f for f in video_files
            if f.get("file_type") == "video/mp4" and f.get("link")
        ]
        if not candidates:
            continue
        candidates.sort(key=lambda f: (
            abs((f.get("height") or 720) - 720),
            abs((f.get("width") or 1280) - 1280),
        ))
        best_video_file = candidates[0]
        best_video = video
        break

    if not best_video_file:
        raise ValueError("Pexels có kết quả nhưng không có file mp4 phù hợp.")

    slug = _safe_slug(query)
    video_id = best_video.get("id", random.randint(1000, 9999)) if best_video else random.randint(1000, 9999)
    out_path = config.EFFECTS_DIR / f"pexels_{slug}_{video_id}.mp4"

    download = requests.get(best_video_file["link"], stream=True, timeout=120)
    download.raise_for_status()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("wb") as f:
        for chunk in download.iter_content(chunk_size=1024 * 512):
            if chunk:
                f.write(chunk)

    if not out_path.exists() or out_path.stat().st_size < 1024:
        raise ValueError("File hiệu ứng tải về bị lỗi hoặc quá nhỏ.")

    credit_path = config.EFFECTS_DIR / "online_effects_credits.txt"
    photographer = (best_video or {}).get("user", {}).get("name", "Pexels")
    source_url = (best_video or {}).get("url", "https://www.pexels.com")
    with credit_path.open("a", encoding="utf-8") as f:
        f.write(f"{out_path.name} | Video by {photographer} on Pexels | {source_url}\n")

    logger.info(f"[Pexels] Đã tải hiệu ứng online: {out_path.name}")
    return out_path


def create_builtin_effect_pack() -> list[Path]:
    """
    Tạo bộ hiệu ứng code local (nền đen, dùng với blend screen).
    Kỹ thuật hạt rơi: sinh 1 khung noise tĩnh (select frame 0 + loop) rồi cuộn dọc
    bằng filter scroll -> hạt có quỹ đạo rơi thật thay vì nhiễu nhấp nháy.
    Tốc độ cuộn chọn sao cho sau 8s (192 frame) trôi tròn số lần chiều cao khung
    -> video lặp khít (seamless loop) khi render dùng -stream_loop.
    """
    config.EFFECTS_DIR.mkdir(parents=True, exist_ok=True)

    # Khung noise tĩnh: giữ frame 0, lặp đủ 192 frame, đặt lại timestamp 24fps
    _static_noise = (
        "select='eq(n,0)',loop=loop=191:size=1:start=0,setpts=N/(24*TB)"
    )
    # 3/192 vòng/frame: mưa rơi ~270px/s; 1/192: tuyết ~90px/s (đều tròn vòng sau 8s)
    effect_specs = {
        # Mưa: hạt thưa kéo dọc thành vệt (avgblur dọc) + tăng sáng lại, cuộn nhanh
        "effect_rain_fall.mp4": (
            "nullsrc=s=1280x720:d=8:r=24,"
            "geq=lum='if(gt(random(1),0.9975),255,0)':cb=128:cr=128,"
            f"{_static_noise},"
            "avgblur=sizeX=1:sizeY=7,lutyuv=y='min(val*10,190)',"
            "scroll=vertical=0.015625,"
            "format=yuv420p"
        ),
        # Tuyết: bông mềm (blur đều), rơi chậm
        "effect_snow_fall.mp4": (
            "nullsrc=s=1280x720:d=8:r=24,"
            "geq=lum='if(gt(random(1),0.996),255,0)':cb=128:cr=128,"
            f"{_static_noise},"
            "avgblur=sizeX=2:sizeY=2,lutyuv=y='min(val*7,210)',"
            "scroll=vertical=0.00520833,"
            "format=yuv420p"
        ),
        # Bụi: hạt rất thưa, mờ và tối, trôi lơ lửng lên trên
        "effect_dust_soft.mp4": (
            "nullsrc=s=1280x720:d=8:r=24,"
            "geq=lum='if(gt(random(1),0.998),255,0)':cb=128:cr=128,"
            f"{_static_noise},"
            "avgblur=sizeX=3:sizeY=3,lutyuv=y='min(val*5,140)',"
            "scroll=vertical=-0.00520833,"
            "format=yuv420p"
        ),
        # Scanline retro: tĩnh, vốn là hiệu ứng nhân tạo nên giữ nguyên
        "effect_retro_scanline.mp4": "nullsrc=s=1280x720:d=8:r=24,geq=lum='if(eq(mod(Y,6),0),70,0)':cb=128:cr=128,format=yuv420p",
        # Film grain: random mỗi frame là ĐÚNG bản chất grain, giữ nguyên
        "effect_light_film_grain.mp4": "nullsrc=s=1280x720:d=8:r=24,geq=lum='random(1)*45':cb=128:cr=128,format=yuv420p",
    }
    created = []
    for file_name, lavfi in effect_specs.items():
        out_path = config.EFFECTS_DIR / file_name
        if out_path.exists() and out_path.stat().st_size > 1024:
            created.append(out_path)
            continue
        cmd = [
            "ffmpeg", "-y", "-f", "lavfi", "-i", lavfi,
            "-t", "8", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(out_path),
        ]
        subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        created.append(out_path)
    return created


def pick_effect_video() -> Path:
    """Chọn ngẫu nhiên 1 video hiệu ứng (mưa/bụi/đĩa than) từ thư mục asset tĩnh."""
    effects = list_effect_videos()
    if not effects:
        default_path = config.EFFECTS_DIR / "default_effect.mp4"
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "color=c=black:s=1280x720:d=5",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            str(default_path)
        ]
        logger.info(f"Tạo file hiệu ứng mặc định: {' '.join(cmd)}")
        subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        effects = [default_path]
    return random.choice(effects)

# --- Pixabay Video API ---
PIXABAY_VIDEO_API_URL = "https://pixabay.com/api/videos/"
PIXABAY_LICENSE_NAME = "Pixabay Content License"
PIXABAY_LICENSE_URL = "https://pixabay.com/service/license-summary/"


def _pick_pixabay_file(files: dict) -> dict | None:
    items = [dict(files.get(q) or {}, quality=q) for q in ("medium", "small", "large", "tiny") if (files.get(q) or {}).get("url")]
    items.sort(key=lambda x: (abs(int(x.get("height") or 720) - 720), int(x.get("size") or 0)))
    return items[0] if items else None


def search_pixabay_effects(query: str, api_key: str = "", max_results: int = 12) -> list[dict]:
    """Tìm metadata video; không tải tự động."""
    key = (api_key or getattr(config, "PIXABAY_API_KEY", "") or os.getenv("PIXABAY_API_KEY", "")).strip()
    if not key:
        raise ValueError("Chưa có Pixabay API key.")
    query = (query or "rain overlay").strip()[:100]
    response = requests.get(PIXABAY_VIDEO_API_URL, params={
        "key": key, "q": query, "video_type": "film", "safesearch": "true",
        "per_page": max(3, min(int(max_results), 20)),
    }, headers={"User-Agent": "LoFi-Studio/1.0"}, timeout=int(getattr(config, "EFFECT_API_TIMEOUT", 20)))
    response.raise_for_status()
    results = []
    for hit in response.json().get("hits") or []:
        chosen = _pick_pixabay_file(hit.get("videos") or {})
        if not chosen:
            continue
        results.append({
            "provider": "pixabay", "id": int(hit.get("id") or 0),
            "page_url": str(hit.get("pageURL") or ""), "download_url": str(chosen["url"]),
            # Thumbnail chỉ dùng hiển thị kết quả; video vẫn chỉ tải khi người dùng chọn.
            "thumbnail_url": str(chosen.get("thumbnail") or ""),
            "tags": [x.strip() for x in str(hit.get("tags") or "").split(",") if x.strip()],
            "duration": float(hit.get("duration") or 0), "width": int(chosen.get("width") or 0),
            "height": int(chosen.get("height") or 0), "file_size": int(chosen.get("size") or 0),
            "quality": chosen.get("quality"), "query": query,
            "license_name": PIXABAY_LICENSE_NAME, "license_url": PIXABAY_LICENSE_URL,
        })
    return results


def _allowed_pixabay_url(url: str) -> bool:
    from urllib.parse import urlparse
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and any(host == domain or host.endswith("." + domain) for domain in ("pixabay.com", "pixabayusercontent.com"))


def download_pixabay_effect(candidate: dict) -> Path:
    """Tải đúng video được chọn, giới hạn dung lượng và ghi manifest."""
    import shutil
    from core.effects.manifest import register_effect, sha256_file
    if candidate.get("provider") != "pixabay" or not _allowed_pixabay_url(str(candidate.get("download_url") or "")):
        raise ValueError("Kết quả tải không hợp lệ hoặc không thuộc Pixabay.")
    asset_id = int(candidate.get("id") or 0)
    if not asset_id:
        raise ValueError("Kết quả Pixabay thiếu asset id.")
    limit_mb = int(getattr(config, "EFFECT_MAX_DOWNLOAD_MB", 30))
    limit = limit_mb * 1024 * 1024
    if int(candidate.get("file_size") or 0) > limit:
        raise ValueError(f"Video vượt giới hạn {limit_mb} MB.")
    config.EFFECTS_DIR.mkdir(parents=True, exist_ok=True)
    output = config.EFFECTS_DIR / f"pixabay_{asset_id}.mp4"
    if output.is_file() and output.stat().st_size > 1024:
        return output
    partial = output.with_suffix(".mp4.part")
    total = 0
    try:
        with requests.get(candidate["download_url"], stream=True, headers={"User-Agent": "LoFi-Studio/1.0"}, timeout=(15, 120)) as response:
            response.raise_for_status()
            with partial.open("wb") as stream:
                for chunk in response.iter_content(512 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > limit:
                        raise ValueError(f"Video vượt giới hạn {limit_mb} MB.")
                    stream.write(chunk)
        partial.replace(output)
        if not output.is_file() or output.stat().st_size < 1024:
            raise ValueError("File tải về bị lỗi hoặc quá nhỏ.")
        if shutil.which("ffprobe"):
            test = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_type", "-of", "default=nw=1:nk=1", str(output)], capture_output=True, text=True, timeout=20)
            if test.returncode or "video" not in test.stdout:
                raise ValueError("File tải về không phải video hợp lệ.")
    except Exception:
        partial.unlink(missing_ok=True)
        output.unlink(missing_ok=True)
        raise
    register_effect(config.EFFECTS_DIR, {
        "file_name": output.name, "provider": "pixabay", "provider_asset_id": asset_id,
        "source_page_url": candidate.get("page_url"), "license_name": PIXABAY_LICENSE_NAME,
        "license_url": PIXABAY_LICENSE_URL, "query": candidate.get("query"),
        "thumbnail_url": candidate.get("thumbnail_url") or None,
        "tags": candidate.get("tags") or [], "duration_seconds": candidate.get("duration"),
        "width": candidate.get("width"), "height": candidate.get("height"),
        "file_size": output.stat().st_size, "sha256": sha256_file(output), "status": "ready",
    })
    # Tự nhận diện loại nền (phông xanh/nền đen/alpha) ngay sau khi tải.
    try:
        from core.effects.analyzer import analyze_and_register
        analyze_and_register(output)
    except Exception as exc:
        logger.warning(f"[EffectAnalyzer] Không phân tích được {output.name}: {exc}")
    return output


def get_effect_metadata(effect_path: str | Path) -> dict:
    from core.effects.manifest import get_effect_metadata as get_metadata
    return get_metadata(config.EFFECTS_DIR, effect_path)


def sync_effect_manifest(calculate_hashes: bool = False) -> dict:
    """Đồng bộ manifest với thư viện local và tự nhận diện file cũ."""
    from core.effects.manifest import reconcile_manifest
    return reconcile_manifest(config.EFFECTS_DIR, calculate_hashes=calculate_hashes)


def list_effect_records(include_missing: bool = False) -> list[dict]:
    """Lấy metadata thư viện để UI hiển thị nguồn, license và trạng thái."""
    from core.effects.manifest import list_effect_records as _list
    return _list(config.EFFECTS_DIR, include_missing=include_missing)


def remove_missing_manifest_entries() -> int:
    """Dọn metadata của file đã bị xóa khỏi ổ đĩa; không xóa video."""
    from core.effects.manifest import remove_missing_entries
    return remove_missing_entries(config.EFFECTS_DIR)


def build_ai_effect_profile(track: dict, music_tags: list[str], image_context: str) -> dict:
    """Tạo hồ sơ hiệu ứng bằng AI; lỗi API tự chuyển sang mapping local."""
    from core.effects.recommender import build_effect_profile
    return build_effect_profile(
        track, music_tags, image_context,
        api_url=getattr(config, "PROMPT_API_URL", ""),
        api_key=getattr(config, "PROMPT_API_KEY", ""),
        model=getattr(config, "PROMPT_API_MODEL", "openai"),
        timeout=int(getattr(config, "PROMPT_API_TIMEOUT", 40)),
        enabled=bool(getattr(config, "EFFECT_AI_ENABLED", True)),
    )


def search_and_rank_pixabay_effects(profile: dict, api_key: str = "") -> list[dict]:
    """Tìm tối đa 3 query, khử trùng và xếp hạng metadata; không tự tải."""
    from core.effects.recommender import rank_candidates
    max_queries = int(getattr(config, "EFFECT_AI_MAX_QUERIES", 3))
    max_results = int(getattr(config, "EFFECT_AI_MAX_RESULTS", 6))
    candidates = []
    seen_ids = set()
    for query in (profile.get("queries") or [])[:max_queries]:
        for item in search_pixabay_effects(str(query), api_key, max_results=10):
            asset_id = item.get("id")
            if asset_id in seen_ids:
                continue
            seen_ids.add(asset_id)
            candidates.append(item)
    return rank_candidates(candidates, profile)[:max_results]


def recommend_effects(profile: dict, api_key: str = "", min_local_results: int = 3, min_local_score: int = 55) -> dict:
    """
    Luồng đề xuất local-first:
    1. Xếp hạng thư viện local theo hồ sơ AI.
    2. Đủ min_local_results kết quả đạt điểm thì KHÔNG gọi Pixabay (tiết kiệm quota,
       chạy được offline).
    3. Thiếu mới tìm thêm Pixabay; kết quả local vẫn đứng trước.
    """
    from core.effects.recommender import rank_local_effects
    local_ranked = rank_local_effects(list_effect_records(), profile)
    good_local = [item for item in local_ranked if int(item.get("ai_score") or 0) >= min_local_score]
    online: list[dict] = []
    online_error = ""
    if len(good_local) < int(min_local_results):
        try:
            online = search_and_rank_pixabay_effects(profile, api_key)
        except Exception as exc:
            online_error = str(exc)[:300]
    for item in online:
        item["origin"] = "pixabay"
    return {
        "local": local_ranked,
        "online": online,
        "used_pixabay": bool(online),
        "online_error": online_error,
    }


def analyze_effect_type(effect_path: str | Path) -> dict:
    """Phân tích loại nền (phông xanh/nền đen/alpha) và ghi vào manifest."""
    from core.effects.analyzer import analyze_and_register
    return analyze_and_register(Path(effect_path))
