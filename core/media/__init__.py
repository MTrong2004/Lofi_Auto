"""
AI FILE NOTE - PACKAGE core.media
Chức năng chính:
- Thăm dò (probe), xử lý audio và kiểm định video đầu ra.
Nội dung package:
- probe.py: dùng ffprobe để phân tích stream, thời lượng, codec, đo độ ồn LUFS.
- audio_processor.py: xử lý audio nâng cao (chuẩn hoá, hiệu ứng...).
- output_verifier.py: kiểm tra tính toàn vẹn video cuối, so khớp spec và sinh manifest đầu ra.
Ghi chú:
- __init__ chỉ đánh dấu package (không export symbol nào).
"""
