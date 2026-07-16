"""
AI FILE NOTE - ADVANCED AUDIO PROCESSOR
Chức năng chính:
- Tiền xử lý nhạc nền thô sang chuẩn màu âm Lofi: làm chậm tempo (0.88x), lọc dải thấp (lowpass 1800Hz), căn chỉnh mức âm lượng (Target LUFS).
- Tạo chuỗi nối âm (crossfade) tạo vòng lặp nhạc vô hạn (seamless loop).
- Tạo và hòa trộn (mix) âm thanh môi trường (tiếng mưa rơi, tiếng vinyl crackle) từ code.
Đầu vào chính:
- File nhạc đầu vào, thời lượng mong muốn, cấu hình vibe (clean, ambient, crackly).
Đầu ra chính:
- Path file âm thanh đầu ra có định dạng .m4a hoặc .mp3.
API được file khác sử dụng:
- Lớp `AudioProcessor`, `AudioProcessorError`.
Phụ thuộc quan trọng:
- FFmpeg, config, core.media.probe
Lưu ý khi sửa:
- Đảm bảo tham số lọc tần số của lowpass và compressor được tinh chỉnh để tránh vỡ âm hoặc méo tiếng quá mức.
"""
import os
import sys
import shutil
import subprocess
import logging
import math
from pathlib import Path
import config
from core.media.probe import MediaProbe

logger = logging.getLogger("lofi_automation")

class AudioProcessorError(Exception):
    """Lỗi phát sinh trong quá trình xử lý âm thanh nâng cao."""
    pass

class AudioProcessor:
    """
    Module xử lý âm thanh nâng cao (Mục 4.2 & 4.3):
    Chuẩn hóa LUFS, phối trộn âm thanh nền (ambience) và xử lý lặp crossfade.
    """

    @classmethod
    def apply_lofi_character(cls, input_path: Path, output_path: Path, tempo: float = None) -> Path:
        """
        Áp chất âm lofi đặc trưng lên bài nhạc:
        - atempo: chậm lại (slowed) theo config.AUDIO_TEMPO_RATE - chữ ký của nhạc lofi
        - lowpass 11kHz + treble giảm nhẹ: âm ấm, bớt chói như băng cối
        - highpass 40Hz: cắt rumble dưới đáy
        - bass boost nhẹ 120Hz: đầy đặn
        - compressor nhẹ: âm lượng đều, dễ nghe lâu
        """
        input_path = Path(input_path)
        output_path = Path(output_path)
        if not input_path.is_file():
            raise AudioProcessorError(f"Không tìm thấy file âm thanh đầu vào: {input_path}")
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if tempo is None:
            tempo = getattr(config, "AUDIO_TEMPO_RATE", 0.88)
        tempo = min(2.0, max(0.5, float(tempo)))

        filter_chain = (
            f"atempo={tempo},"
            "highpass=f=40,"
            "lowpass=f=11000,"
            "bass=g=2:f=120:w=0.6,"
            "treble=g=-1.5:f=7500,"
            "acompressor=threshold=-18dB:ratio=2:attack=20:release=250:makeup=2dB"
        )
        cmd = [
            "ffmpeg", "-y",
            "-i", str(input_path),
            "-af", filter_chain,
            "-c:a", "aac", "-b:a", "192k",
            str(output_path)
        ]
        logger.info(f"[AudioProcessor] Đang áp chất âm lofi (tempo {tempo}x, lowpass 11kHz)...")
        result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
        if result.returncode != 0:
            raise AudioProcessorError(f"Áp chất âm lofi thất bại: {result.stderr}")
        return output_path

    @classmethod
    def create_builtin_ambience_pack(cls) -> dict:
        """
        Sinh bộ âm thanh nền bằng code (không cần tải):
        - rain_ambience.mp3: brown noise lọc dải thấp + tremolo chậm ~ tiếng mưa đều
        - vinyl_crackle.mp3: white noise qua gate ngưỡng cao ~ tiếng lộp bộp đĩa than
        Mỗi file 60 giây, khi trộn được stream_loop nên dài bao nhiêu cũng đủ.
        """
        config.EFFECTS_DIR.mkdir(parents=True, exist_ok=True)
        specs = {
            "rain_ambience.mp3": (
                "anoisesrc=colour=brown:sample_rate=48000:duration=60,"
                "highpass=f=150,lowpass=f=1500,"
                "tremolo=f=0.3:d=0.15,volume=2.0"
            ),
            "vinyl_crackle.mp3": (
                "anoisesrc=colour=white:sample_rate=48000:duration=60,"
                "agate=threshold=0.6:ratio=9000:attack=0.01:release=2,"
                "lowpass=f=7000,volume=1.5"
            ),
        }
        created = {}
        for file_name, lavfi in specs.items():
            out_path = config.EFFECTS_DIR / file_name
            key = file_name.split(".")[0]
            if out_path.exists() and out_path.stat().st_size > 10 * 1024:
                created[key] = out_path
                continue
            cmd = [
                "ffmpeg", "-y", "-f", "lavfi", "-i", lavfi,
                "-c:a", "libmp3lame", "-q:a", "4", str(out_path)
            ]
            result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
            if result.returncode != 0:
                logger.warning(f"[AudioProcessor] Không sinh được {file_name}: {result.stderr[-300:]}")
                continue
            created[key] = out_path
            logger.info(f"[AudioProcessor] Đã sinh âm thanh nền: {file_name}")
        return created

    @classmethod
    def normalize_audio(cls, input_path: Path, output_path: Path, target_lufs: float = -15.0) -> Path:
        """
        Chuẩn hóa âm lượng LUFS của file nhạc chính (Mục 503).
        Đích mặc định: -15 LUFS, True Peak: -1.0 dBTP.
        """
        input_path = Path(input_path)
        output_path = Path(output_path)
        
        if not input_path.is_file():
            raise AudioProcessorError(f"Không tìm thấy file âm thanh đầu vào: {input_path}")
            
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        # FFmpeg loudnorm filter
        filter_str = f"loudnorm=I={target_lufs}:TP=-1.0:LRA=11:print_format=json"
        
        cmd = [
            "ffmpeg", "-y",
            "-i", str(input_path),
            "-af", filter_str,
            "-c:a", "aac",
            "-b:a", "192k",
            str(output_path)
        ]
        
        logger.info(f"[AudioProcessor] Đang chuẩn hóa âm lượng file {input_path.name} sang {target_lufs} LUFS...")
        result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
        
        if result.returncode != 0:
            raise AudioProcessorError(f"Chuẩn hóa âm lượng thất bại: {result.stderr}")
            
        return output_path

    @classmethod
    def mix_ambience(cls, music_path: Path, ambience_path: Path, output_path: Path,
                     music_volume: float = 1.0, ambience_volume: float = 0.08,
                     duration: float = None) -> Path:
        """
        Phối hợp tiếng môi trường (ambience) vào bài nhạc chính (Mục 510, 514).
        Sử dụng stream_loop để lặp vô hạn tiếng ambience nếu bài nhạc chính dài hơn.
        """
        music_path = Path(music_path)
        ambience_path = Path(ambience_path)
        output_path = Path(output_path)
        
        if not music_path.is_file():
            raise AudioProcessorError(f"Không tìm thấy bài nhạc chính: {music_path}")
            
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Thiết lập filter_complex trộn âm
        # [0:a] là nhạc chính, [1:a] là ambience
        filter_complex = f"[0:a]volume={music_volume}[m];[1:a]volume={ambience_volume}[a];[m][a]amix=inputs=2:duration=first:dropout_transition=2[out]"
        
        cmd = [
            "ffmpeg", "-y",
            "-i", str(music_path)
        ]
        
        # Nếu có file ambience thì mix, nếu không thì chỉ copy nhạc chính
        if ambience_path.is_file():
            cmd.extend(["-stream_loop", "-1", "-i", str(ambience_path)])
            cmd.extend(["-filter_complex", filter_complex, "-map", "[out]"])
        else:
            logger.warning(f"[AudioProcessor] Không tìm thấy file ambience {ambience_path.name}. Giữ nguyên nhạc gốc.")
            cmd.extend(["-c:a", "copy"])
            
        if duration:
            cmd.extend(["-t", f"{duration:.3f}"])
            
        cmd.extend(["-c:a", "aac", "-b:a", "192k", str(output_path)])
        
        logger.info(f"[AudioProcessor] Đang phối âm nền {ambience_path.name} với tỷ lệ {ambience_volume:.2f}...")
        result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
        
        if result.returncode != 0:
            raise AudioProcessorError(f"Phối âm nền thất bại: {result.stderr}")
            
        return output_path

    @classmethod
    def loop_audio(cls, input_path: Path, output_path: Path, target_duration: float,
                   crossfade_seconds: float = 5.0) -> Path:
        """
        Kéo dài bài nhạc bằng cách lặp và sử dụng bộ lọc crossfade mượt mà (Mục 532).
        """
        input_path = Path(input_path)
        output_path = Path(output_path)
        
        if not input_path.is_file():
            raise AudioProcessorError(f"Không tìm thấy file đầu vào: {input_path}")
            
        # Đọc thời lượng file nhạc gốc
        probe_res = MediaProbe.probe_media(input_path)
        orig_duration = probe_res["duration_seconds"]
        if orig_duration <= 0:
            raise AudioProcessorError("Không đọc được thời lượng của bài nhạc.")
            
        if orig_duration >= target_duration:
            logger.info("[AudioProcessor] Nhạc gốc đã đủ thời lượng yêu cầu, bỏ qua bước lặp.")
            shutil.copy(str(input_path), str(output_path))
            return output_path
            
        # Tính số lần lặp cần thiết
        # Mỗi lần lặp sẽ mất đi crossfade_seconds
        net_duration = orig_duration - crossfade_seconds
        if net_duration <= 0:
            raise AudioProcessorError("Thời lượng bài nhạc quá ngắn so với thời gian crossfade.")
            
        num_repeats = math.ceil(target_duration / net_duration) + 1
        
        # Xây dựng command và filter complex cho acrossfade liên tục
        cmd = ["ffmpeg", "-y"]
        for _ in range(num_repeats):
            cmd.extend(["-i", str(input_path)])
            
        # Chèn filter crossfade nối tiếp:
        # [0:a][1:a]acrossfade=d=5[a1]; [a1][2:a]acrossfade=d=5[a2]...
        filter_parts = []
        last_out = "[0:a]"
        for i in range(1, num_repeats):
            next_out = f"[a{i}]"
            filter_parts.append(f"{last_out}[{i}:a]acrossfade=d={crossfade_seconds}:c1=tri:c2=tri{next_out}")
            last_out = next_out
            
        filter_complex = ";".join(filter_parts)
        
        cmd.extend([
            "-filter_complex", filter_complex,
            "-map", last_out,
            "-t", f"{target_duration:.3f}",
            "-c:a", "aac",
            "-b:a", "192k",
            str(output_path)
        ])
        
        logger.info(f"[AudioProcessor] Đang lặp nhạc ({num_repeats} lần) đạt thời lượng {target_duration}s với crossfade {crossfade_seconds}s...")
        result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
        
        if result.returncode != 0:
            raise AudioProcessorError(f"Lặp nhạc với crossfade thất bại: {result.stderr}")
            
        return output_path

    @classmethod
    def generate_previews(cls, music_path: Path, preview_dir: Path, duration: float = 30.0) -> dict:
        """
        Sinh 3 bản nghe thử 30 giây phục vụ người dùng duyệt trước khi render chính (Mục 547).
        1. Clean: Gốc sạch (chỉ chuẩn hóa LUFS)
        2. Light Vibe: Phối thêm tiếng mưa rơi nhẹ (-22dB)
        3. Rich Vibe: Phối thêm tiếng mưa rõ (-20dB) + tiếng đĩa than crackle (-24dB) + reverb nhẹ.
        """
        music_path = Path(music_path)
        preview_dir = Path(preview_dir)
        preview_dir.mkdir(parents=True, exist_ok=True)
        
        clean_out = preview_dir / "preview_clean.m4a"
        light_out = preview_dir / "preview_light.m4a"
        rich_out = preview_dir / "preview_rich.m4a"

        # Đảm bảo có sẵn âm thanh nền (tự sinh bằng code nếu thiếu)
        ambience = cls.create_builtin_ambience_pack()
        rain_path = ambience.get("rain_ambience", config.EFFECTS_DIR / "rain_ambience.mp3")
        crackle_path = ambience.get("vinyl_crackle", config.EFFECTS_DIR / "vinyl_crackle.mp3")

        # Bước 1: Chuẩn hóa LUFS + áp chất âm lofi (slowed, lowpass ấm)
        normalized_tmp = preview_dir / "normalized_tmp.m4a"
        lofi_tmp = preview_dir / "lofi_tmp.m4a"
        cls.normalize_audio(music_path, normalized_tmp, target_lufs=-15.0)
        cls.apply_lofi_character(normalized_tmp, lofi_tmp)

        # Cắt lấy 30s đầu làm bản Clean
        subprocess.run([
            "ffmpeg", "-y", "-i", str(lofi_tmp),
            "-t", str(duration), "-c:a", "copy", str(clean_out)
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Bước 2: Light Vibe = nhạc + tiếng mưa nhẹ
        cls.mix_ambience(clean_out, rain_path, light_out, music_volume=1.0, ambience_volume=0.06, duration=duration)

        # Bước 3: Rich Vibe = nhạc + mưa rõ hơn + vinyl crackle + reverb nhẹ
        reverb_filter = "aecho=0.8:0.88:60:0.4"

        rich_tmp = preview_dir / "rich_tmp.m4a"
        rich_tmp2 = preview_dir / "rich_tmp2.m4a"
        cls.mix_ambience(clean_out, rain_path, rich_tmp, music_volume=1.0, ambience_volume=0.09, duration=duration)
        cls.mix_ambience(rich_tmp, crackle_path, rich_tmp2, music_volume=1.0, ambience_volume=0.05, duration=duration)

        subprocess.run([
            "ffmpeg", "-y", "-i", str(rich_tmp2),
            "-af", reverb_filter,
            "-c:a", "aac", "-b:a", "192k", str(rich_out)
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Dọn dẹp tệp tạm
        for tmp in (normalized_tmp, lofi_tmp, rich_tmp, rich_tmp2):
            if tmp.exists():
                tmp.unlink()
            
        return {
            "clean": clean_out,
            "light": light_out,
            "rich": rich_out
        }
