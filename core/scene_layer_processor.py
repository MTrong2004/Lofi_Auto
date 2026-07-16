"""
AI FILE NOTE - SCENE LAYERS PARSER
Chức năng chính:
- Phân tách ảnh gốc thành các lớp mặt nạ ngữ nghĩa (bầu trời, mây trôi, kiến trúc tĩnh, cây cỏ).
- Sử dụng mô hình học máy cục bộ (SegFormer ADE20K) để phân tách vùng tiền cảnh (foreground) và trung cảnh (midground) của thực vật.
- Tạo ra ảnh overlay trong suốt (PNG RGBA) và mặt nạ lông mịn (feathered masks) để tránh răng cưa khi dựng hình.
Đầu vào chính:
- Đường dẫn ảnh gốc cần xử lý, model_id phân đoạn.
Đầu ra chính:
- Cây thư mục `scene_layers/` chứa các tệp PNG đã tách lớp và file `scene_manifest.json` đặc tả phân cảnh.
API được file khác sử dụng:
- Lớp `SceneLayerProcessor`, `SceneLayerPaths`, `SceneLayerError`.
Phụ thuộc quan trọng:
- transformers, torch, Pillow (PIL), core.db
Lưu ý khi sửa:
- Giữ logic feather mặt nạ (`feather_radius`) mịn màng và đảm bảo độ co giãn kích thước chính xác.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter

# Allow execution both as core.scene_layer_processor and as a standalone file.
sys.path.append(str(Path(__file__).resolve().parent.parent))
import config

logger = logging.getLogger("lofi_automation")

SCHEMA_NAME = "scene_layers_manifest"
SCHEMA_VERSION = 1
PROCESSOR_VERSION = "scene_layer_processor_v1.0"
DEFAULT_MODEL_ID = "nvidia/segformer-b2-finetuned-ade-512-512"


class SceneLayerError(RuntimeError):
    """Base error for scene layer processing."""


class SceneLayerDependencyError(SceneLayerError):
    """Required AI/runtime dependency is unavailable."""


class SceneLayerInferenceError(SceneLayerError):
    """The segmentation model failed or returned unusable masks."""


@dataclass(frozen=True)
class SceneLayerPaths:
    root: Path
    masks_dir: Path
    layers_dir: Path
    previews_dir: Path
    source: Path
    manifest: Path
    leaves_all_mask: Path
    leaves_near_mask: Path
    leaves_mid_mask: Path
    architecture_mask: Path
    sky_mask: Path
    clouds_mask: Path
    leaves_near_layer: Path
    leaves_mid_layer: Path
    architecture_layer: Path
    sky_layer: Path
    mask_preview: Path

    @classmethod
    def create(cls, root: Path) -> "SceneLayerPaths":
        root = Path(root)
        masks = root / "masks"
        layers = root / "layers"
        previews = root / "previews"
        for directory in (root, masks, layers, previews):
            directory.mkdir(parents=True, exist_ok=True)
        return cls(
            root=root,
            masks_dir=masks,
            layers_dir=layers,
            previews_dir=previews,
            source=root / "source.png",
            manifest=root / "scene_manifest.json",
            leaves_all_mask=masks / "leaves_all.png",
            leaves_near_mask=masks / "leaves_near.png",
            leaves_mid_mask=masks / "leaves_mid.png",
            architecture_mask=masks / "architecture.png",
            sky_mask=masks / "sky.png",
            clouds_mask=masks / "clouds.png",
            leaves_near_layer=layers / "leaves_near.png",
            leaves_mid_layer=layers / "leaves_mid.png",
            architecture_layer=layers / "architecture_static.png",
            sky_layer=layers / "sky.png",
            mask_preview=previews / "mask_preview.png",
        )


class SceneLayerProcessor:
    """Build and cache semantic masks for object-aware scene animation."""

    # ADE20K label aliases. Matching is case-insensitive and tolerant of labels
    # such as "tree; plant; flora".
    VEGETATION_LABELS = {
        "tree", "plant", "flower", "grass", "palm", "field", "earth",
    }
    ARCHITECTURE_LABELS = {
        "building", "house", "wall", "door", "windowpane", "ceiling",
        "floor", "wood", "roof", "shelf", "table", "chair", "cabinet",
    }
    SKY_LABELS = {"sky"}
    CLOUD_LABELS = {"cloud", "clouds"}

    @classmethod
    def inspect_environment(cls) -> dict[str, Any]:
        """Return dependency and device information without loading a model."""
        result: dict[str, Any] = {
            "processor_version": PROCESSOR_VERSION,
            "pillow": True,
            "numpy": True,
            "torch": False,
            "transformers": False,
            "device": "cpu",
        }
        try:
            import torch

            result["torch"] = True
            if torch.cuda.is_available():
                result["device"] = "cuda"
            elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
                result["device"] = "mps"
        except Exception:
            pass
        try:
            import transformers  # noqa: F401

            result["transformers"] = True
        except Exception:
            pass
        return result

    @classmethod
    def get_scene_dir(cls, project_id: str) -> Path:
        """Return the canonical scene layer directory for a project."""
        try:
            from core.project_manager import ProjectManager

            project_dir = ProjectManager.get_project_dir(project_id)
        except Exception:
            project_dir = Path(config.BASE_DIR) / "data" / "projects" / project_id
        return Path(project_dir) / "scene_layers"

    @classmethod
    def analyze_scene(
        cls,
        image_path: Path,
        project_id: str,
        *,
        force_recreate: bool = False,
        model_id: str | None = None,
        min_component_ratio: float = 0.00035,
        feather_radius: int = 2,
    ) -> dict[str, Any]:
        """Analyze an image, save semantic layers and return its manifest.

        The current backend uses SegFormer ADE20K. It performs semantic scene
        parsing locally after the model has been downloaded once. It never
        falls back to geometric masks. Missing dependencies produce a clear
        error so the UI can offer procedural animation instead.
        """
        image_path = Path(image_path)
        if not image_path.is_file():
            raise FileNotFoundError(f"Không tìm thấy ảnh nguồn: {image_path}")
        if feather_radius < 0:
            raise ValueError("feather_radius không được âm.")
        if not 0 <= min_component_ratio < 1:
            raise ValueError("min_component_ratio phải nằm trong khoảng 0..1.")

        model_id = model_id or getattr(
            config, "SCENE_SEGMENTATION_MODEL", os.getenv("SCENE_SEGMENTATION_MODEL", DEFAULT_MODEL_ID)
        )
        paths = SceneLayerPaths.create(cls.get_scene_dir(project_id))
        input_hash = cls._input_hash(image_path, model_id)

        if not force_recreate:
            cached = cls._read_valid_cache(paths, input_hash)
            if cached is not None:
                logger.info("[SceneLayerProcessor] Dùng lại scene layer cache.")
                return cached

        with Image.open(image_path) as opened:
            source = opened.convert("RGB")
        width, height = source.size
        if width < 256 or height < 256:
            raise SceneLayerError("Ảnh quá nhỏ để tách lớp ổn định; cần tối thiểu 256x256.")

        logger.info("[SceneLayerProcessor] Phân tích semantic scene bằng %s", model_id)
        class_map, id2label, backend_info = cls._run_segformer(source, model_id)
        if class_map.shape != (height, width):
            raise SceneLayerInferenceError(
                f"Kích thước class map sai: {class_map.shape}, cần {(height, width)}"
            )

        leaves_raw = cls._labels_to_mask(class_map, id2label, cls.VEGETATION_LABELS)
        architecture_raw = cls._labels_to_mask(class_map, id2label, cls.ARCHITECTURE_LABELS)
        sky_raw = cls._labels_to_mask(class_map, id2label, cls.SKY_LABELS)
        clouds_raw = cls._labels_to_mask(class_map, id2label, cls.CLOUD_LABELS)

        # ADE20K usually merges clouds into sky. A conservative color cue is
        # used only inside the AI-detected sky mask, never across the image.
        if float(clouds_raw.mean()) < 0.001 and sky_raw.any():
            clouds_raw = cls._estimate_clouds_inside_sky(source, sky_raw)

        min_pixels = max(32, int(width * height * min_component_ratio))
        leaves_clean = cls._clean_binary_mask(leaves_raw, min_pixels)
        architecture_clean = cls._clean_binary_mask(architecture_raw, min_pixels)
        sky_clean = cls._clean_binary_mask(sky_raw, min_pixels)
        clouds_clean = cls._clean_binary_mask(clouds_raw & sky_clean, min_pixels)

        if float(leaves_clean.mean()) < 0.002:
            raise SceneLayerInferenceError(
                "AI không tìm thấy đủ vùng lá/cây. Không tạo mask hình học thay thế."
            )
        if float(sky_clean.mean()) < 0.002:
            logger.warning("[SceneLayerProcessor] Không tìm thấy vùng trời đáng kể.")

        leaves_near, leaves_mid = cls._split_vegetation_by_scale_and_position(leaves_clean)
        # Keep masks mutually exclusive to prevent double compositing.
        leaves_mid &= ~leaves_near

        mask_arrays = {
            "leaves_all": leaves_clean,
            "leaves_near": leaves_near,
            "leaves_mid": leaves_mid,
            "architecture": architecture_clean,
            "sky": sky_clean,
            "clouds": clouds_clean,
        }
        mask_paths = {
            "leaves_all": paths.leaves_all_mask,
            "leaves_near": paths.leaves_near_mask,
            "leaves_mid": paths.leaves_mid_mask,
            "architecture": paths.architecture_mask,
            "sky": paths.sky_mask,
            "clouds": paths.clouds_mask,
        }
        for name, array in mask_arrays.items():
            cls._save_mask(array, mask_paths[name], feather_radius=feather_radius)

        cls._save_rgba_layer(source, paths.leaves_near_mask, paths.leaves_near_layer)
        cls._save_rgba_layer(source, paths.leaves_mid_mask, paths.leaves_mid_layer)
        cls._save_rgba_layer(source, paths.architecture_mask, paths.architecture_layer)
        cls._save_rgba_layer(source, paths.sky_mask, paths.sky_layer)
        source.save(paths.source, "PNG")
        cls._save_mask_preview(source, mask_arrays, paths.mask_preview)

        coverage = {name: round(float(mask.mean()), 6) for name, mask in mask_arrays.items()}
        warnings: list[str] = []
        if coverage["architecture"] < 0.02:
            warnings.append("Mask kiến trúc có độ phủ thấp; cần xem preview trước khi fill nền.")
        if coverage["sky"] < 0.02:
            warnings.append("Mask bầu trời có độ phủ thấp; nên tắt chuyển động mây.")
        if coverage["leaves_near"] < 0.002:
            warnings.append("Không có cụm lá gần đủ lớn; chỉ animate leaves_mid.")

        manifest: dict[str, Any] = {
            "schema_name": SCHEMA_NAME,
            "schema_version": SCHEMA_VERSION,
            "processor_version": PROCESSOR_VERSION,
            "created_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "project_id": project_id,
            "input_image": str(image_path.resolve()),
            "input_hash": input_hash,
            "width": width,
            "height": height,
            "backend": backend_info,
            "coverage": coverage,
            "warnings": warnings,
            "fill_status": "pending",
            "animation_status": "pending",
            "source_path": str(paths.source.resolve()),
            "mask_preview_path": str(paths.mask_preview.resolve()),
            "masks": {name: str(path.resolve()) for name, path in mask_paths.items()},
            "layers": {
                "leaves_near": str(paths.leaves_near_layer.resolve()),
                "leaves_mid": str(paths.leaves_mid_layer.resolve()),
                "architecture_static": str(paths.architecture_layer.resolve()),
                "sky": str(paths.sky_layer.resolve()),
            },
            "motion_defaults": {
                "leaves_near_amplitude_px": 7,
                "leaves_mid_amplitude_px": 4,
                "cloud_speed_px_per_minute": 12,
                "mask_fill_expand_px": 16,
            },
        }
        cls._atomic_write_json(manifest, paths.manifest)
        logger.info("[SceneLayerProcessor] Đã tạo scene layers tại %s", paths.root)
        return manifest

    @staticmethod
    def _input_hash(image_path: Path, model_id: str) -> str:
        sha = hashlib.sha256()
        with image_path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                sha.update(chunk)
        sha.update(PROCESSOR_VERSION.encode("utf-8"))
        sha.update(model_id.encode("utf-8"))
        return sha.hexdigest()

    @classmethod
    def _read_valid_cache(cls, paths: SceneLayerPaths, input_hash: str) -> dict[str, Any] | None:
        if not paths.manifest.is_file():
            return None
        try:
            manifest = json.loads(paths.manifest.read_text(encoding="utf-8"))
            if manifest.get("input_hash") != input_hash:
                return None
            required = [
                paths.source,
                paths.leaves_all_mask,
                paths.leaves_near_mask,
                paths.leaves_mid_mask,
                paths.architecture_mask,
                paths.sky_mask,
                paths.clouds_mask,
                paths.mask_preview,
            ]
            if all(path.is_file() and path.stat().st_size > 128 for path in required):
                return manifest
        except Exception as exc:
            logger.warning("[SceneLayerProcessor] Cache không hợp lệ: %s", exc)
        return None

    @staticmethod
    def _run_segformer(image: Image.Image, model_id: str) -> tuple[np.ndarray, dict[int, str], dict[str, Any]]:
        try:
            import torch
            from transformers import AutoImageProcessor, AutoModelForSemanticSegmentation
        except Exception as exc:
            raise SceneLayerDependencyError(
                "Thiếu torch/transformers. Hãy cài dependencies trước khi phân tích cảnh."
            ) from exc

        if torch.cuda.is_available():
            device = torch.device("cuda")
        elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            device = torch.device("mps")
        else:
            device = torch.device("cpu")

        try:
            processor = AutoImageProcessor.from_pretrained(model_id)
            model = AutoModelForSemanticSegmentation.from_pretrained(model_id)
            model.to(device)
            model.eval()
            inputs = processor(images=image, return_tensors="pt")
            inputs = {key: value.to(device) for key, value in inputs.items()}
            with torch.inference_mode():
                outputs = model(**inputs)
            logits = torch.nn.functional.interpolate(
                outputs.logits,
                size=(image.height, image.width),
                mode="bilinear",
                align_corners=False,
            )
            class_map = logits.argmax(dim=1)[0].detach().cpu().numpy().astype(np.int32)
            id2label = {int(key): str(value) for key, value in model.config.id2label.items()}
            return class_map, id2label, {
                "name": "segformer_ade20k",
                "model_id": model_id,
                "device": str(device),
            }
        except SceneLayerError:
            raise
        except Exception as exc:
            raise SceneLayerInferenceError(f"SegFormer chạy thất bại: {exc}") from exc
        finally:
            try:
                del model
                if device.type == "cuda":
                    torch.cuda.empty_cache()
            except Exception:
                pass

    @staticmethod
    def _normalize_label(label: str) -> set[str]:
        cleaned = label.lower().replace("_", " ").replace("-", " ")
        for separator in (";", ",", "/", "|"):
            cleaned = cleaned.replace(separator, " ")
        return {part.strip() for part in cleaned.split() if part.strip()}

    @classmethod
    def _labels_to_mask(
        cls,
        class_map: np.ndarray,
        id2label: dict[int, str],
        wanted: Iterable[str],
    ) -> np.ndarray:
        wanted_normalized = {item.lower() for item in wanted}
        selected_ids: list[int] = []
        for class_id, label in id2label.items():
            words = cls._normalize_label(label)
            normalized_label = label.lower()
            if words & wanted_normalized or any(item in normalized_label for item in wanted_normalized):
                selected_ids.append(class_id)
        if not selected_ids:
            return np.zeros_like(class_map, dtype=bool)
        return np.isin(class_map, selected_ids)

    @staticmethod
    def _estimate_clouds_inside_sky(image: Image.Image, sky_mask: np.ndarray) -> np.ndarray:
        rgb = np.asarray(image, dtype=np.uint8)
        r = rgb[..., 0].astype(np.int16)
        g = rgb[..., 1].astype(np.int16)
        b = rgb[..., 2].astype(np.int16)
        brightness = (r + g + b) / 3.0
        neutral = (np.maximum.reduce([r, g, b]) - np.minimum.reduce([r, g, b])) < 52
        white_blue = (brightness > 170) & neutral & (b > 145)
        return sky_mask & white_blue

    @classmethod
    def _clean_binary_mask(cls, mask: np.ndarray, min_pixels: int) -> np.ndarray:
        mask = np.asarray(mask, dtype=bool)
        if not mask.any():
            return mask
        mask = cls._remove_small_components(mask, min_pixels)
        pil = Image.fromarray(mask.astype(np.uint8) * 255, mode="L")
        pil = pil.filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.MinFilter(5))
        pil = pil.filter(ImageFilter.MedianFilter(5))
        return np.asarray(pil, dtype=np.uint8) >= 128

    @staticmethod
    def _remove_small_components(mask: np.ndarray, min_pixels: int) -> np.ndarray:
        try:
            from scipy import ndimage

            labels, count = ndimage.label(mask)
            if count == 0:
                return mask
            sizes = np.bincount(labels.ravel())
            keep = sizes >= min_pixels
            keep[0] = False
            return keep[labels]
        except Exception:
            # Safe fallback when scipy is not installed. PIL cleanup still runs.
            return mask

    @classmethod
    def _split_vegetation_by_scale_and_position(cls, vegetation: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        height, width = vegetation.shape
        near = np.zeros_like(vegetation, dtype=bool)
        mid = np.zeros_like(vegetation, dtype=bool)
        try:
            from scipy import ndimage

            labels, count = ndimage.label(vegetation)
            sizes = np.bincount(labels.ravel())
            frame_area = width * height
            for component_id in range(1, count + 1):
                component = labels == component_id
                if not component.any():
                    continue
                ys, xs = np.nonzero(component)
                area_ratio = sizes[component_id] / frame_area
                touches_frame = (
                    xs.min() <= width * 0.025
                    or xs.max() >= width * 0.975
                    or ys.min() <= height * 0.025
                    or ys.max() >= height * 0.975
                )
                lower_foreground = float(np.median(ys)) > height * 0.67
                if area_ratio >= 0.012 and (touches_frame or lower_foreground):
                    near |= component
                else:
                    mid |= component
        except Exception:
            # Position-based fallback is semantic-mask dependent, not geometric
            # mask generation. It only separates an existing AI vegetation mask.
            yy = np.arange(height)[:, None]
            border = np.zeros_like(vegetation, dtype=bool)
            border[: max(1, int(height * 0.10)), :] = True
            border[:, : max(1, int(width * 0.10))] = True
            border[int(height * 0.72) :, :] = True
            near = vegetation & border
            mid = vegetation & ~near
        return near, mid

    @staticmethod
    def _save_mask(mask: np.ndarray, path: Path, feather_radius: int = 0) -> None:
        image = Image.fromarray(np.asarray(mask, dtype=np.uint8) * 255, mode="L")
        if feather_radius > 0:
            image = image.filter(ImageFilter.GaussianBlur(radius=feather_radius))
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path, "PNG")

    @staticmethod
    def _save_rgba_layer(source: Image.Image, mask_path: Path, output_path: Path) -> None:
        with Image.open(mask_path) as opened_mask:
            alpha = opened_mask.convert("L")
        layer = source.convert("RGBA")
        layer.putalpha(alpha)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        layer.save(output_path, "PNG")

    @staticmethod
    def _save_mask_preview(source: Image.Image, masks: dict[str, np.ndarray], output_path: Path) -> None:
        preview = source.convert("RGBA")
        overlay = Image.new("RGBA", source.size, (0, 0, 0, 0))
        colors = {
            "leaves_near": (255, 70, 70, 120),
            "leaves_mid": (255, 190, 40, 105),
            "architecture": (65, 130, 255, 85),
            "sky": (45, 220, 255, 75),
            "clouds": (255, 255, 255, 125),
        }
        for name, color in colors.items():
            mask = Image.fromarray(masks[name].astype(np.uint8) * 255, mode="L")
            color_layer = Image.new("RGBA", source.size, color)
            overlay.alpha_composite(Image.composite(color_layer, Image.new("RGBA", source.size), mask))
        preview = Image.alpha_composite(preview, overlay)

        # Compact legend, no external font dependency.
        draw = ImageDraw.Draw(preview)
        legend = [
            ("near leaves", colors["leaves_near"]),
            ("mid leaves", colors["leaves_mid"]),
            ("architecture", colors["architecture"]),
            ("sky", colors["sky"]),
            ("clouds", colors["clouds"]),
        ]
        x, y = 14, 14
        box_w, box_h = 148, 18 * len(legend) + 12
        draw.rounded_rectangle((x - 6, y - 6, x + box_w, y + box_h), radius=7, fill=(0, 0, 0, 155))
        for index, (label, color) in enumerate(legend):
            row_y = y + index * 18
            draw.rectangle((x, row_y, x + 12, row_y + 12), fill=color)
            draw.text((x + 18, row_y), label, fill=(255, 255, 255, 255))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        preview.convert("RGB").save(output_path, "PNG")

    @staticmethod
    def _atomic_write_json(data: dict[str, Any], destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=destination.name, suffix=".tmp", dir=destination.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            Path(temp_name).replace(destination)
        except Exception:
            Path(temp_name).unlink(missing_ok=True)
            raise


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Analyze semantic scene layers.")
    parser.add_argument("image", type=Path)
    parser.add_argument("--project-id", default="scene_layer_test")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    result = SceneLayerProcessor.analyze_scene(
        args.image,
        args.project_id,
        force_recreate=args.force,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
