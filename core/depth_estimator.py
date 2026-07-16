"""
AI FILE NOTE - DEPTH ESTIMATION MODEL (CPU)
Chức năng chính:
- Quản lý tải và nạp mô hình Depth Anything V2 Small dạng ONNX quantized (~25MB) từ HuggingFace.
- Chạy InferenceSession trên CPU để dự đoán bản đồ độ sâu (depth map) của ảnh 2D đầu vào.
- Trả về độ sâu tương đối chuẩn hóa của từng điểm ảnh để chuẩn bị tách lớp Parallax.
Đầu vào chính:
- Ảnh PIL Image gốc.
Đầu ra chính:
- Mảng NumPy ndarray 2 chiều chứa độ sâu tương đối float32 [0..1] (giá trị càng lớn càng gần camera).
API được file khác sử dụng:
- Lớp `DepthEstimator`, `DepthEstimationError`.
Phụ thuộc quan trọng:
- onnxruntime, numpy, Pillow (PIL), urllib
Lưu ý khi sửa:
- Giữ kích thước đầu vào _INPUT_SIZE = 518 vì đây là kiến trúc chuẩn của mô hình ViT. Chạy dạng singleton.
"""
import logging
import os
import urllib.request
from pathlib import Path

import numpy as np

import config

logger = logging.getLogger("lofi_automation")

# Depth Anything V2 Small (bản ONNX int8 quantized ~25MB, chạy tốt trên CPU)
MODEL_URL = "https://huggingface.co/onnx-community/depth-anything-v2-small/resolve/main/onnx/model_quantized.onnx"
MODEL_PATH = config.BASE_DIR / "data" / "models" / "depth_anything_v2_small_quantized.onnx"
# Đưa vào hash cache của ParallaxProcessor: đổi version -> cache lớp cũ tự vô hiệu
MODEL_VERSION = "depth_anything_v2_small_q8_v1"

# Kích thước đầu vào chuẩn của ViT-S (bội số của patch 14)
_INPUT_SIZE = 518
_IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


class DepthEstimationError(Exception):
    """Lỗi trong quá trình ước lượng độ sâu ảnh."""
    pass


class DepthEstimator:
    """
    Ước lượng bản đồ độ sâu (depth map) từ ảnh tĩnh bằng Depth Anything V2 Small
    qua onnxruntime CPU. Dùng cho việc tách lớp Parallax theo nội dung thật.
    """
    _session = None

    @classmethod
    def ensure_model(cls) -> Path:
        """Tải model về data/models nếu chưa có (tải 1 lần, dùng mãi)."""
        if MODEL_PATH.exists() and MODEL_PATH.stat().st_size > 10 * 1024 * 1024:
            return MODEL_PATH

        MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = MODEL_PATH.with_suffix(".onnx.tmp")
        logger.info(f"[DepthEstimator] Đang tải model Depth Anything V2 Small (~25MB) từ HuggingFace...")
        try:
            urllib.request.urlretrieve(MODEL_URL, tmp_path)
            if tmp_path.stat().st_size < 10 * 1024 * 1024:
                raise DepthEstimationError("File model tải về quá nhỏ, có thể bị lỗi.")
            os.replace(tmp_path, MODEL_PATH)
            logger.info(f"[DepthEstimator] Đã tải model về: {MODEL_PATH}")
        except Exception as e:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise DepthEstimationError(f"Không tải được model depth: {e}") from e
        return MODEL_PATH

    @classmethod
    def get_session(cls):
        """Khởi tạo InferenceSession 1 lần cho cả phiên chạy."""
        if cls._session is None:
            import onnxruntime as ort
            cls.ensure_model()
            cls._session = ort.InferenceSession(
                str(MODEL_PATH), providers=["CPUExecutionProvider"]
            )
        return cls._session

    @classmethod
    def estimate_depth(cls, image) -> np.ndarray:
        """
        Nhận PIL Image, trả về depth map float32 HxW chuẩn hóa 0..1
        (giá trị càng lớn = càng GẦN camera).
        """
        from PIL import Image

        session = cls.get_session()
        width, height = image.size

        img = image.convert("RGB").resize((_INPUT_SIZE, _INPUT_SIZE), Image.Resampling.LANCZOS)
        x = np.asarray(img, dtype=np.float32) / 255.0
        x = (x - _IMAGENET_MEAN) / _IMAGENET_STD
        x = x.transpose(2, 0, 1)[np.newaxis, ...]  # NCHW

        input_name = session.get_inputs()[0].name
        output = session.run(None, {input_name: x})[0]
        depth = np.squeeze(output).astype(np.float32)  # 518x518, giá trị lớn = gần

        d_min, d_max = float(depth.min()), float(depth.max())
        if d_max - d_min < 1e-6:
            raise DepthEstimationError("Depth map phẳng bất thường (ảnh không có chiều sâu).")
        depth = (depth - d_min) / (d_max - d_min)

        # Resize về kích thước ảnh gốc (8-bit là đủ chính xác cho việc tạo mask)
        depth_img = Image.fromarray((depth * 255.0).astype(np.uint8))
        depth_img = depth_img.resize((width, height), Image.Resampling.BILINEAR)
        return np.asarray(depth_img, dtype=np.float32) / 255.0
