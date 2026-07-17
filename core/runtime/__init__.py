"""
AI FILE NOTE - PACKAGE core.runtime
Chức năng chính:
- Hạ tầng chạy: trạng thái project, validation, lập lịch, khoá tài nguyên và lưu trữ.
Nội dung package:
- db.py: lớp database lõi (lưu trữ dữ liệu bền vững).
- schemas.py: định nghĩa và validation schema dữ liệu.
- project_manager.py: quản lý vòng đời project.
- cache_manager.py: quản lý cache.
- resource_scheduler.py: lập lịch dùng tài nguyên.
- lock_manager.py: khoá tài nguyên phần cứng (tránh dùng đồng thời).
- render_worker.py: tiến trình worker thực hiện render.
Ghi chú:
- __init__ chỉ đánh dấu package (không export symbol nào).
"""
