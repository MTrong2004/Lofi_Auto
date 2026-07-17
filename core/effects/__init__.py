"""
AI FILE NOTE - PACKAGE core.effects
Chức năng chính:
- Quản lý hiệu ứng video overlay: khám phá, phân tích nền, gợi ý và ghép (composite) bằng FFmpeg.
Nội dung package:
- manifest.py: sổ đăng ký (registry) các hiệu ứng video khả dụng.
- analyzer.py: tự nhận diện loại nền của video hiệu ứng.
- recommender.py: hồ sơ AI + xếp hạng hiệu ứng theo hướng local-first.
- compositor.py: builder chuỗi filter FFmpeg dùng chung để ghép hiệu ứng.
Ghi chú:
- __init__ chỉ đánh dấu package (không export symbol nào).
"""
