"""
AI FILE NOTE - PACKAGE core.text
Chức năng chính:
- Overlay chữ, caption và render phụ đề (subtitle/karaoke) cho video.
Nội dung package:
- ass_renderer.py: render phụ đề karaoke ra file ASS.
- effect_manifest.py: lưu profile chữ (text effect) theo từng project.
- effect_recommender.py: AI gợi ý profile chữ + fallback local.
- effect_renderer.py: sinh file ASS cho chữ động.
- provider.py: điều phối (orchestrate) chữ động tuỳ chọn.
- caption_writer.py: sinh title/description/hashtag cho upload YouTube.
Ghi chú:
- __init__ chỉ đánh dấu package (không export symbol nào).
"""
