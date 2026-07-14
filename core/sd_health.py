import logging
import json
from datetime import datetime, timezone
from pathlib import Path
import config
from core.schemas import validate_data_schema
from core.sd_adapter import SDAdapter

logger = logging.getLogger("lofi_automation")

class SDHealthChecker:
    """
    Thực hiện kiểm tra sức khỏe Stable Diffusion cục bộ và xuất báo cáo (Mục 6.11).
    """
    @classmethod
    def run_health_check(cls, api_url: str, report_out_path: Path) -> dict:
        adapter = SDAdapter(api_url)
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        
        report = {
            "schema_name": "sd_health_report",
            "schema_version": 1,
            "api_check": "failed",
            "model_load_check": "failed",
            "generation_check": "failed",
            "tested_at_utc": now_str
        }
        
        try:
            # 1. API check
            adapter.discover_api()
            is_cap_ok = adapter.capability_check()
            if not is_cap_ok:
                raise ValueError("Không hỗ trợ đủ các API endpoints bắt buộc.")
            report["api_check"] = "passed"
            
            # 2. Model load check
            # Kiểm tra xem có models khả dụng không
            r = requests_get_models(api_url)
            if not r:
                raise ValueError("Không có model checkpoint nào được tải.")
            report["model_load_check"] = "passed"
            
            # 3. Generation check (Test render 256x256 nhẹ)
            test_payload = {
                "prompt": "test lofi style background",
                "steps": 5,
                "width": 256,
                "height": 256,
                "cfg_scale": 5,
                "seed": 42
            }
            img_b64 = adapter.txt2img(test_payload)
            if img_b64:
                report["generation_check"] = "passed"
                
        except Exception as e:
            logger.error(f"[SDHealth] Kiểm tra sức khỏe thất bại: {e}")
            report["error_detail"] = str(e)
            
        # Xác thực báo cáo theo schema
        try:
            validate_data_schema(report, "sd_health_report")
        except Exception as ve:
            logger.error(f"[SDHealth] Báo cáo sức khỏe không khớp schema: {ve}")
            
        # Ghi báo cáo ra file an toàn
        try:
            report_out_path.parent.mkdir(parents=True, exist_ok=True)
            with open(report_out_path, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
            logger.info(f"[SDHealth] Đã xuất báo cáo sức khỏe tại: {report_out_path.name}")
        except Exception as write_err:
            logger.error(f"[SDHealth] Không ghi được file báo cáo: {write_err}")
            
        return report

def requests_get_models(api_url: str) -> list:
    import requests
    try:
        r = requests.get(f"{api_url}/sdapi/v1/sd-models", timeout=5)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return []
