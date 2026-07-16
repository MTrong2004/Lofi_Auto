"""
AI FILE NOTE - SCHEMAS VALIDATION
Chức năng chính:
- Định nghĩa 12 cấu trúc schema (dạng JSON-like schema validation) cho dữ liệu hệ thống (projects, tracks, assets, jobs, locks).
- Kiểm tra tính toàn vẹn và xác thực kiểu dữ liệu (data types, formats, required/optional fields).
Đầu vào chính:
- Dữ liệu JSON/Dict cần kiểm tra, tên schema tương ứng.
Đầu ra chính:
- Trả về dữ liệu đã được làm sạch, hoặc raise lỗi `SchemaValidationError` nếu vi phạm.
API được file khác sử dụng:
- `validate_data_schema()`, `SchemaValidationError`
Phụ thuộc quan trọng:
- re, datetime
Lưu ý khi sửa:
- Mọi thay đổi trường bắt buộc trong schema phải đồng bộ với SQLite schema và các file logic khác.
"""
import re
from datetime import datetime

class SchemaValidationError(Exception):
    """Lỗi khi dữ liệu không khớp với định nghĩa Schema."""
    pass

def validate_iso_timestamp(ts):
    if not isinstance(ts, str):
        return False
    # Cho phép định dạng ISO 8601: YYYY-MM-DDTHH:MM:SSZ hoặc có offset
    pattern = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})$"
    return bool(re.match(pattern, ts))

def check_keys(data, required_types, optional_types=None):
    """
    Kiểm tra các trường bắt buộc, kiểu dữ liệu tương ứng.
    required_types: dict {field_name: expected_type}
    optional_types: dict {field_name: expected_type}
    """
    if optional_types is None:
        optional_types = {}

    # Kiểm tra các trường bắt buộc
    for key, expected_type in required_types.items():
        if key not in data:
            raise SchemaValidationError(f"Thiếu trường bắt buộc: '{key}'")
        
        # Nếu expected_type là list/tuple chứa các kiểu dữ liệu chấp nhận được
        if isinstance(expected_type, tuple):
            if not any(isinstance(data[key], t) or (t is None and data[key] is None) for t in expected_type):
                types_str = " hoặc ".join(t.__name__ if t else "None" for t in expected_type)
                raise SchemaValidationError(f"Trường '{key}' phải có kiểu {types_str}, nhận được {type(data[key]).__name__}")
        else:
            if not isinstance(data[key], expected_type) and not (expected_type is float and isinstance(data[key], int)):
                # Đặc cách: int có thể làm float
                raise SchemaValidationError(f"Trường '{key}' phải có kiểu {expected_type.__name__}, nhận được {type(data[key]).__name__}")

    # Kiểm tra các trường tùy chọn nếu xuất hiện
    for key, expected_type in optional_types.items():
        if key in data:
            if isinstance(expected_type, tuple):
                if not any(isinstance(data[key], t) or (t is None and data[key] is None) for t in expected_type):
                    types_str = " hoặc ".join(t.__name__ if t else "None" for t in expected_type)
                    raise SchemaValidationError(f"Trường '{key}' phải có kiểu {types_str}, nhận được {type(data[key]).__name__}")
            else:
                if not isinstance(data[key], expected_type) and not (expected_type is float and isinstance(data[key], int)) and not (data[key] is None):
                    # Đặc cách: int có thể làm float, hoặc None
                    if expected_type is not None:
                        raise SchemaValidationError(f"Trường '{key}' phải có kiểu {expected_type.__name__}, nhận được {type(data[key]).__name__}")

SCHEMAS = {
    "project": {
        "required": {
            "schema_name": str,
            "schema_version": int,
            "project_id": str,
            "snapshot_sequence": int,
            "database_revision": int,
            "workflow_status": dict,
            "created_at_utc": str,
            "updated_at_utc": str
        },
        "optional": {
            "trend_context": (dict, type(None)),
            "track": dict,
            "audio_path": (str, type(None)),
            "image_candidates": list,
            "selected_image": (str, type(None)),
            "layers": (dict, type(None)),
            "animation_config": dict,
            "render_job": dict,
            "final_video": (str, type(None))
        }
    },
    "track_metadata": {
        "required": {
            "schema_name": str,
            "schema_version": int,
            "track_id": str,
            "title": str,
            "author": str,
            "source": str,
            "url": str,
            "duration_seconds": (int, float),
            "license": str,
            "views": int,
            "likes": int,
            "relevance_score": (int, float),
            "source_trust_score": (int, float),
            "risk_reasons": list,
            "download_status": str
        },
        "optional": {
            "license_url": (str, type(None))
        }
    },
    "rights_review": {
        "required": {
            "schema_name": str,
            "schema_version": int,
            "asset_id": str,
            "status": str,
            "license": str,
            "evidence": list,
            "reviewed_by": (str, type(None)),
            "reviewed_at_utc": (str, type(None))
        }
    },
    "job": {
        "required": {
            "schema_name": str,
            "schema_version": int,
            "job_id": str,
            "project_id": str,
            "job_type": str,
            "job_status": str,
            "created_at_utc": str,
            "updated_at_utc": str
        },
        "optional": {
            "current_step": (str, type(None)),
            "idempotency_key": (str, type(None)),
            "request_payload_hash": (str, type(None)),
            "owner_id": (str, type(None)),
            "lease_expires_at_utc": (str, type(None)),
            "config_snapshot": (str, type(None))
        }
    },
    "render_job": {
        "required": {
            "schema_name": str,
            "schema_version": int,
            "job_id": str,
            "project_id": str,
            "job_status": str,
            "created_at_utc": str,
            "video_codec": str,
            "requested_video_encoder": str,
            "resolution": dict,
            "fps": dict,
            "input_hash": str,
            "config_hash": str,
            "producer_version": str,
            "segments": list
        },
        "optional": {
            "actual_video_encoder": (str, type(None)),
            "config_snapshot_path": (str, type(None))
        }
    },
    "segment_metadata": {
        "required": {
            "schema_name": str,
            "schema_version": int,
            "segment_index": int,
            "start_seconds_display": (int, float),
            "duration_seconds_display": (int, float),
            "start_pts": int,
            "end_pts_exclusive": int,
            "video_time_base": str,
            "contains_audio": bool,
            "animation_time_offset_pts": int,
            "processing_status": str,
            "review_status": str,
            "video_sha256": str,
            "video_codec": str,
            "requested_video_encoder": str,
            "actual_video_encoder": str
        },
        "optional": {
            "audio_start_sample": (int, type(None)),
            "audio_end_sample_exclusive": (int, type(None)),
            "probe_result_path": (str, type(None)),
            "first_frame_hash": (str, type(None)),
            "last_frame_hash": (str, type(None))
        }
    },
    "error_record": {
        "required": {
            "schema_name": str,
            "schema_version": int,
            "error_id": str,
            "error_code": str,
            "category": str,
            "step": str,
            "message": str,
            "retryable": bool,
            "fallback_available": bool,
            "occurred_at_utc": str
        },
        "optional": {
            "job_id": (str, type(None)),
            "technical_detail": (str, type(None)),
            "suggested_action": (str, type(None))
        }
    },
    "output_manifest": {
        "required": {
            "schema_name": str,
            "schema_version": int,
            "project_id": str,
            "video_path": str,
            "video_sha256": str,
            "duration_seconds": (int, float),
            "resolution": str,
            "fps": dict,
            "video_codec": str,
            "requested_video_encoder": str,
            "actual_video_encoder": str,
            "audio_codec": str,
            "audio_sample_rate_hz": int,
            "pixel_format": str,
            "color_metadata": dict,
            "track_id": str,
            "app_version": str,
            "config_hash": str,
            "input_hash": str,
            "producer_version": str,
            "rendered_at_utc": str
        },
        "optional": {
            "video_encoder_fallback_reason": (str, type(None)),
            "requested_layer_mode": (str, type(None)),
            "actual_layer_mode": (str, type(None)),
            "image_seed": (int, type(None)),
            "animation_seed": (int, type(None)),
            "ffmpeg_version": (str, type(None)),
            "filter_builder_version": (str, type(None)),
            "verification_report_path": (str, type(None)),
            "rights_review_ids": list
        }
    },
    "image_metadata": {
        "required": {
            "schema_name": str,
            "schema_version": int,
            "provider": str,
            "model": str,
            "prompt": str,
            "negative_prompt": str,
            "seed": int,
            "source_size": str,
            "final_size": str,
            "upscale_method": str,
            "source_path": str,
            "full_hd_path": str
        }
    },
    "sd_health_report": {
        "required": {
            "schema_name": str,
            "schema_version": int,
            "api_check": str,
            "model_load_check": str,
            "generation_check": str,
            "tested_at_utc": str
        },
        "optional": {
            "error_detail": (str, type(None))
        }
    },
    "sd_install_state": {
        "required": {
            "schema_name": str,
            "schema_version": int,
            "installation_id": str,
            "ownership_mode": str,
            "state": str,
            "installed": bool,
            "running": bool,
            "healthy": bool,
            "ready": bool,
            "configured_port": int,
            "bind_host": str,
            "updated_at_utc": str
        },
        "optional": {
            "distribution_id": (str, type(None)),
            "version": (str, type(None)),
            "commit_or_release": (str, type(None)),
            "adapter_version": (str, type(None)),
            "install_root": (str, type(None)),
            "staging_path": (str, type(None)),
            "startup_profile": (str, type(None)),
            "last_completed_step": (str, type(None)),
            "resume_supported": (bool, type(None)),
            "rollback_version": (str, type(None)),
            "process_identity": (int, type(None)),
            "active_model_sha256": (str, type(None)),
            "health_report_path": (str, type(None)),
            "health_valid_until_utc": (str, type(None)),
            "last_error_id": (str, type(None))
        }
    }
}

def validate_data_schema(data, schema_name):
    """Xác thực định dạng dữ liệu JSON theo schema_name."""
    if not isinstance(data, dict):
        raise SchemaValidationError("Dữ liệu xác thực phải là một dictionary.")
    
    if schema_name not in SCHEMAS:
        raise SchemaValidationError(f"Không tìm thấy định nghĩa schema cho: '{schema_name}'")
        
    spec = SCHEMAS[schema_name]
    
    # 1. Kiểm tra schema_name trùng khớp trong dữ liệu
    if data.get("schema_name") != schema_name:
        raise SchemaValidationError(
            f"Trường 'schema_name' không trùng khớp: Yêu cầu '{schema_name}', nhận được '{data.get('schema_name')}'"
        )
        
    # 2. Kiểm tra kiểu dữ liệu của các key
    check_keys(data, spec["required"], spec.get("optional"))
    
    # 3. Kiểm tra các định dạng thời gian
    for key in ["created_at_utc", "updated_at_utc", "reviewed_at_utc", "occurred_at_utc", "rendered_at_utc"]:
        if key in data and data[key] is not None:
            if not validate_iso_timestamp(data[key]):
                raise SchemaValidationError(f"Trường '{key}' phải có định dạng timestamp ISO 8601 UTC (ví dụ: '2026-07-14T21:00:00Z')")
                
    # 4. Kiểm tra các trạng thái enum đặc thù
    if schema_name == "project":
        wf = data.get("workflow_status", {})
        valid_states = {
            "not_started", "queued", "queued_waiting_resource", "running", 
            "waiting_review", "approved", "verifying", "verified", 
            "cancelling", "cancelled", "failed", "stale", "interrupted", 
            "recovering", "paused_resource_limit", "invalidated", "completed", "skipped"
        }
        for mod, val in wf.items():
            if val not in valid_states:
                raise SchemaValidationError(f"workflow_status.{mod} chứa trạng thái không hợp lệ: '{val}'")
                
    elif schema_name == "track_metadata":
        status = data.get("download_status")
        valid_download_statuses = {"not_downloaded", "downloading", "downloaded", "failed"}
        if status not in valid_download_statuses:
            raise SchemaValidationError(f"download_status không hợp lệ: '{status}'")
            
    elif schema_name == "rights_review":
        status = data.get("status")
        valid_rights_statuses = {"requires_manual_review", "licensed_for_use", "user_attested_ownership", "trend_reference_only", "blocked_from_download"}
        if status not in valid_rights_statuses:
            raise SchemaValidationError(f"rights_review status không hợp lệ: '{status}'")
            
    return True

if __name__ == "__main__":
    # Test nhanh
    test_track = {
        "schema_name": "track_metadata",
        "schema_version": 1,
        "track_id": "test_123",
        "title": "A beautiful day",
        "author": "Antigravity",
        "source": "soundcloud",
        "url": "https://soundcloud.com/test",
        "duration_seconds": 180,
        "license": "CC-BY",
        "views": 1000,
        "likes": 50,
        "relevance_score": 9.5,
        "source_trust_score": 80,
        "risk_reasons": [],
        "download_status": "not_downloaded"
    }
    try:
        validate_data_schema(test_track, "track_metadata")
        print("Schema validation test passed!")
    except Exception as e:
        print("Schema validation test failed:", e)
