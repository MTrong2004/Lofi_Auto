# ĐẶC TẢ CORE BUILD - VIDEO LO-FI (rút gọn từ v4.9)

**Mục đích:** bản rút gọn chỉ giữ luồng **Core bắt buộc** để code trước tiên - audio local + ảnh local → render → verify → manifest. Mọi phần Enhanced (vibe, ambience, Parallax, tách lớp, xu hướng) và Optional (Stable Diffusion, provider online, tự sáng tác nhạc) đã được lược bỏ khỏi bản này; xem file gốc `quy_trinh_lofi_v4_9_trinh_bay_chuan.md` khi cần triển khai các phần đó.

**Cấu hình mục tiêu:** Ryzen 7 5800H, RAM 16GB, RTX 3050 Ti 4GB
**Đầu ra:** Full HD 1920x1080, 24 FPS

---

## 1. Nguyên tắc phạm vi

Mỗi tính năng được phân loại để hệ thống luôn có một đường xuất video khả dụng:

- **Core:** bắt buộc để tạo, lưu, render và kiểm tra video.
- **Enhanced/Optional:** không được chặn hoặc làm thất bại luồng Core - bỏ qua hoàn toàn trong bản rút gọn này.

### 1.1. Core bao gồm

- Quản lý dự án, trạng thái, khóa ghi và phục hồi.
- Nhập audio và ảnh từ máy.
- FFprobe, chuẩn hóa audio, lưu SHA-256 và metadata.
- Ảnh phẳng với zoom/pan nhẹ làm phương án tối thiểu.
- Hàng đợi công việc, render nền, hủy, tiếp tục và checkpoint.
- Render phân đoạn, nối, xác minh và tạo manifest.

### 1.2. Đường fallback bắt buộc (luôn phải chạy được)

```text
Audio local hợp lệ + ảnh local hợp lệ
→ chuẩn hóa audio
→ ảnh phẳng với zoom/pan nhẹ
→ render nền
→ verify
→ xuất video và manifest
```

---

## 2. Luồng Core bắt buộc

```text
Tạo hoặc mở dự án
→ nhập audio local → FFprobe → rights review → chuẩn hóa
→ nhập ảnh local → kiểm tra → scale đúng profile (1920x1080)
→ preview ảnh phẳng → người dùng duyệt
→ render phân đoạn → nối → verify → manifest
```

**Quy tắc nhánh áp dụng cho bản rút gọn này:**
- Chỉ chạy bước khi input contract của bước trước hợp lệ.
- Fallback (ảnh phẳng, không ambience/vibe) phải thỏa output contract của bước sau và được ghi trong manifest.

---

## 3. Quản lý dự án

Mỗi dự án có một `project_id` và file snapshot riêng:

```text
data/projects/<project_id>/project.json
```

Ví dụ:

```json
{
  "schema_name": "project",
  "schema_version": 2,
  "project_id": "lofi_20260714_001",
  "snapshot_sequence": 105,
  "database_revision": 105,
  "workflow_status": {
    "audio": "approved",
    "image": "approved",
    "preview": "not_started",
    "render": "not_started",
    "output": "not_started"
  },
  "audio_path": null,
  "image_candidates": [],
  "selected_image": null,
  "animation_config": {},
  "render_job": {},
  "final_video": null,
  "created_at_utc": "2026-07-13T21:00:00Z",
  "updated_at_utc": "2026-07-13T21:00:00Z"
}
```

### Quy tắc

- Lưu sau mỗi hành động quan trọng.
- Ghi JSON theo giao thức nguyên tử: chỉ đổi tên trong cùng filesystem sau khi flush, xác minh, fsync file và sau đó fsync thư mục cha khi nền tảng hỗ trợ.
- SQLite là nguồn chuẩn của trạng thái hiện hành; không phụ thuộc trạng thái tạm của giao diện hoặc `project.json`. SQLite phải bật foreign keys, `busy_timeout`; WAL chỉ bật khi filesystem hỗ trợ khóa tin cậy.
- `project.json` là snapshot/export; mở lại ứng dụng phục hồi từ SQLite rồi đối chiếu file và hash.
- Mỗi dự án chỉ có một writer logic tại một thời điểm. Mọi cập nhật SQLite vẫn phải dùng transaction.
- Mỗi bước có `input_hash`; chỉ dùng lại kết quả khi input/config/producer hash không đổi, asset đã verify, output hash khớp và mọi dependency vẫn hợp lệ.

---

## 4. Nhập và chuẩn hóa audio (Core)

**File chính:** `step1_music_hunter.py` (chỉ phần nhập file local - bỏ Trend Hunter, tìm kiếm online, tự sáng tác nhạc)

### Đầu vào

- Chọn file MP3, M4A, WAV hoặc FLAC trong máy.

### Quy trình

```text
Nhận file
→ Kiểm tra bằng FFprobe (codec, thời lượng, luồng âm thanh)
→ Rights review (người dùng xác nhận quyền sử dụng)
→ Chuẩn hóa audio (loudness)
→ Lưu credit và SHA-256
```

### Metadata bắt buộc

```json
{
  "schema_name": "track_metadata",
  "schema_version": 1,
  "track_id": "unique_track_id",
  "title": "Track title",
  "author": "Creator",
  "source": "local_upload",
  "duration_seconds": 180,
  "license": "unknown",
  "license_url": null,
  "download_status": "not_downloaded"
}
```

### Trạng thái quyền sử dụng (bắt buộc kiểm tra trước khi đưa vào render)

- `licensed_for_use`: có bằng chứng giấy phép phù hợp.
- `user_attested_ownership`: người dùng xác nhận họ sở hữu hoặc tự tạo tài sản; phải lưu audit record (actor, thời điểm, ghi chú).
- `requires_manual_review`: cần người dùng kiểm tra thêm - **không đưa vào render cho đến khi được xác nhận**.

### Chuẩn hóa chất lượng âm thanh

- Dùng loudness normalization thay vì chỉ tăng giảm volume thủ công.
- Mục tiêu mặc định cho video dài: khoảng `-14 LUFS` đến `-16 LUFS`.
- True peak không vượt khoảng `-1 dBTP`.
- Kiểm tra clipping, im lặng bất thường và kênh trái/phải lệch mạnh.
- Fade đầu 2-5 giây và fade cuối 5-10 giây.
- Giữ bản audio gốc; mọi xử lý tạo ra file mới.

### Cải thiện bắt buộc

- Không tự đoán giấy phép.
- Dùng FFprobe kiểm tra codec, thời lượng và luồng âm thanh.
- Dùng SHA-256 và `track_id` để chống trùng.

### Đầu ra

```text
data/input_audio/<track_id>/source.*
data/input_audio/<track_id>/normalized.m4a
data/metadata/tracks/<track_id>.json
```

> **Ghi chú phạm vi:** ambience, EQ, vibe profile, loop crossfade và tempo-change là phần **Enhanced** - không nằm trong Core Build. Nếu video cần lặp dài hơn độ dài file gốc, cân nhắc bổ sung loop crossfade đơn giản sớm hơn dự kiến; xem mục "Loop audio mượt" trong file gốc khi cần.

---

## 5. Nhập ảnh local (Core)

**File chính:** `step2_image_provider.py` (chỉ phần ảnh tải từ máy - bỏ Stable Diffusion, provider online, hệ thống prompt)

### Nguồn ảnh

- Ảnh tải từ máy - nguồn Core luôn được hỗ trợ, không phụ thuộc dịch vụ ngoài.

### Quy trình

```text
Nhận ảnh
→ Kiểm tra file (định dạng, kích thước, không hỏng)
→ Scale đúng profile đích: 1920x1080
→ Lưu ảnh gốc, ảnh Full HD và metadata
```

### Yêu cầu ảnh tối thiểu

- Định dạng hỗ trợ: PNG, JPG.
- Không dùng ảnh có độ phân giải thấp hơn 1920x1080 mà không cảnh báo chất lượng khi upscale.
- Giữ ảnh gốc; ảnh Full HD là file phái sinh riêng.

### Metadata ảnh (bản Core)

```json
{
  "schema_name": "image_metadata",
  "schema_version": 1,
  "provider": "local_upload",
  "source_size": "original",
  "final_size": "1920x1080",
  "upscale_method": "lanczos_fallback",
  "source_path": "...",
  "full_hd_path": "..."
}
```

### Ảnh phẳng với zoom/pan nhẹ (phương án dựng hình mặc định Core)

- Đây là phương án tối thiểu - **luôn phải hoạt động** kể cả khi Parallax/tách lớp (Enhanced) chưa triển khai.
- Chuyển động nhẹ, tuần hoàn, không cắt xén chủ thể quan trọng.
- Preview phải kiểm tra: không viền đen, không nhấp nháy mạnh, màu/FPS/kích thước giống render chính.

> **Ghi chú phạm vi:** hệ thống prompt AI, upscale AI, tách lớp Parallax 2.5D và Stable Diffusion là **Enhanced/Optional** - xem mục 5 (phần AI) và mục 6-8 trong file gốc khi mở rộng.

---

## 6. Render, verify và manifest (mức tối thiểu)

```text
Preview ảnh phẳng đã duyệt
→ render phân đoạn (background job, có checkpoint để resume)
→ nối các phân đoạn
→ verify (kiểm tra stream, timestamp, thời lượng, không hỏng)
→ xuất video + manifest
```

### Nguyên tắc bắt buộc

- Mỗi job render khai báo tiến độ dựa trên dữ liệu thật (không tăng giả để tạo cảm giác đang chạy).
- Có thể hủy và tiếp tục (resume) từ checkpoint, không render lại từ đầu.
- Verify phải chạy **sau khi nối/mux**, không chỉ tin kết quả từng đoạn riêng lẻ.
- Manifest ghi lại: input hash, cấu hình render, hash của asset đã verify.

---

## 7. Test và release gate tối thiểu (Core)

### End-to-end bắt buộc trước khi coi Core "chạy được"

- Audio local + ảnh local → asset video `verified`, output job `completed` và manifest `published`.
- Audio preview → duyệt → render phân đoạn → resume → manifest.

### Điều kiện chặn "release" bản Core (rút gọn từ release gate v4.9)

Không coi Core hoàn thành nếu còn:

- Lỗi Critical hoặc High chưa xử lý (mất/hỏng dữ liệu, thực thi ngoài ý muốn, verifier báo đạt sai).
- Core end-to-end chưa đạt trên cấu hình mục tiêu (Ryzen 7 5800H / RTX 3050 Ti 4GB).
- Recovery test (resume sau crash) chưa đạt.
- Output verifier có thể báo đạt với file thiếu stream, sai timestamp/frame rate, sai thời lượng, hoặc chưa verify lại sau khi nối.
- License/rights state có thể tự động nâng thành hợp lệ khi thiếu bằng chứng.

---

## 8. Việc gì làm sau khi Core chạy ổn

Theo đúng thứ tự phụ thuộc gợi ý (chi tiết đầy đủ nằm trong file gốc):

1. **Enhanced - vibe/ambience/loop** (mục 4 phần vibe trong file gốc).
2. **Enhanced - Parallax 2.5D/tách lớp** (mục 7-8 file gốc) - cần môi trường GPU riêng cho SAM/depth/inpainting.
3. **Optional - Stable Diffusion Local** (mục 6 file gốc) - phần dài và phức tạp nhất, chỉ nên bắt đầu khi Core + Enhanced ảnh tĩnh đã ổn định.
4. **Optional - Trend Hunter, tự sáng tác nhạc, provider online** - không phụ thuộc kỹ thuật vào các phần trên, có thể làm song song nếu có nhân lực riêng.

Không nên bắt đầu mục 3-4 trước khi mục 6 (Core render/verify/manifest) đã qua toàn bộ test end-to-end và fault-injection cơ bản.
