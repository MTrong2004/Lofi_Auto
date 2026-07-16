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
