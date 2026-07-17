"""
AI FILE NOTE - TEST EFFECT LIVE PREVIEW
Chức năng chính:
- Unit test (pytest) cho hàm _text_payload trong components.effect_live_preview.
- Kiểm tra payload trả về giữ đầy đủ các trường hoạt ảnh chữ (intro/hold/outro effect và duration).
Đầu vào chính:
- Dict profile text mẫu (enabled, content, position, màu, font, intro/hold/outro_effect, duration...).
Đầu ra chính:
- Kết quả assert pass/fail của pytest (không sinh file).
API được file khác sử dụng:
- Không (đây là file test, được pytest thu thập tự động qua tên test_*).
Phụ thuộc quan trọng:
- components.effect_live_preview._text_payload; chạy bằng pytest.
Lưu ý khi sửa:
- Đồng bộ tên khóa camelCase trong payload (introEffect, holdEffect, outroEffect, introDuration, outroDuration)
  với đầu ra thực tế của _text_payload; lệch tên là test đỏ.
"""
from components.effect_live_preview import _text_payload


def test_text_payload_includes_animation_fields():
    profile = {
        "enabled": True,
        "content": "Hello world",
        "position": "bottom_center",
        "text_color": "#FFFFFF",
        "outline_color": "#000000",
        "outline_width": 2.0,
        "font_size": 72,
        "bold": True,
        "font_style": "sans",
        "intro_effect": "slide_up",
        "hold_effect": "soft_glow",
        "outro_effect": "fade",
        "intro_duration": 1.2,
        "outro_duration": 0.8,
    }

    payload = _text_payload(profile)

    assert payload is not None
    assert payload["introEffect"] == "slide_up"
    assert payload["holdEffect"] == "soft_glow"
    assert payload["outroEffect"] == "fade"
    assert payload["introDuration"] == 1.2
    assert payload["outroDuration"] == 0.8
