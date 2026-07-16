"""
Kiểm tra cấu hình máy (GPU/VRAM/RAM) và đề xuất thông số chạy SD local phù hợp.
Chạy độc lập: python system_check.py
Hoặc gọi run_check() từ main.py trước khi kích hoạt nhánh SD local.

Yêu cầu: pip install gputil psutil --break-system-packages
"""
import logging
import shutil
import subprocess

logger = logging.getLogger("lofi_automation")


def get_gpu_info() -> dict | None:
    """Lấy thông tin GPU NVIDIA qua nvidia-smi (không cần thư viện ngoài)."""
    if not shutil.which("nvidia-smi"):
        return None
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,memory.free",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10, check=True,
        )
        name, total_mb, free_mb = [x.strip() for x in result.stdout.strip().split(",")]
        return {
            "name": name,
            "vram_total_mb": int(total_mb),
            "vram_free_mb": int(free_mb),
        }
    except Exception as e:
        logger.warning(f"Không đọc được thông tin GPU: {e}")
        return None


def get_ram_info() -> dict:
    """Lấy RAM tổng/khả dụng bằng psutil."""
    import psutil
    mem = psutil.virtual_memory()
    return {
        "ram_total_gb": round(mem.total / (1024 ** 3), 1),
        "ram_available_gb": round(mem.available / (1024 ** 3), 1),
    }


def get_cpu_info() -> dict:
    import psutil
    return {
        "cpu_cores": psutil.cpu_count(logical=False),
        "cpu_threads": psutil.cpu_count(logical=True),
    }


def recommend_sd_config(gpu: dict | None, ram: dict) -> dict:
    """
    Đưa ra khuyến nghị dựa trên VRAM/RAM thực tế của máy.
    Ngưỡng tham khảo:
      < 4GB VRAM  -> không nên chạy SD local, chỉ dùng Pollinations
      4-6GB VRAM  -> SD 1.5 + --lowvram, resolution 512x512
      6-8GB VRAM  -> SD 1.5 + --medvram, có thể 512x768
      8GB+ VRAM   -> SD 1.5 thoải mái, cân nhắc thử SDXL + --medvram
      12GB+ VRAM  -> SDXL chạy ổn, không cần flag tiết kiệm VRAM
    """
    if gpu is None:
        return {
            "can_run_sd_local": False,
            "reason": "Không phát hiện GPU NVIDIA. Chỉ nên dùng Pollinations.ai, "
                      "không kích hoạt nhánh fallback SD local.",
            "checkpoint": None,
            "flags": [],
        }

    vram = gpu["vram_total_mb"] / 1024  # GB

    if vram < 4:
        return {
            "can_run_sd_local": False,
            "reason": f"VRAM {vram:.1f}GB quá thấp, khả năng out of memory rất cao. "
                      f"Khuyến nghị chỉ dùng Pollinations.ai làm nguồn ảnh duy nhất.",
            "checkpoint": None,
            "flags": [],
        }
    elif vram < 6:
        checkpoint, flags, resolution = "SD 1.5 (anime/illustration fine-tune)", ["--lowvram"], "512x512"
    elif vram < 8:
        checkpoint, flags, resolution = "SD 1.5", ["--medvram"], "512x512 hoặc 512x768"
    elif vram < 12:
        checkpoint, flags, resolution = "SD 1.5 (thoải mái) hoặc thử SDXL", ["--medvram"], "768x768"
    else:
        checkpoint, flags, resolution = "SDXL", [], "1024x1024"

    ram_warning = None
    if ram["ram_total_gb"] < 16:
        ram_warning = "RAM dưới 16GB - nên đóng bớt ứng dụng khác khi chạy SD cùng lúc với FFmpeg/Streamlit."

    return {
        "can_run_sd_local": True,
        "gpu_name": gpu["name"],
        "vram_gb": round(vram, 1),
        "checkpoint": checkpoint,
        "resolution": resolution,
        "flags": flags,
        "ram_warning": ram_warning,
    }


def run_check(verbose: bool = True) -> dict:
    """Hàm chính - gọi khi cần kiểm tra trước khi bật SD local."""
    gpu = get_gpu_info()
    ram = get_ram_info()
    cpu = get_cpu_info()
    recommendation = recommend_sd_config(gpu, ram)

    if verbose:
        print("=== KIỂM TRA CẤU HÌNH MÁY ===")
        print(f"CPU: {cpu['cpu_cores']} nhân / {cpu['cpu_threads']} luồng")
        print(f"RAM: {ram['ram_total_gb']}GB tổng, {ram['ram_available_gb']}GB khả dụng")
        if gpu:
            print(f"GPU: {gpu['name']} - VRAM {gpu['vram_total_mb']}MB "
                  f"(còn trống {gpu['vram_free_mb']}MB)")
        else:
            print("GPU: không phát hiện GPU NVIDIA")
        print("\n=== ĐỀ XUẤT CẤU HÌNH SD LOCAL ===")
        if recommendation["can_run_sd_local"]:
            print(f"Checkpoint đề xuất : {recommendation['checkpoint']}")
            print(f"Độ phân giải        : {recommendation['resolution']}")
            print(f"Flag khởi chạy      : {' '.join(recommendation['flags']) or '(không cần)'}")
            if recommendation.get("ram_warning"):
                print(f"Lưu ý RAM           : {recommendation['ram_warning']}")
        else:
            print(f"Không nên chạy SD local: {recommendation['reason']}")

    return {"gpu": gpu, "ram": ram, "cpu": cpu, "recommendation": recommendation}


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    run_check()
