"""
AI FILE NOTE - VEGETATION SEMANTIC MASKER (CPU)
Chức năng chính:
- Tải mô hình phân đoạn ngữ nghĩa SegFormer-B0 ADE20K dạng ONNX (~4.4MB) từ HuggingFace.
- Chạy InferenceSession trên CPU để trích xuất mặt nạ (mask) nhị phân của các thực thể thực vật/cây cỏ.
- Hỗ trợ làm sạch, lọc tạp nhiễu các vùng cây quá nhỏ qua thuật toán Connected Components.
Đầu vào chính:
- Ảnh PIL Image gốc.
Đầu ra chính:
- Ảnh mặt nạ grayscale (PIL Image hệ L) định vị chính xác vùng phân đoạn cây lá.
API được file khác sử dụng:
- Lớp `VegetationMasker`, `VegetationMaskError`.
Phụ thuộc quan trọng:
- onnxruntime, numpy, Pillow (PIL)
Lưu ý khi sửa:
- Độ phân giải đầu vào chuẩn là 512x512. Tránh lọc mất các vùng lá tiền cảnh lớn.
"""
import logging
import os
import urllib.request
from pathlib import Path

import numpy as np

import config

logger = logging.getLogger("lofi_automation")

# SegFormer-B0 fine-tune ADE20K (150 class), bản ONNX int8 quantized ~15MB, chạy nhanh trên CPU
MODEL_URL = "https://huggingface.co/Xenova/segformer-b0-finetuned-ade-512-512/resolve/main/onnx/model_quantized.onnx"
MODEL_PATH = config.BASE_DIR / "data" / "models" / "segformer_b0_ade20k_quantized.onnx"
MODEL_VERSION = "segformer_b0_ade20k_q8_v1"

# Các class ADE20K thuộc nhóm thực vật (lay được theo gió)
# 4=tree, 9=grass, 17=plant, 66=flower, 72=palm tree
VEGETATION_CLASS_IDS = {4, 9, 17, 66, 72}

_INPUT_SIZE = 512
_IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


class VegetationMaskError(Exception):
    """Lỗi trong quá trình segmentation vùng thực vật."""
    pass


class VegetationMasker:
    """
    Tìm vùng cây/lá/cỏ/hoa trong ảnh bằng semantic segmentation (SegFormer ADE20K).
    Dùng để tạo bản đồ warp cho hiệu ứng lá cây lay riêng từng vùng.
    """
    _session = None

    @classmethod
    def ensure_model(cls) -> Path:
        """Tải model về data/models nếu chưa có (~15MB, tải 1 lần)."""
        if MODEL_PATH.exists() and MODEL_PATH.stat().st_size > 2 * 1024 * 1024:
            return MODEL_PATH
        MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = MODEL_PATH.with_suffix(".onnx.tmp")
        logger.info("[VegetationMasker] Đang tải model SegFormer-B0 ADE20K (~4.4MB) từ HuggingFace...")
        try:
            urllib.request.urlretrieve(MODEL_URL, tmp_path)
            if tmp_path.stat().st_size < 2 * 1024 * 1024:
                raise VegetationMaskError("File model tải về quá nhỏ, có thể bị lỗi.")
            os.replace(tmp_path, MODEL_PATH)
            logger.info(f"[VegetationMasker] Đã tải model về: {MODEL_PATH}")
        except Exception as e:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise VegetationMaskError(f"Không tải được model segmentation: {e}") from e
        return MODEL_PATH

    @classmethod
    def get_session(cls):
        if cls._session is None:
            import onnxruntime as ort
            cls.ensure_model()
            cls._session = ort.InferenceSession(str(MODEL_PATH), providers=["CPUExecutionProvider"])
        return cls._session

    @classmethod
    def get_vegetation_mask(cls, image) -> np.ndarray:
        """
        Nhận PIL Image, trả về mask float32 HxW (0..1) của vùng thực vật.
        Trả về mask toàn 0 nếu ảnh không có cây cỏ.
        """
        from PIL import Image

        session = cls.get_session()
        width, height = image.size

        img = image.convert("RGB").resize((_INPUT_SIZE, _INPUT_SIZE), Image.Resampling.BILINEAR)
        x = np.asarray(img, dtype=np.float32) / 255.0
        x = (x - _IMAGENET_MEAN) / _IMAGENET_STD
        x = x.transpose(2, 0, 1)[np.newaxis, ...]

        input_name = session.get_inputs()[0].name
        logits = session.run(None, {input_name: x})[0]  # [1, 150, 128, 128]
        class_map = np.argmax(np.squeeze(logits), axis=0).astype(np.uint8)  # 128x128

        veg = np.isin(class_map, list(VEGETATION_CLASS_IDS)).astype(np.uint8) * 255

        # Resize mask về kích thước gốc, làm mềm nhẹ để biên warp không gắt
        from PIL import ImageFilter
        mask_img = Image.fromarray(veg, mode="L").resize((width, height), Image.Resampling.BILINEAR)
        mask_img = mask_img.filter(ImageFilter.MedianFilter(size=5))
        mask_img = mask_img.filter(ImageFilter.GaussianBlur(radius=max(4, width // 300)))
        return np.asarray(mask_img, dtype=np.float32) / 255.0
