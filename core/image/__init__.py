"""
AI FILE NOTE - PACKAGE core.image
Chức năng chính:
- Sinh và xử lý ảnh nền: tạo ảnh, nâng cấp, ước lượng chiều sâu, tách lớp cảnh và hiệu ứng parallax.
Nội dung package:
- sd_manager.py: quản lý Stable Diffusion (khởi động/gọi WebUI local).
- provider_capability.py: registry năng lực của các provider sinh ảnh (SD Local, AI Horde, Pollinations...).
- upscaler.py: nâng ảnh lên Full HD (SD extra-single-image, fallback PIL Lanczos).
- depth_estimator.py: ước lượng bản đồ độ sâu trên CPU.
- parallax_processor.py: tạo chuyển động parallax 2.5D từ ảnh + depth.
- scene_layer_processor.py: phân tích/tách ảnh thành các lớp cảnh (scene layers).
- vegetation_masker.py: mask ngữ nghĩa vùng cây cối/thực vật (CPU).
Ghi chú:
- __init__ chỉ đánh dấu package (không export symbol nào).
"""
