# ĐẶC TẢ QUY TRÌNH SẢN XUẤT VIDEO LO-FI

**Phiên bản:** 4.5  
**Mô hình:** Chạy local, có người kiểm duyệt  
**Cấu hình mục tiêu:** Ryzen 7 5800H, RAM 16GB, RTX 3050 Ti 4GB  
**Đầu ra mặc định:** Full HD 1920×1080, 24 FPS

---

## 0. Kết quả rà soát phiên bản 4.5

Bản 4.2 đã sửa các nhóm lỗi chính: mô hình trạng thái `approved/verified`, giao thức publish giữa SQLite và filesystem, điều kiện tái sử dụng cache, quyền tải nhạc, điểm xu hướng khi thiếu dữ liệu, tính liên tục animation/audio, quy tắc scheduler GPU, dữ liệu manifest và các lỗi định dạng/phiên bản.

**Rà soát vòng 2 (4.3):** sửa tiếp mô hình trạng thái, chống stale writer bằng fencing token, phân biệt codec với encoder, quy tắc timeline nửa mở, chiến lược audio segment, publish durability, rights cho tài sản người dùng sở hữu, tính điểm trend khi thiếu dữ liệu, cùng các lỗi encoding và truy vết manifest.

**Rà soát vòng 3 (4.4):** sửa các mâu thuẫn còn sót giữa codec/encoder, video-only segment và kiểm tra audio; làm rõ `verified`/`completed`; buộc thu hồi duyệt khi invalidation; bổ sung vô hiệu hóa khi quyền sử dụng thay đổi; sửa durability, hủy process tree, verifier và tính nhất quán timeline.

**Rà soát vòng 4 (4.5):** chuẩn hóa trạng thái job và asset, sửa metadata segment video-only, khóa cấu hình theo job, làm rõ CFR/time base, tăng tính nguyên tử của idempotency và queue claim, bổ sung xác minh sau mux, chống cache nhầm môi trường và hoàn thiện release gate.

**Nguyên tắc kiểm tra nhất quán:** mọi đầu ra chỉ được dùng khi đồng thời thỏa schema, hash, probe/validator, dependency validity và review bắt buộc. Kiểm tra tĩnh không thể bảo đảm tuyệt đối “không còn lỗi”; release vẫn phải vượt toàn bộ test và gate ở Mục 40–41.

---

## 1. Mục tiêu và phạm vi phiên bản 4.5

Phiên bản 4.5 có một **luồng Core bắt buộc** và các module độc lập:

1. **Core:** quản lý dự án, audio local, ảnh local, render ảnh phẳng, checkpoint, phục hồi, verify và manifest.
2. **Enhanced:** xử lý vibe, ảnh AI, tách lớp và Parallax 2.5D.
3. **Optional:** Trend Hunter, provider online và tự sáng tác nhạc.
4. Enhanced/Optional không được chặn hoặc làm thất bại luồng Core.

Upload YouTube nằm ngoài phạm vi phiên bản 4.5 cho đến khi toàn bộ luồng tạo nội dung, kiểm duyệt và render hoạt động ổn định.

### 1.1. Nguyên tắc phạm vi

Mỗi tính năng được phân loại để hệ thống luôn có một đường xuất video khả dụng:

- **Core:** bắt buộc để tạo, lưu, render và kiểm tra video.
- **Enhanced:** nâng chất lượng nhưng phải có phương án thay thế.
- **Optional:** có thể không cài hoặc tắt mà không chặn luồng Core.
- **Future:** chưa triển khai trong phiên bản này.

### 1.2. Phân loại tính năng

**Core**
- Quản lý dự án, trạng thái, khóa ghi và phục hồi.
- Nhập audio và ảnh từ máy.
- FFprobe, chuẩn hóa audio, lưu SHA-256 và metadata.
- Ảnh phẳng với zoom/pan nhẹ làm phương án tối thiểu.
- Hàng đợi công việc, render nền, hủy, tiếp tục và checkpoint.
- Render phân đoạn, nối, xác minh và tạo manifest.

**Enhanced**
- Tạo ảnh AI, upscale AI và hệ thống prompt đa dạng.
- Hồ sơ vibe, ambience, EQ nhẹ và loop crossfade.
- Tách lớp, inpainting và Parallax 2.5D.
- Phân tích xu hướng và xếp hạng tiềm năng.

**Optional**
- Stable Diffusion Local do người dùng cài.
- Nguồn tạo ảnh online.
- SAM 2, AI upscale và model nâng cao.
- Tự sáng tác nhạc Lo-Fi.

**Future**
- Upload và lên lịch YouTube.
- Tự động xuất bản không có bước kiểm duyệt của người dùng.

### 1.3. Đường fallback bắt buộc

Nếu các thành phần Enhanced hoặc Optional không hoạt động, hệ thống vẫn phải cho phép:

```text
Audio local hợp lệ + ảnh local hợp lệ
→ chuẩn hóa audio
→ ảnh phẳng với zoom/pan nhẹ
→ render nền
→ verify
→ xuất video và manifest
```

---

## 2. Luồng xử lý thống nhất

### 2.1. Luồng Core bắt buộc

```text
Tạo hoặc mở dự án
→ nhập audio local → FFprobe → rights review → chuẩn hóa
→ nhập ảnh local → kiểm tra → scale đúng profile
→ preview ảnh phẳng → người dùng duyệt
→ render phân đoạn → nối → verify → manifest
```

### 2.2. Luồng nâng cao có điều kiện

```text
Nguồn nhạc hợp lệ hoặc nhạc tự sáng tác
→ phân tích vibe/audio preview
→ chọn hoặc tạo ảnh AI
→ upscale → tách lớp hoặc fallback ảnh phẳng
→ preview Parallax → người dùng duyệt
→ quay về render Core
```

### 2.3. Nhánh Trend Hunter

Trend Hunter chỉ tạo dữ liệu tham khảo. Nhánh này không tự tải audio, không tự thay đổi quyền sử dụng và không phải điều kiện chạy Core.

### 2.4. Quy tắc nhánh

- Optional thiếu capability phải chuyển `skipped`; trạng thái capability của adapter có thể là `disabled`, nhưng đây không phải trạng thái workflow và không làm project `failed`.
- Fallback phải thỏa output contract của bước sau và được ghi trong provenance/manifest.
- Chỉ chạy bước khi input contract hợp lệ.

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
    "trend": "not_started",
    "audio": "approved",
    "image": "approved",
    "layers": "not_started",
    "preview": "not_started",
    "render": "not_started",
    "output": "not_started"
  },
  "trend_context": null,
  "track": {},
  "audio_path": null,
  "image_candidates": [],
  "selected_image": null,
  "layers": null,
  "animation_config": {},
  "render_job": {},
  "final_video": null,
  "created_at_utc": "2026-07-13T21:00:00Z",
  "updated_at_utc": "2026-07-13T21:00:00Z"
}
```

### Quy tắc

- Lưu sau mỗi hành động quan trọng.
- Ghi JSON theo giao thức nguyên tử ở Mục 19.2; chỉ đổi tên trong cùng filesystem sau khi flush, xác minh, fsync file và sau đó fsync thư mục cha khi nền tảng hỗ trợ.
- SQLite là nguồn chuẩn của trạng thái hiện hành; không phụ thuộc trạng thái tạm của Streamlit hoặc `project.json`. SQLite phải bật foreign keys, `busy_timeout`; WAL chỉ bật khi filesystem hỗ trợ khóa tin cậy, nếu không dùng journal mode an toàn tương thích.
- `project.json` là snapshot/export; mở lại ứng dụng phục hồi từ SQLite rồi đối chiếu file và hash. Snapshot phải chứa hoặc tham chiếu revision của `processing_status` và `review_status`; nhãn `workflow_status` không đủ để phục hồi trạng thái nguồn.
- Mỗi dự án chỉ có một writer logic tại một thời điểm. Mọi cập nhật SQLite vẫn phải dùng transaction; lock file đơn lẻ không thay thế ràng buộc/transaction trong database.
- Mỗi bước có `input_hash`; chỉ dùng lại kết quả khi input/config/producer hash không đổi, asset đã verify, output hash khớp và mọi dependency vẫn hợp lệ.

---

## 4. Lõi tra cứu và kiểm duyệt nhạc

**File chính:** `step1_music_hunter.py`

### Đầu vào

- Chọn chủ đề có sẵn.
- Nhập từ khóa.
- Dán URL YouTube hoặc SoundCloud.
- Chọn file MP3, M4A, WAV hoặc FLAC trong máy.

### Quy trình

```text
Nhận yêu cầu
→ Tìm metadata
→ Chuẩn hóa kết quả
→ Loại kết quả trùng
→ Tính điểm phù hợp và điểm tin cậy nguồn
→ Nghe thử
→ Người dùng chọn bài
→ Kiểm tra quyền tải và điều khoản nguồn
→ Chỉ tải bản đầy đủ khi nguồn cho phép và rights review xác nhận `licensed_for_use` hoặc `user_attested_ownership`; đồng thời adapter phải có capability `audio_download` và điều khoản nguồn cho phép thao tác tải. Xác nhận của người dùng không được mở khóa capability bị provider cấm; audit phải lưu actor, thời điểm, phạm vi và bằng chứng/ghi chú
→ Kiểm tra bằng FFprobe
→ Chuẩn hóa audio
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
  "source": "soundcloud",
  "url": "https://...",
  "duration_seconds": 180,
  "license": "unknown",
  "license_url": null,
  "views": 0,
  "likes": 0,
  "relevance_score": 0,
  "source_trust_score": 0,
  "risk_reasons": [],
  "download_status": "not_downloaded"
}
```

### Cải thiện bắt buộc

- Không tự tìm nhạc mỗi lần giao diện tải lại.
- Hủy truy vấn cũ khi người dùng tìm từ khóa mới.
- Cache metadata theo truy vấn và nguồn.
- Chỉ tải bản đầy đủ sau khi người dùng chọn, nguồn/adapter có capability `audio_download`, điều khoản cho phép và rights review xác nhận `licensed_for_use` hoặc `user_attested_ownership`; trạng thái `requires_manual_review`, `trend_reference_only` hoặc `blocked_from_download` đều không được tải để đưa vào video.
- Không tự đoán giấy phép.
- Đổi tên “điểm an toàn bản quyền” thành **điểm tin cậy nguồn**.
- Dùng FFprobe kiểm tra codec, thời lượng và luồng âm thanh.
- Dùng SHA-256, `track_id` và URL để chống trùng.
- Giữ `.part` khi lỗi tạm thời và server hỗ trợ resume; xóa hoặc quarantine khi sai MIME/hash, không hỗ trợ resume hoặc hủy vĩnh viễn.

### Đầu ra

```text
data/input_audio/<track_id>/source.*
data/input_audio/<track_id>/normalized.m4a
data/metadata/tracks/<track_id>.json
data/metadata/previews/<track_id>.m4a
```

### Trend Hunter — phát hiện bài mới và chủ đề có tiềm năng

Trend Hunter là lớp **phân tích xu hướng**, không phải lớp cấp quyền sử dụng nhạc. Một bài có điểm tiềm năng cao vẫn không được tải hoặc đưa vào video nếu quyền sử dụng chưa rõ ràng.

#### Nguồn dữ liệu

**Nguồn chính thức ưu tiên**

- SoundCloud API: chỉ dùng endpoint và trường dữ liệu mà ứng dụng hiện được cấp phép; quyền đọc metadata không đồng nghĩa với quyền tải hoặc tái sử dụng audio.
- YouTube Data API: tìm video và đọc metadata/số liệu công khai trong phạm vi quota, chính sách và trường dữ liệu API thực tế cung cấp.
- Dữ liệu nội bộ: lịch sử các lần quét để tính tốc độ tăng theo thời gian.
- Mọi tích hợp phải đi qua capability registry; khi API, quota, xác thực hoặc điều khoản thay đổi, adapter chuyển `degraded` hoặc `disabled` mà không chặn luồng Core.

**Nguồn tham khảo tùy chọn**

- SoundCloud New & Hot hoặc chart chỉ dùng khi cách truy cập ổn định và phù hợp điều khoản nền tảng.
- Spotify chỉ dùng tham khảo metadata hoặc phát hành mới khi endpoint còn khả dụng; không dùng làm nguồn tải audio.
- Không phụ thuộc vào API không chính thức làm nguồn duy nhất.

#### Phạm vi quét

Người dùng chọn:

- Khu vực: Việt Nam, Hoa Kỳ hoặc toàn cầu.
- Thể loại: Lo-Fi, chill, jazz, ambient, study, sleep hoặc gaming.
- Khoảng thời gian: 24 giờ, 7 ngày hoặc 30 ngày.
- Nguồn: SoundCloud, YouTube hoặc tất cả nguồn hỗ trợ.
- Chế độ: bài mới, tăng nhanh, ổn định hoặc chủ đề đang nổi.

#### Quy trình thu thập

```text
Tạo phiên quét
→ Gọi nguồn dữ liệu theo giới hạn tốc độ
→ Chuẩn hóa metadata
→ Loại kết quả trùng giữa các nguồn
→ Lưu snapshot theo thời gian
→ Tính tốc độ tăng trưởng
→ Phát hiện bất thường
→ Tính điểm tiềm năng
→ Tách riêng trạng thái quyền sử dụng
→ Hiển thị bảng xếp hạng
```

Không tự tải audio trong bước quét xu hướng.

#### Snapshot xu hướng

Mỗi lần quét lưu một bản ghi:

```json
{
  "schema_name": "trend_snapshot",
  "schema_version": 1,
  "source": "youtube",
  "source_id": "abc123",
  "captured_at_utc": "2026-07-13T21:00:00Z",
  "published_at_utc": "2026-07-12T03:00:00Z",
  "views": 12500,
  "likes": 980,
  "comments": 75,
  "chart_position": null,
  "region": "VN",
  "genre": "lofi"
}
```

Cần ít nhất hai snapshot ở hai thời điểm khác nhau mới tính được tốc độ tăng đáng tin cậy. Nếu chỉ có một snapshot, trạng thái là `insufficient_history`.

#### Chỉ số tăng trưởng

Hệ thống tính riêng:

- Lượt xem hoặc lượt nghe tăng mỗi giờ.
- Lượt thích tăng mỗi giờ.
- Bình luận tăng mỗi giờ nếu nguồn có dữ liệu.
- Tỷ lệ tương tác trên tổng lượt xem hoặc lượt nghe.
- Tuổi bài tính từ thời điểm phát hành.
- Thay đổi vị trí chart nếu có.
- Mức phù hợp với chủ đề Lo-Fi.
- Độ tin cậy của dữ liệu nguồn.

Dùng tốc độ tăng theo giờ hoặc ngày, không so trực tiếp tổng lượt xem giữa một bài mới và một bài đã phát hành lâu.

#### Điểm tiềm năng

Điểm tiềm năng nằm trong khoảng 0–100:

```text
30% tốc độ tăng lượt xem hoặc lượt nghe
20% tỷ lệ tương tác
15% độ mới của bài
15% mức phù hợp với Lo-Fi
10% đà tăng vị trí chart
10% độ tin cậy dữ liệu
```

Nếu nguồn không có một chỉ số, hệ thống chỉ phân bổ lại trọng số trong tập chỉ số được cấu hình là có thể thiếu và ghi `missing_metrics`. Nếu tổng trọng số gốc còn khả dụng thấp hơn ngưỡng tối thiểu, kết quả là `insufficient_data`, không tính điểm hoặc gắn nhãn tiềm năng.

#### Phân loại

- **Đang bùng nổ:** tăng nhanh trong 24–72 giờ và tương tác tốt.
- **Có tiềm năng:** còn mới, tốc độ tăng tốt nhưng chưa bão hòa.
- **Ổn định:** tăng đều, không có dấu hiệu bất thường.
- **Đã bão hòa:** tổng lượt xem cao nhưng tốc độ tăng mới thấp.
- **Tăng trưởng bất thường:** tăng lượt xem mạnh nhưng tương tác quá thấp hoặc dữ liệu biến động bất thường.
- **Chưa đủ dữ liệu:** chưa có đủ snapshot để kết luận.

#### Quyền sử dụng tách biệt

Mỗi kết quả có hai nhóm điểm độc lập:

```json
{
  "schema_name": "trend_ranking",
  "schema_version": 1,
  "trend_score": 82,
  "trend_label": "high_potential",
  "source_trust_score": 70,
  "usage_status": "trend_reference_only",
  "license": "unknown",
  "license_url": null
}
```

`trend_score` không làm tăng `source_trust_score` và không thay đổi `usage_status`.

Trạng thái sử dụng:

- `licensed_for_use`: có bằng chứng giấy phép phù hợp.
- `user_attested_ownership`: người dùng xác nhận họ sở hữu hoặc tự tạo tài sản; phải lưu audit record và không tự suy ra từ file local.
- `requires_manual_review`: cần người dùng kiểm tra.
- `trend_reference_only`: chỉ dùng để phân tích vibe và chủ đề.
- `blocked_from_download`: không cho tải trong ứng dụng.

#### Dùng trend an toàn

Với bài không có quyền sử dụng rõ ràng, hệ thống chỉ trích xuất dữ liệu cấp cao:

- Chủ đề chung.
- Mood.
- Khoảng BPM.
- Năng lượng.
- Nhạc cụ phổ biến.
- Màu sắc hình ảnh và bối cảnh đang được quan tâm.

Không lấy melody, vocal, sample, MIDI hoặc bản ghi để tạo nội dung mới.

#### Giao diện Trend Hunter

- Bộ lọc khu vực, thể loại, nguồn và thời gian.
- Danh sách **Mới nổi**, **Tăng nhanh**, **Ổn định** và **Chưa đủ dữ liệu**.
- Biểu đồ tốc độ tăng theo thời gian.
- Hiển thị riêng điểm tiềm năng, điểm tin cậy nguồn và trạng thái sử dụng.
- Nút **Theo dõi bài này**.
- Nút **Dùng vibe để tạo nhạc mới**.
- Nút **Kiểm tra quyền sử dụng**.
- Nút **Ẩn khỏi đề xuất**.

#### Lịch quét và giới hạn

- Quét nhanh thủ công khi người dùng bấm nút.
- Quét nền mặc định mỗi 12 giờ cho danh sách đang theo dõi.
- Không quét lại khi dữ liệu cache còn mới.
- Tuân thủ quota và giới hạn tốc độ của từng API.
- Khi quota hết, giữ dữ liệu cũ và hiển thị thời điểm cập nhật gần nhất.
- Không dùng scraping làm phương án mặc định.

#### Dữ liệu đầu ra

```text
data/trends/snapshots/<source>/<date>.jsonl
data/trends/rankings/<region>/<genre>.json
data/trends/watchlist.json
data/trends/cache_index.json
```

#### Điều kiện đánh dấu bài tiềm năng

- Có ít nhất hai snapshot hợp lệ.
- Không có dấu hiệu tăng trưởng bất thường nghiêm trọng.
- Điểm tiềm năng đạt ngưỡng cấu hình.
- Dữ liệu không quá hạn.
- Nhãn **tiềm năng** không được hiển thị như một bảo đảm thành công.

### Hồ sơ vibe âm nhạc và xử lý âm thanh

Mỗi bài nhạc được phân tích thành một hồ sơ vibe, dùng chung cho prompt ảnh, hiệu ứng và phối âm.

#### Thuộc tính vibe

```json
{
  "schema_name": "vibe_profile",
  "schema_version": 1,
  "vibe_profile": "rainy_focus",
  "energy": 0.35,
  "warmth": 0.45,
  "brightness": 0.3,
  "calmness": 0.85,
  "tempo_bpm": 72,
  "mood_tags": [
    "rainy",
    "focused",
    "late-night"
  ],
  "ambience_profile": "soft_rain_window"
}
```

Các giá trị chỉ hỗ trợ gợi ý, không tự thay đổi mạnh bản nhạc.

#### Hồ sơ âm thanh có sẵn

- **Rainy Focus:** nhạc rõ, mưa cửa sổ nhẹ, không gian tối và tập trung.
- **Coffee Warm:** âm thanh ấm, ambience quán cà phê rất nhỏ.
- **Night Study:** bass gọn, treble dịu, tiếng phòng nhẹ.
- **Sleep Calm:** âm lượng thấp hơn, ít dải cao, fade dài.
- **Jazz Lounge:** giữ độ động tốt hơn, ambience phòng nhỏ.
- **Vinyl Retro:** vinyl crackle rất nhẹ, không che nhạc.
- **Nature Ambient:** mưa rừng, gió hoặc nước chảy ở mức thấp.
- **Clean Original:** không thêm ambience, chỉ chuẩn hóa và fade.

#### Chuẩn hóa chất lượng âm thanh

- Dùng loudness normalization thay vì chỉ tăng giảm volume thủ công.
- Mục tiêu mặc định cho video dài: khoảng `-14 LUFS` đến `-16 LUFS`.
- True peak không vượt khoảng `-1 dBTP`.
- Không để ambience lớn hơn nhạc.
- Kiểm tra clipping, im lặng bất thường và kênh trái/phải lệch mạnh.
- Giữ bản audio gốc; mọi xử lý tạo ra file mới.

#### Phối ambience

Tỉ lệ ban đầu:

```text
Nhạc chính: 100%
- Ambience nhẹ: 6–12%
- Vinyl crackle: 2–5%
- Hiệu ứng điểm nhấn: tối đa 3–6%
```

Tỉ lệ phải được điều chỉnh theo loudness thực tế, không dùng một giá trị cố định cho mọi file.

#### Làm nhạc tự nhiên hơn

- Không mặc định làm chậm mọi bài; chỉ thay đổi tempo khi người dùng chủ động chọn.
- Có ba lựa chọn: giữ nguyên, chậm nhẹ và tùy chỉnh.
- Chậm nhẹ nên ở khoảng 0.94–0.98; chỉ dùng 0.88 khi người dùng chủ động chọn hiệu ứng rõ.
- Reverb phải rất nhẹ và có nút tắt.
- Fade đầu 2–5 giây và fade cuối 5–10 giây.
- Khi loop audio, dùng crossfade để tránh tiếng bật hoặc điểm nối rõ.
- Không thêm mưa vào mọi chủ đề.

#### Loop audio mượt

```text
Phân tích điểm đầu và cuối
→ chọn vùng nối ít chênh lệch
→ crossfade 3–8 giây
→ kiểm tra loudness tại điểm nối
→ tạo preview loop 30 giây
→ người dùng duyệt
```

Video 1 giờ không nên chỉ lặp thô một file ngắn. Có thể thay đổi nhẹ ambience giữa các đoạn nhưng nhạc chính phải liên tục.

#### Ba bản nghe thử

Trước render, tạo ba preview 30 giây:

1. **Gốc sạch:** chỉ chuẩn hóa loudness.
2. **Vibe nhẹ:** ambience và EQ nhẹ.
3. **Vibe rõ:** ambience, màu âm và không gian rõ hơn nhưng không che nhạc.

Người dùng chọn một bản hoặc tự chỉnh thanh âm lượng nhạc, ambience và reverb.

#### Metadata xử lý audio

```json
{
  "schema_name": "audio_processing_metadata",
  "schema_version": 1,
  "audio_profile": "rainy_focus",
  "target_lufs": -15.0,
  "true_peak_limit": -1.0,
  "tempo_mode": "original",
  "tempo_rate": 1.0,
  "ambience": "soft_rain_window.wav",
  "ambience_gain_db": -22.0,
  "reverb_enabled": false,
  "fade_in_seconds": 3,
  "fade_out_seconds": 8,
  "loop_crossfade_seconds": 5
}
```

#### Điều kiện duyệt âm thanh

- Không clipping.
- Không rè hoặc méo tiếng.
- Ambience không che giai điệu.
- Điểm loop khó nhận ra khi nghe bình thường.
- Loudness ổn định giữa các đoạn.
- Preview đã được người dùng chọn trước khi render dài.



### Tự sáng tác nhạc Lo-Fi mới từ vibe

Chế độ này tạo nhạc mới hoàn toàn, không lấy melody, vocal, sample, MIDI hoặc bản ghi từ bài đang trend.

#### Đầu vào sáng tác

- Vibe: rainy focus, coffee warm, night study, sleep calm, jazz lounge, vinyl retro hoặc nature ambient.
- BPM: mặc định trong khoảng 60–90.
- Tông nhạc và thang âm.
- Nhạc cụ chính: electric piano, piano, guitar sạch, pad hoặc bell mềm.
- Mức năng lượng.
- Độ phức tạp của chord, melody và drum.
- Thời lượng bài gốc trước khi kéo dài.

#### Quy trình sáng tác

```text
Chọn vibe và BPM
→ Tạo vòng hợp âm mới
→ Tạo melody mới theo hợp âm
→ Tạo bass mới
→ Tạo beat mới
→ Chọn nhạc cụ và SoundFont
→ Render MIDI thành audio
→ Mix ambience nhẹ
→ Chuẩn hóa loudness
→ Tạo ba preview 30 giây
→ Người dùng chọn
→ Tạo bản đầy đủ và loop mượt
```

#### Quy tắc chống sao chép

- Không nhận file bài nổi tiếng làm mẫu melody.
- Không trích MIDI, vocal hoặc sample từ bài có bản quyền.
- Trend chỉ cung cấp mood, khoảng BPM, năng lượng, nhạc cụ phổ biến và chủ đề chung.
- Mỗi melody có `melody_hash`; nếu quá giống bài nội bộ đã tạo thì sinh lại.
- Lưu seed, chord progression, melody events, drum pattern và SoundFont để tái tạo.
- Sample và SoundFont phải có nguồn, giấy phép và trạng thái sử dụng rõ ràng.

#### Cấu trúc bài gợi ý

```text
Intro 8 bars
→ Section A 16 bars
→ Section B 16 bars
→ Variation A 16 bars
→ Breakdown 8 bars
→ Outro 8 bars
```

Các đoạn phải có thay đổi melody, drum, bass hoặc nhạc cụ; không lặp nguyên một loop quá lâu.

#### Dữ liệu dự án sáng tác

```json
{
  "schema_name": "composition_metadata",
  "schema_version": 1,
  "composition_mode": "original_lofi",
  "composer_seed": 12345,
  "vibe_profile": "rainy_focus",
  "bpm": 72,
  "key": "A minor",
  "chord_progression": [
    "Am7",
    "Dm7",
    "G7",
    "Cmaj7"
  ],
  "instrument_profile": "electric_piano_soft",
  "soundfont_path": "data/soundfonts/approved.sf2",
  "melody_hash": "...",
  "license_status": "original_generated"
}
```

#### File lõi

```text
core/music_composer.py
core/midi_renderer.py
core/audio_mixer.py
core/internal_melody_similarity.py
```

### Xu hướng theo chủ đề

Trend Hunter theo dõi cả bài nhạc và cụm chủ đề:

- Tần suất xuất hiện của từ khóa.
- Tốc độ tăng của video hoặc track chứa từ khóa.
- Khu vực và thể loại.
- Mood, BPM ước tính, nhạc cụ và bối cảnh hình ảnh phổ biến.
- Thời gian chủ đề bắt đầu tăng và thời điểm cập nhật gần nhất.

Chủ đề chỉ được dùng để tạo nhạc và hình ảnh mới; không dùng để sao chép nhận diện của một nghệ sĩ hoặc bìa nhạc cụ thể.


---

## 5. Lõi tạo hình ảnh

**File chính:** `step2_image_provider.py`

### Nguồn ảnh

- **Ảnh tải từ máy:** nguồn Core luôn được hỗ trợ.
- **Stable Diffusion Local:** Optional, giao tiếp qua adapter phiên bản hóa.
- **Provider online:** Optional; Pollinations, AI Horde, Hugging Face hoặc nguồn khác chỉ được bật khi adapter vượt qua capability check, điều khoản sử dụng phù hợp và giới hạn dịch vụ còn hợp lệ.

Không coi tên nhà cung cấp là hợp đồng API cố định. Mỗi provider phải khai báo capability, phiên bản adapter, phương thức xác thực, giới hạn, chính sách dữ liệu, trạng thái sức khỏe và thời điểm kiểm tra gần nhất.

### Quy trình tạo ảnh

```text
Phân tích nhạc và chủ đề
→ Tạo prompt có cấu trúc
→ Tạo lần lượt 3 ảnh bằng seed khác nhau
→ Kiểm tra file ảnh
→ Người dùng chọn ảnh
→ Upscale lên 1920×1080
→ Lưu ảnh gốc, ảnh Full HD và metadata
```

Không tạo ba ảnh đồng thời để tránh giới hạn dịch vụ và quá tải GPU.

### Kích thước thống nhất

- Kích thước tạo ưu tiên: **960×540**.
- Khi thiếu VRAM: **768×432**.
- Kích thước cuối: **1920×1080**.
- Không dùng 1280×720 làm đầu ra cuối.
- Không tạo trực tiếp 1920×1080 bằng SD 1.5 trên GPU 4GB.

### Upscale

- Ảnh 960×540 có thể upscale 2× tới 1920×1080.
- Ảnh 768×432 phải scale trực tiếp tới 1920×1080, tương đương 2,5×; scale 2× chỉ tạo 1536×864 và không hợp lệ.
- Ưu tiên AI upscale khi hỗ trợ đúng kích thước đích; fallback Lanczos phải đặt đích 1920×1080.
- Không dùng Hires Fix mặc định.
- Giữ ảnh gốc và ảnh Full HD; metadata lưu kích thước nguồn/đích, thuật toán và hệ số scale.

### Yêu cầu ảnh dành cho Parallax

- Chủ thể không nằm sát mép.
- Có vùng dư ít nhất 5% để zoom và pan.
- Bố cục rõ tiền cảnh, trung cảnh và hậu cảnh.
- Hạn chế chữ, logo và watermark.
- Cửa sổ, đèn, màn hình hoặc hơi nước phải có vùng rõ nếu muốn animation theo vùng.

### Metadata ảnh

```json
{
  "schema_name": "image_metadata",
  "schema_version": 1,
  "provider": "sd_local",
  "model": "model.safetensors",
  "prompt": "...",
  "negative_prompt": "...",
  "seed": 12345,
  "source_size": "960x540",
  "final_size": "1920x1080",
  "upscale_method": "ai_2x",
  "source_path": "...",
  "full_hd_path": "..."
}
```

### Hệ thống prompt đa dạng

Prompt không được tạo chỉ từ tên bài nhạc. Hệ thống kết hợp nhiều nhóm thuộc tính:

```text
Chủ đề + bối cảnh + thời gian + thời tiết + góc máy
+ chủ thể + đạo cụ + ánh sáng + bảng màu + phong cách
+ bố cục Parallax + mức chi tiết + điều kiện loại trừ
```

#### Hồ sơ phong cách

- **Lo-Fi Anime:** minh họa anime mềm, ánh sáng điện ảnh, đường nét sạch.
- **Cozy Interior:** phòng ngủ, bàn học, quán cà phê, vật liệu gỗ và ánh đèn ấm.
- **Urban Night:** phố đêm, biển hiệu, mặt đường phản chiếu, mưa nhẹ.
- **Nature Escape:** rừng, hồ, núi, bầu trời, cabin và ánh sáng tự nhiên.
- **Retro Analog:** màu phim cũ, hạt ảnh, đèn neon, đồ vật cổ điển.
- **Minimal Calm:** ít vật thể, nhiều khoảng thở, màu dịu và bố cục sạch.
- **Dreamy Ambient:** mây, sao, sương, ánh sáng mềm và không gian siêu thực nhẹ.

#### Ma trận biến thể

Mỗi ảnh ứng viên phải khác ít nhất 3 thuộc tính:

- Bối cảnh.
- Thời gian trong ngày.
- Góc máy.
- Bảng màu.
- Thời tiết.
- Đạo cụ.
- Mức hiện diện của chủ thể.
- Phong cách hình ảnh.

Không chỉ thay seed trong khi giữ nguyên toàn bộ prompt.

#### Quy tắc tránh lặp

- Lưu `prompt_hash` và các thuộc tính prompt đã dùng.
- Không dùng lại cùng tổ hợp bối cảnh, góc máy và bảng màu trong các dự án gần nhất.
- Có nút **Đổi bối cảnh**, **Đổi màu**, **Đổi góc máy** và **Tạo biến thể gần giống**.
- Tạo ba ảnh theo ba hướng: an toàn, sáng tạo và tối giản.
- Người dùng có thể khóa thuộc tính muốn giữ trước khi tạo lại.

#### Prompt dành cho tách lớp

Prompt phải chủ động tạo bố cục dễ tách:

- Một vật thể tiền cảnh rõ ràng.
- Chủ thể chính nằm ở trung cảnh.
- Hậu cảnh ít bị che kín.
- Ranh giới vật thể rõ nhưng không có viền giả.
- Chừa vùng dư 5–8% quanh khung hình.
- Không để tóc, cây hoặc vật thể nhỏ phủ kín toàn bộ hậu cảnh.

Cụm gợi ý bổ sung:

```text
clear foreground, distinct midground, complete background,
layered depth composition, clean object separation,
wide cinematic framing, extra border space for parallax motion
```

#### Negative prompt chung

```text
text, logo, watermark, signature, deformed anatomy,
extra limbs, duplicate objects, cropped subject, cluttered composition,
flat depth, merged objects, harsh outlines, low resolution,
oversaturated colors, blown highlights, crushed shadows
```

Negative prompt được điều chỉnh theo model; không ép cùng một negative prompt cho mọi checkpoint.

#### Cấu trúc dữ liệu prompt

```json
{
  "schema_name": "prompt_metadata",
  "schema_version": 1,
  "prompt_version": 1,
  "profile": "lofi_anime",
  "scene": "rainy bedroom",
  "time_of_day": "late night",
  "weather": "soft rain",
  "camera": "wide eye-level shot",
  "subject": "quiet study desk",
  "props": [
    "headphones",
    "notebook",
    "warm lamp"
  ],
  "lighting": "warm lamp and cool window light",
  "palette": [
    "navy",
    "violet",
    "amber"
  ],
  "parallax_ready": true,
  "variation_mode": "creative",
  "prompt_hash": "..."
}
```

#### Liên kết prompt với vibe nhạc

- Nhạc êm và chậm: ánh sáng mềm, ít vật thể, màu lạnh hoặc trung tính.
- Nhạc ấm và vui: ánh sáng vàng, vật liệu gỗ, màu kem và cam nhạt.
- Nhạc mưa hoặc buồn: xanh navy, tím, phản chiếu và cửa sổ mưa.
- Nhạc jazz: quán cà phê, đèn tungsten, màu nâu và đỏ trầm.
- Nhạc ambient: không gian rộng, sương, mây, sao và chuyển màu nhẹ.
- Nhạc gaming: màn hình, ánh sáng RGB dịu, góc bàn rõ và không quá nhiều neon.

Gợi ý chỉ tạo prompt nháp. Người dùng vẫn duyệt trước khi tạo ảnh.


---

## 6. Stable Diffusion Local

### Cấu hình khuyến nghị

- AUTOMATIC1111.
- Model nền SD 1.5.
- Batch size: 1.
- Steps: 20–25.
- Sampler: DPM++ 2M Karras hoặc Euler a.
- Khởi động bằng `--api --medvram`.
- Nếu thiếu VRAM, dùng `--api --lowvram`.
- Không chạy Stable Diffusion cùng lúc với render NVENC.

### Cài theo thư mục người dùng chọn

Nút **Cài Stable Diffusion** mở hộp thoại chọn thư mục. Ví dụ:

```text
D:\AI\LofiStudioAI
```

Cấu trúc:

```text
D:\AI\LofiStudioAI├── stable-diffusion-webui├── models│   ├── checkpoints│   ├── vae│   └── lora├── outputs│   ├── original│   ├── full_hd│   └── previews├── cache├── temp├── logs└── install_state.json
```

Đường dẫn được lưu tại:

```text
config/local_ai.json
```

Ứng dụng phải có các nút:

- Cài Stable Diffusion.
- Dùng bản đã có.
- Đổi thư mục.
- Mở thư mục.
- Khởi động.
- Dừng.
- Kiểm tra API.
- Tiếp tục cài đặt bị gián đoạn.

### Chọn model

Nhóm lựa chọn:

- Lo-Fi Anime.
- Anime phổ thông.
- Phong cảnh và nội thất.
- Chân thực.
- Tự chọn file `.safetensors`.

Với RTX 3050 Ti 4GB:

- Chỉ đánh dấu **Khuyến nghị** cho model SD 1.5.
- Không đặt SDXL, Flux hoặc model video làm mặc định.
- Không nạp nhiều checkpoint cùng lúc.
- Không tự tải model khi người dùng chưa đồng ý.
- Lưu URL nguồn, giấy phép và SHA-256 của model.


### Quản lý tải model

- Chỉ tải model sau khi người dùng chọn và đồng ý.
- Trước khi tải, hiển thị tên file, dung lượng dự kiến, nguồn và giấy phép.
- Tải vào file `.part`; chỉ đổi sang `.safetensors` sau khi tải hoàn tất.
- Nếu nhà phát hành có SHA-256 thì phải kiểm tra; nếu không có thì tự tạo hash sau tải.
- Không nạp model nếu file thiếu, sai hash hoặc không đọc được.
- Lưu metadata model trong `models/index.json`.
- Có thể tiếp tục tải bị gián đoạn nếu máy chủ hỗ trợ.
- Không tự cập nhật hoặc thay model giữa lúc dự án đang chạy.


### API

```text
GET  http://127.0.0.1:7860/sdapi/v1/options
POST http://127.0.0.1:7860/sdapi/v1/txt2img
```

Không bật `--listen` nếu chỉ dùng trên một máy.

---

## 7. Tách ảnh tự động và Parallax 2.5D

### Nút Tách ảnh tự động

Nút chỉ bật khi:

- Đã chọn ảnh Full HD.
- Không có job GPU khác đang chạy.
- Thư mục dự án có quyền ghi.
- Có đủ dung lượng trống.

Khi bấm:

```text
queued
→ estimating_depth
→ segmenting
→ refining_masks
→ waiting_for_mask_review
→ inpainting_background
→ waiting_for_background_review
→ building_preview
→ waiting_for_preview_review
→ approved
```

### Công cụ

- Mô hình ước lượng độ sâu để xác định gần và xa.
- SAM hoặc SAM 2 để tạo mask vật thể.
- Inpainting SD 1.5 để điền phần hậu cảnh bị che.
- Công cụ tô và xóa mask thủ công.

Các công cụ này dùng môi trường riêng, không cài chung với ứng dụng Lo-Fi.

### Ba lớp mặc định

- **Hậu cảnh:** tường, bầu trời, thành phố và cảnh xa.
- **Trung cảnh:** chủ thể chính, ghế, cửa sổ và nội thất.
- **Tiền cảnh:** bàn, cây, ly, rèm và vật thể gần máy quay.

Mặc định chỉ dùng 3 lớp. Nếu tự động tách lỗi, người dùng có thể chuyển về 2 lớp hoặc ảnh phẳng.

### Mask Full HD

- Có thể dự đoán mask ở 960×540 hoặc 768×432 để giảm tải.
- Sau khi upscale mask, phải tinh chỉnh cạnh ở 1920×1080.
- Làm mềm cạnh nhẹ, co hoặc giãn mask khi cần.
- Không dùng mask bị răng cưa trực tiếp cho render Full HD.

### Điền hậu cảnh

1. Mở rộng mask quanh vật thể.
2. Dùng inpainting điền vùng bị che.
3. Kiểm tra vùng lặp, vùng đen và chi tiết bất thường.
4. Nếu inpainting lỗi, dùng phương án làm mờ hoặc nhân bản vùng lân cận.
5. Người dùng phải duyệt hậu cảnh trước khi tạo preview.

### Đầu ra

```text
data/projects/<project_id>/layers/
├── source_full_hd.png
├── depth_map.png
├── foreground_mask.png
├── midground_mask.png
├── background_mask.png
├── foreground.png
├── midground.png
├── background_filled.png
├── layer_preview.mp4
└── layers.json
```


### Cache tách lớp

- Tạo `layer_input_hash` từ ảnh Full HD, model độ sâu, model segmentation và tham số mask.
- Nếu hash không đổi và các file lớp còn hợp lệ thì dùng lại kết quả.
- Chỉnh mask chỉ làm mất hiệu lực bước điền hậu cảnh và preview, không chạy lại tạo ảnh.
- Đổi ảnh nguồn làm mất hiệu lực toàn bộ cache tách lớp.
- Cache đã được người dùng duyệt không tự xóa theo thời hạn thông thường.


### Chuyển động mặc định

- Hậu cảnh: dịch 2–5 pixel.
- Trung cảnh: dịch 5–12 pixel.
- Tiền cảnh: dịch 8–20 pixel.
- Chu kỳ: 30–45 giây.
- Chuyển động phải là hàm tuần hoàn và trở về đúng trạng thái ban đầu tại biên chu kỳ; preview kiểm tra sai lệch biên theo ngưỡng cấu hình.

### time_offset cho render phân đoạn

Mỗi đoạn phải nhận thời điểm bắt đầu tuyệt đối theo PTS/time base, không lấy số giây float làm khóa timeline:

```json
{
  "schema_name": "animation_segment",
  "schema_version": 1,
  "segment_index": 2,
  "segment_start_pts": 28800,
  "video_time_base": "1/24",
  "animation_time_offset_pts": 28800
}
```

Không được khởi động lại animation từ giây 0 ở từng đoạn.

---

## 8. Hiệu ứng chuyển động và môi trường

### Lớp dựng hình

```text
Hậu cảnh đã điền
→ hiệu ứng xa
→ trung cảnh
→ hiệu ứng ánh sáng
→ tiền cảnh
→ hiệu ứng gần
→ film grain và vignette
```

### Quy tắc

- Preview và render chính dùng chung một filter builder.
- Tối đa hai video hiệu ứng trên GPU 4GB.
- Mỗi hiệu ứng có blend mode, opacity, tốc độ và start offset riêng.
- Bỏ audio bên trong file hiệu ứng.
- Lưu seed để tái tạo đúng preview đã duyệt.
- Nếu Parallax lỗi, quay về ảnh phẳng với zoom và pan nhẹ.

### Preview bắt buộc

Preview cuối dài 10 giây và kiểm tra:

- Không có viền đen.
- Không có mép PNG trong suốt.
- Không có viền sáng quanh mask.
- Không nhấp nháy mạnh.
- Không che chủ thể quá mức.
- Không có vùng nền chưa điền.
- Màu, FPS và kích thước giống render chính.

---

## 9. Hàng đợi GPU chung

Tất cả tác vụ dùng GPU phải đi qua một hàng đợi:

- Stable Diffusion tạo ảnh.
- AI upscale.
- Ước lượng độ sâu.
- SAM hoặc SAM 2.
- Inpainting.
- Preview Parallax.
- Render NVENC.

### Quy tắc cho GPU 4GB

- Chỉ một tác vụ GPU nặng chạy tại một thời điểm.
- Tác vụ mới chuyển sang `queued_waiting_resource`.
- Giải phóng model trước khi chuyển sang tác vụ kế tiếp.
- Không tạo ảnh lúc NVENC đang render.
- Không render NVENC lúc bất kỳ job `gpu_heavy` nào đang giữ GPU. Việc nhường GPU chỉ diễn ra tại ranh giới an toàn giữa các segment, không tạm dừng giữa một lệnh FFmpeg.
- Nếu thiếu VRAM, chuyển phần tách lớp sang CPU hoặc dùng model nhẹ hơn.

---

## 10. Render chạy nền

**File chính:** `step4_render.py`

Giao diện không chạy FFmpeg trực tiếp.

```text
Giao diện tạo job JSON
→ Render worker nhận job
→ Cập nhật progress JSON
→ Giao diện đọc tiến độ
```

### Trạng thái

Các tên dưới đây là `step` chi tiết của render, không thay thế trạng thái chuẩn tại Mục 18. Trạng thái chuẩn nằm trong `job_status`; bước chi tiết nằm trong `current_step`.

```text
status: queued | queued_waiting_resource | running | verifying | verified | completed | failed | cancelling | cancelled | interrupted | recovering | paused_resource_limit
current_step: preparing | rendering_audio | rendering_segments | joining_segments | verifying_output

`paused_resource_limit` chỉ hợp lệ sau khi worker đã tạo checkpoint nhất quán và giải phóng tài nguyên. Nếu chưa bắt đầu thì dùng `queued_waiting_resource`; nếu đang chạy nhưng không thể checkpoint an toàn thì chuyển `interrupted`, không giả vờ đã pause.
```

### Render Full HD

- Độ phân giải: 1920×1080.
- Frame rate: CFR `24/1`. Không chấp nhận VFR cho profile mặc định; FFprobe phải kiểm tra `avg_frame_rate`, `r_frame_rate`, time base và timestamp monotonic.
- Video codec: `h264`.
- Encoder ưu tiên: `h264_nvenc`.
- Encoder fallback: `libx264`.
- Audio: AAC, 48 kHz.
- Video từ 30 phút trở lên mặc định render theo đoạn.


### Ước lượng dung lượng trước render

Trước khi tạo job, hệ thống ước tính:

```text
Dung lượng video cuối
- Tổng dung lượng các phân đoạn
- Audio trung gian
- File tạm
- 20% vùng an toàn
```

- Nếu ổ đĩa không đủ, không bắt đầu render.
- Hiển thị dung lượng cần, dung lượng còn trống và thư mục đang dùng.
- Có nút đổi thư mục tạm trước khi render.
- Sau khi nối và verify thành công mới xóa phân đoạn nếu người dùng cho phép.


### Render phân đoạn

- Mỗi đoạn 5 hoặc 10 phút.
- Các video segment dùng cùng codec, encoder profile, FPS, kích thước, pixel format, time base và color metadata. Mặc định segment không chứa audio; audio master được mux một lần sau khi nối video.
- Mỗi đoạn có SHA-256 và kết quả FFprobe.
- Đoạn đạt yêu cầu được giữ lại.
- Khi lỗi chỉ render lại đoạn lỗi.
- Sau khi nối phải kiểm tra tính liên tục tại điểm nối.

### Kiểm tra điểm nối

- So sánh frame quanh biên bằng timestamp, SSIM/histogram và chuyển động dự kiến; không yêu cầu hai frame kề nhau có hash giống nhau. Frame hash chỉ kiểm tra toàn vẹn/tái sử dụng đúng file.
- Vì đường mặc định dùng audio master duy nhất, kiểm tra timestamp và độ dài audio sau mux; chỉ kiểm tra gap/overlap tại biên segment nếu chế độ audio-per-segment được bật qua capability/test gate.
- Kiểm tra animation dùng đúng `animation_time_offset_pts` và `video_time_base`.
- Nếu nối trực tiếp thất bại mới encode lại.

### Tiến độ

Dùng `ffmpeg -progress`, không phân tích chuỗi log thông thường.

Log ghi ra:

```text
data/logs/<project_id>/<job_id>.log
```

Giao diện chỉ đọc 20–50 dòng cuối.

### Hủy và khôi phục

- Nút hủy gửi yêu cầu tới đúng process group/Windows Job Object của job; xác minh owner, PID và thời điểm bắt đầu, dừng mềm theo grace period rồi mới buộc dừng toàn bộ process tree.
- Giữ các đoạn đã kiểm tra đạt.
- Khi mở lại ứng dụng, kiểm tra PID và checkpoint.
- File trung gian chỉ được publish sau verify của chính asset. Video cuối phải được verify lại **sau join và mux audio**, rồi mới publish theo giao thức Mục 34; không coi segment verified là bằng chứng video cuối đạt.

---

## 11. Kiểm tra đầu ra

Dùng FFprobe cho metadata/stream/timestamp và chạy FFmpeg decode/filter kiểm tra nội dung:

- Có luồng hình và tiếng.
- Độ phân giải 1920×1080.
- FPS đúng.
- FFprobe xác nhận `video_codec` thực tế thuộc profile (ví dụ `h264`). Encoder thực tế (`h264_nvenc` hoặc `libx264`) lấy từ job/worker metadata, không suy ra từ FFprobe; nếu khác encoder yêu cầu phải có fallback reason hợp lệ.
- Thời lượng được tính từ timestamp/frame-count và audio sample-count, không chỉ đọc container duration; sai số theo RND-ACC-004.
- Không có đoạn đen dài ở đầu hoặc điểm nối theo bộ lọc/threshold cấu hình; FFprobe đơn lẻ không đủ để kết luận tiêu chí này.
- Dung lượng chỉ là sanity check `[WARN]`, không dùng một mình để pass/fail output.

Sau đó tạo manifest:

```json
{
  "schema_name": "output_manifest",
  "schema_version": 1,
  "project_id": "lofi_20260714_001",
  "video_path": "data/output_final/lofi_20260714_001.mp4",
  "video_sha256": "...",
  "duration_seconds": 3600,
  "resolution": "1920x1080",
  "fps": {"numerator": 24, "denominator": 1},
  "video_codec": "h264",
  "requested_video_encoder": "h264_nvenc",
  "actual_video_encoder": "libx264",
  "video_encoder_fallback_reason": "nvenc_unavailable",
  "requested_layer_mode": "three_layer",
  "actual_layer_mode": "flat_image",
  "audio_codec": "aac",
  "audio_sample_rate_hz": 48000,
  "pixel_format": "yuv420p",
  "color_metadata": {"primaries": "bt709", "transfer": "bt709", "space": "bt709", "range": "tv"},
  "track_id": "...",
  "image_seed": 12345,
  "animation_seed": 67890,
  "app_version": "4.5.0",
  "ffmpeg_version": "...",
  "filter_builder_version": "...",
  "config_hash": "...",
  "input_hash": "...",
  "producer_version": "4.5.0",
  "verification_report_path": "...",
  "rights_review_ids": ["..."],
  "rendered_at_utc": "2026-07-13T21:00:00Z"
}
```

---

## 12. Cache, dung lượng và dọn dữ liệu

### Ngân sách dung lượng đề xuất

- Stable Diffusion và môi trường: 10–20GB.
- Model: tùy số lượng, thường 2–7GB mỗi checkpoint.
- Cache và file tạm: giới hạn cấu hình mặc định 20GB.
- Dự án đang làm: không tự xóa.
- Nên dành tổng cộng 50–100GB nếu lưu nhiều model và dự án.

### Chỉ tự xóa

- File `.part` và `.tmp` cũ không thuộc job đang chạy.
- Preview đã hết hạn.
- Log cũ vượt giới hạn.
- Ảnh ứng viên chưa chọn, không có lock, không còn project/job tham chiếu và đã qua thời gian giữ tối thiểu theo cấu hình.

### Không tự xóa

- Audio đã chọn.
- Ảnh đã duyệt.
- Layer đã duyệt.
- Video hoàn chỉnh.
- Metadata, credit và manifest.

---

## 13. Cấu trúc code đề xuất

```text
lofi_automation/
├── config.py
├── main.py
├── step1_music_hunter.py
├── step2_image_provider.py
├── step3_review_app.py
├── step4_render.py
├── step5_uploader.py          # Future, không thuộc bản 4.5
├── system_check.py
├── core/
│   ├── project_manager.py
│   ├── trend_collector.py
│   ├── trend_analyzer.py
│   ├── rights_checker.py
│   ├── music_composer.py
│   ├── midi_renderer.py
│   ├── audio_mixer.py
│   ├── internal_melody_similarity.py
│   ├── media_probe.py
│   ├── cache_manager.py
│   ├── resource_scheduler.py
│   ├── image_upscaler.py
│   ├── layer_separator.py
│   ├── mask_editor.py
│   ├── background_inpainter.py
│   ├── animation_builder.py
│   ├── render_queue.py
│   ├── render_worker.py
│   └── output_verifier.py
├── utils/
│   ├── metadata_store.py
│   └── retry_helper.py
└── data/
```

`step3_review_app.py` chỉ điều khiển và hiển thị. Logic nặng nằm trong `core`.

---

## 14. Kiểm thử lõi bắt buộc

### Nhạc và Trend Hunter

- API mất kết nối, hết quota và dữ liệu cache hết hạn.
- Chỉ có một snapshot nên chưa đủ dữ liệu xếp hạng.
- Số liệu tăng trưởng bất thường.
- File nhạc hỏng, tải dở hoặc không có audio stream.
- Chế độ tự sáng tác tạo melody trùng nội bộ và phải sinh lại.
- SoundFont hoặc sample thiếu thông tin giấy phép.

### Hình ảnh và Stable Diffusion

- API SD Local chưa chạy.
- Thiếu VRAM ở 960×540 và tự giảm xuống 768×432.
- Model tải dở, sai hash hoặc thiếu giấy phép.
- Upscale AI lỗi và chuyển sang Lanczos.
- Prompt ứng viên bị lặp quá nhiều thuộc tính.

### Tách lớp và Parallax

- Depth hoặc SAM thất bại.
- Mask răng cưa sau upscale.
- Inpainting để lại vùng đen hoặc chi tiết lặp.
- Fallback từ 3 lớp xuống 2 lớp rồi ảnh phẳng.
- Cache tách lớp được dùng lại đúng khi ảnh không đổi.

### Render

- NVENC lỗi và chuyển sang libx264.
- Ổ đĩa không đủ trước render.
- Người dùng hủy giữa một phân đoạn.
- Worker chết và khôi phục từ checkpoint.
- Điểm nối video hoặc audio không liên tục.
- `animation_time_offset_pts` giữ Parallax liên tục giữa các đoạn.

Chỉ đánh dấu một giai đoạn hoàn thành khi toàn bộ kiểm thử bắt buộc của giai đoạn đó đạt.


---

## 15. Thứ tự triển khai duy nhất

1. Schema, config, error model và migration.
2. SQLite, project manager, asset identity và atomic writer.
3. Lock, lease, heartbeat, idempotency và recovery.
4. Media probe, asset store, hashing và provenance graph.
5. Scheduler, queue, worker, cancellation và process-tree control.
6. Core: audio local + ảnh local + preview ảnh phẳng.
7. Segment, audio timeline, join, verify và manifest.
8. Ảnh AI/provider adapter.
9. Audio vibe/ambience/loop.
10. Tách lớp/inpainting/Parallax.
11. Trend Hunter.
12. Sáng tác nhạc và tương đồng nội bộ.

Mục 15 là nguồn chuẩn duy nhất. Không làm bước 8–12 trước khi Core và recovery test đạt.

---

## 16. Tiêu chí hoàn thành lõi

- Tìm nhạc không tự chạy lại khi giao diện reload.
- Trend Hunter có snapshot, cache, quota và thời điểm cập nhật rõ ràng.
- Điểm tiềm năng tách biệt hoàn toàn với quyền sử dụng.
- Audio có metadata, credit, SHA-256 và kiểm tra FFprobe.
- Audio có hồ sơ vibe, loudness, ambience và loop đã duyệt.
- Chế độ sáng tác mới lưu chord, melody, beat, seed và giấy phép tài nguyên âm thanh.
- Ảnh có thể tái tạo bằng model, prompt và seed.
- Ba ảnh ứng viên khác nhau về ít nhất ba thuộc tính hình ảnh.
- Ảnh cuối là 1920×1080.
- Nút Tách ảnh tự động có tiến độ và fallback.
- Mask được tinh chỉnh ở Full HD.
- Preview giống render chính.
- Mọi tác vụ GPU dùng chung một hàng đợi.
- Render chạy nền, có hủy và khôi phục.
- Parallax liên tục giữa các đoạn.
- Video chỉ hoàn thành sau khi verify đạt.

---

## 17. Thuật ngữ và quy ước

- **Asset:** file nguồn hoặc file trung gian thuộc dự án.
- **Job:** một công việc có trạng thái, đầu vào, đầu ra và log riêng.
- **Worker:** tiến trình nền nhận và xử lý job.
- **Segment:** một đoạn video được render độc lập.
- **Checkpoint:** điểm lưu cho phép tiếp tục sau khi gián đoạn.
- **Cache:** kết quả có thể tái sử dụng khi đầu vào và phiên bản xử lý không đổi.
- **Snapshot:** bản ghi dữ liệu xu hướng tại một thời điểm.
- **Preview:** đầu ra ngắn để người dùng duyệt trước khi xử lý dài.
- **Approved:** đã được người dùng duyệt.
- **Verified:** đã vượt qua kiểm tra tự động.
- **Invalidated:** kết quả cũ không còn hợp lệ do đầu vào hoặc cấu hình thay đổi.
- **Manifest:** bản mô tả đầu ra cuối và thông tin truy vết.
- **Requested value:** giá trị người dùng hoặc hệ thống yêu cầu.
- **Actual value:** giá trị thực tế đã sử dụng sau fallback.

### Quy ước chung

- Enum, khóa JSON, mã lỗi và trạng thái dùng tiếng Anh dạng `snake_case`.
- Nội dung hiển thị cho người dùng dùng tiếng Việt.
- Thời gian lưu bằng UTC theo ISO 8601, ví dụ `2026-07-14T21:00:00Z`.
- Thời lượng dùng giây; loudness dùng LUFS; true peak dùng dBTP; kích thước file dùng byte.
- Đường dẫn asset trong metadata là đường dẫn tương đối chuẩn hóa bằng dấu `/` tính từ data root. Thư mục ngoài chỉ lưu trong cấu hình cục bộ, không đưa đường dẫn tuyệt đối máy người dùng vào manifest/export công khai.
- ID nội bộ cho project/job/asset/event/lock dùng UUID. Chuỗi chữ thường, số và gạch dưới chỉ dùng cho slug hiển thị hoặc mã nguồn bên ngoài đã namespace, không thay thế UUID nội bộ.

---

## 18. Mô hình trạng thái thống nhất

### 18.1. Trạng thái workflow theo module

Mỗi module có `processing_status` cho kỹ thuật và `review_status` cho quyết định người dùng. `workflow_status` chỉ là snapshot tổng hợp, được suy ra từ hai trạng thái nguồn và không được cập nhật độc lập.

```json
{
  "schema_name": "workflow_status_snapshot",
  "schema_version": 1,
  "derived_from_database_revision": 105,
  "workflow_status": {
    "trend": "not_started",
    "audio": "approved",
    "image": "approved",
    "layers": "not_started",
    "preview": "not_started",
    "render": "not_started",
    "output": "not_started"
  }
}
```

### 18.2. Tập trạng thái chuẩn

- `not_started`: chưa bắt đầu.
- `queued`: đã xếp hàng.
- `queued_waiting_resource`: đang chờ tài nguyên.
- `running`: đang xử lý.
- `waiting_review`: **chỉ là nhãn tổng hợp cho UI**, không thuộc `processing_status`.
- `approved`: **chỉ là nhãn tổng hợp cho UI**, không thuộc `processing_status`; quyết định thật nằm trong `review_status`.
- `verifying`: đang kiểm tra tự động.
- `verified`: đầu ra kỹ thuật đã vượt kiểm tra tự động và có thể được duyệt/sử dụng theo policy.
- `cancelling`: đã nhận yêu cầu hủy nhưng tiến trình chưa dừng hoàn toàn.
- `cancelled`: đã hủy an toàn.
- `failed`: thất bại và đã ghi lỗi.
- `stale`: ghi nhận đang chạy nhưng lease/heartbeat hết hạn.
- `interrupted`: bị gián đoạn bất thường.
- `recovering`: đang phục hồi từ trạng thái lưu.
- `paused_resource_limit`: tạm dừng vì giới hạn tài nguyên.
- `invalidated`: kết quả bị mất hiệu lực.
- `completed`: chỉ dùng cho `job_status`, nghĩa là job đã kết thúc mọi công việc bắt buộc sau verify/publish/review gate; asset vẫn ở `processing_status=verified`.
- `skipped`: bỏ qua hợp lệ vì dùng fallback hoặc tính năng không áp dụng.

`processing_status` của asset/module chỉ nhận `not_started`, `queued`, `queued_waiting_resource`, `running`, `verifying`, `verified`, `failed`, `invalidated`, `skipped`. `job_status` chỉ nhận `queued`, `queued_waiting_resource`, `running`, `verifying`, `completed`, `cancelling`, `cancelled`, `failed`, `stale`, `interrupted`, `recovering`, `paused_resource_limit`. Không dùng `completed` làm trạng thái kỹ thuật của asset. `review_status` chỉ nhận `not_required`, `pending`, `approved`, `rejected`, `revoked`. Asset dùng cho render phải có `processing_status=verified` và, nếu cần duyệt, `review_status=approved`.

### 18.3. Quy tắc chuyển trạng thái

```text
asset/module processing_status:
not_started → queued → running → verifying → verified
                    ↓          ↓           ↓
                  failed    invalidated  invalidated

review_status (độc lập):
not_required | pending → approved | rejected | revoked

Nhãn UI:
waiting_review = processing_status=verified và review_status=pending
approved = processing_status=verified và review_status=approved
```

Đối với `job_status`:

```text
queued → queued_waiting_resource → running → verifying → completed
                                      ↓            ↓
                                  cancelling     failed
                                      ↓
                                  cancelled
```

Quy tắc bắt buộc:

- Chỉ worker sở hữu job hoặc project manager mới được cập nhật trạng thái xử lý.
- Job không chuyển thẳng từ `running` sang `completed`: worker phải verify và publish mọi output bắt buộc, ghi asset `processing_status=verified`, kiểm tra review gate nếu áp dụng, rồi mới đặt `job_status=completed`.
- `review_status=approved` là quyết định của người dùng; `processing_status=verified` là kết quả kiểm tra tự động. Không ghi `approved` vào `processing_status`.
- Khi đầu vào thay đổi, các kết quả phụ thuộc chuyển sang `invalidated`, mọi `review_status=approved` của chính kết quả bị đổi thành `revoked` (hoặc tạo review revision mới), và kết quả không được dùng để render.
- Job `running` nhưng hết lease/heartbeat chuyển `stale`; recovery manager xác minh process tree rồi chuyển `interrupted` hoặc `recovering`.
- Mọi chuyển trạng thái phải lưu `from`, `to`, `reason`, `actor` và thời gian.

### 18.4. Lịch sử trạng thái

```json
{
  "schema_name": "state_history",
  "schema_version": 1,
  "state_history": [
    {
      "module": "render",
      "state_dimension": "processing_status",
      "from": "running",
      "to": "interrupted",
      "reason": "worker_heartbeat_expired",
      "actor": "recovery_manager",
      "changed_at_utc": "2026-07-14T21:00:00Z"
    }
  ]
}
```

---

## 19. Nguồn trạng thái, khóa ghi và phục hồi

### 19.1. Nguồn dữ liệu

- **SQLite:** nguồn chuẩn cho job, queue, lock, heartbeat, trạng thái worker và lịch sử chuyển trạng thái.
- **JSON:** snapshot/export có revision; không quyết định trạng thái chạy hiện hành.
- **File hệ thống:** audio, ảnh, mask, segment, preview và video cuối.

Nếu dữ liệu mâu thuẫn:

1. SQLite revision/sequence là nguồn chuẩn của trạng thái hiện hành.
2. Kiểm tra file thực tế, hash và trạng thái publish.
3. JSON snapshot cũ được tạo lại theo `database_revision`, không ghi ngược vào SQLite.
4. Không tự đánh dấu hoàn thành nếu file hoặc hash không hợp lệ.
5. Ghi sự kiện phục hồi và yêu cầu duyệt thủ công nếu không xác định được nguồn đúng.

### 19.2. Ghi file nguyên tử

Mọi JSON quan trọng phải được ghi theo quy trình:

```text
Tạo file .tmp trong cùng thư mục
→ ghi đầy đủ
→ flush
→ `fsync` file trên nền tảng hỗ trợ; nếu nền tảng không cung cấp durability tương đương, ghi rõ capability degraded
→ đọc lại và kiểm tra JSON/schema
→ đổi tên nguyên tử trong cùng filesystem sang file chính
→ `fsync` thư mục cha trên nền tảng hỗ trợ trước khi đánh dấu published
```

Không ghi đè trực tiếp file chính. Trước migration schema hoặc sửa dữ liệu quan trọng phải tạo bản sao an toàn.

### 19.3. Lock và lease

```json
{
  "schema_name": "resource_lock",
  "schema_version": 1,
  "lock_id": "uuid",
  "resource_type": "project",
  "resource_id": "lofi_20260714_001",
  "owner_id": "worker_uuid",
  "pid": 1234,
  "process_started_at_utc": "2026-07-14T20:00:00Z",
  "acquired_at_utc": "2026-07-14T20:00:02Z",
  "heartbeat_at_utc": "2026-07-14T20:00:12Z",
  "lease_expires_at_utc": "2026-07-14T20:00:42Z",
  "fencing_token": 42
}
```

- Không xác định tiến trình chỉ bằng PID.
- Lock phải có `owner_id`, thời điểm bắt đầu tiến trình, heartbeat và thời hạn lease.
- Worker gia hạn heartbeat theo chu kỳ cấu hình.
- Mỗi lần cấp lại lock phải tăng `fencing_token` bằng transaction. Mọi lệnh ghi/publish phải mang token hiện hành; database từ chối token cũ để stale writer không thể ghi sau khi lease bị thu hồi.
- Chỉ thu hồi lock khi lease hết và tiến trình sở hữu không còn hợp lệ; việc kiểm tra process chỉ hỗ trợ, fencing token mới là hàng rào bắt buộc.
- Một dự án chỉ có một writer; nhiều reader được phép nếu không sửa dữ liệu.

### 19.4. Phục hồi khi mở ứng dụng

```text
Đọc danh sách job chưa kết thúc
→ kiểm tra lock và heartbeat
→ xác minh tiến trình sở hữu
→ kiểm tra file tạm, checkpoint và segment
→ FFprobe/hash lại đầu ra đã có
→ giữ kết quả đạt
→ chuyển job sang recovering
→ xếp lại phần chưa hoàn thành
```

Không xóa file tạm hoặc segment trước khi phục hồi xác định chúng không còn cần thiết.

---

## 20. Schema và hợp đồng dữ liệu

### 20.1. Quy tắc bắt buộc

Mỗi JSON phải có:

```json
{
  "schema_name": "project",
  "schema_version": 1
}
```

Schema phải quy định:

- Trường bắt buộc và tùy chọn.
- Kiểu dữ liệu, enum, giá trị mặc định và giới hạn.
- Đơn vị của trường số.
- Quy tắc phụ thuộc giữa các trường.
- Có cho phép trường lạ hay không.
- Cách migration từ phiên bản cũ.
- Ví dụ hợp lệ và ví dụ không hợp lệ.

### 20.2. Danh sách schema tối thiểu

- `project`
- `track_metadata`
- `rights_review`
- `trend_snapshot`
- `trend_ranking`
- `vibe_profile`
- `composition_metadata`
- `image_metadata`
- `prompt_metadata`
- `model_metadata`
- `layers_metadata`
- `job`
- `render_job`
- `render_progress`
- `segment_metadata`
- `error_record`
- `output_manifest`
- `app_config`

### 20.3. Render job mẫu

```json
{
  "schema_name": "render_job",
  "schema_version": 1,
  "job_id": "uuid",
  "project_id": "lofi_20260714_001",
  "job_status": "queued",
  "created_at_utc": "2026-07-14T21:00:00Z",
  "video_codec": "h264",
  "requested_video_encoder": "h264_nvenc",
  "actual_video_encoder": null,
  "resolution": {"width": 1920, "height": 1080},
  "fps": {"numerator": 24, "denominator": 1},
  "input_hash": "sha256",
  "config_hash": "sha256",
  "config_snapshot_path": "jobs/<job_id>/config_snapshot.json",
  "producer_version": "4.5.0",
  "segments": []
}
```

### 20.4. Migration

- Không sửa âm thầm JSON cũ khi chưa tạo backup.
- Migration phải có phiên bản nguồn, phiên bản đích và log.
- Nếu không hỗ trợ migration, mở dự án ở chế độ chỉ đọc và báo rõ.
- Không loại bỏ trường chưa hiểu nếu mục tiêu chỉ là đọc và lưu lại dữ liệu cũ.

---

## 21. Chuẩn lỗi, retry và fallback

### 21.1. Cấu trúc lỗi

```json
{
  "schema_name": "error_record",
  "schema_version": 1,
  "error_id": "uuid",
  "error_code": "RENDER_INSUFFICIENT_DISK",
  "category": "resource",
  "step": "render_precheck",
  "message": "Không đủ dung lượng trống",
  "technical_detail": null,
  "retryable": false,
  "fallback_available": false,
  "suggested_action": "Chọn thư mục tạm khác",
  "occurred_at_utc": "2026-07-14T21:00:00Z"
}
```

### 21.2. Tiền tố mã lỗi

- `INPUT_*`
- `NETWORK_*`
- `RIGHTS_*`
- `AUDIO_*`
- `IMAGE_*`
- `MODEL_*`
- `GPU_*`
- `LAYER_*`
- `RENDER_*`
- `STORAGE_*`
- `STATE_*`
- `SECURITY_*`

### 21.3. Chính sách retry

- Chỉ retry lỗi được đánh dấu `retryable`.
- Retry phải giới hạn số lần và dùng backoff.
- Không retry truy vấn cũ khi người dùng đã gửi truy vấn mới.
- Không retry vô hạn lỗi hash, schema, quyền ghi hoặc đầu vào hỏng.
- Mỗi lần retry phải ghi số lần, lý do và kết quả.

Mặc định:

```text
API timeout: tối đa 3 lần, chờ 2 giây, 5 giây, 15 giây.
- HTTP 429: tôn trọng Retry-After; nếu không có thì dừng và giữ cache.
- Thiếu VRAM 960×540: giải phóng model, thử lại 1 lần, sau đó giảm 768×432.
- NVENC không khả dụng: thử kiểm tra 1 lần, sau đó fallback libx264.
- Sai hash model: không retry nạp; yêu cầu tải lại hoặc chọn file khác.
- Ổ đĩa không đủ: không bắt đầu job; yêu cầu đổi thư mục hoặc dọn dữ liệu.
```

### 21.4. Fallback bắt buộc

- AI upscale → Lanczos.
- Ba lớp → hai lớp → ảnh phẳng.
- NVENC → libx264.
- Trend API mới → cache còn hợp lệ → hiển thị dữ liệu cũ kèm thời điểm.
- Tạo ảnh online/local lỗi → ảnh người dùng cung cấp.
- Parallax lỗi → zoom/pan nhẹ.

Fallback phải được ghi vào metadata và không được giả vờ rằng phương án ban đầu đã chạy thành công.

---

## 22. Bảo mật

### 22.1. File và đường dẫn

- Chuẩn hóa đường dẫn trước khi dùng.
- Chặn `..`, ký tự điều khiển và đường dẫn thoát khỏi thư mục được phép.
- Chặn symlink/junction/reparse-point thoát vùng dữ liệu. Khi nền tảng hỗ trợ, mở file tương đối qua directory handle với chế độ không-follow; sau khi mở phải kiểm tra canonical path/file identity để giảm TOCTOU.
- Không ghi đè file nguồn.
- Tên file nội bộ do hệ thống tạo; không dùng trực tiếp tên từ URL.
- Giới hạn độ dài tên và đường dẫn.
- Kiểm tra quyền ghi trước khi bắt đầu job.

### 22.2. URL và tải xuống

- Chỉ cho phép `http` và `https` với nguồn hỗ trợ.
- Chặn URL chứa thông tin xác thực hoặc trỏ đến tài nguyên nội bộ không được phép.
- Có timeout kết nối, timeout đọc và giới hạn chuyển hướng.
- Giới hạn kích thước trước và trong khi tải.
- Kiểm tra MIME, magic bytes và nội dung bằng công cụ media.
- Không tin vào phần mở rộng file.
- Tải vào `.part`, kiểm tra xong mới đổi tên.
- Không thực thi file đã tải.
- Không ghi token hoặc URL nhạy cảm vào log.

### 22.3. Chống SSRF

- Resolve DNS trước kết nối; kiểm tra toàn bộ A/AAAA, chỉ kết nối tới IP đã được chấp thuận và kiểm tra địa chỉ peer thực tế sau khi kết nối.
- Chặn loopback, private, link-local, multicast, reserved và cloud metadata cho IPv4/IPv6.
- Kiểm tra lại sau từng redirect; giới hạn scheme, port và số redirect.
- Chống DNS rebinding bằng pin IP đã xác minh hoặc xác minh ngay trước kết nối.
- Không tự dùng proxy từ biến môi trường.

### 22.4. File/media không đáng tin

- Không tự giải nén archive; giới hạn kích thước sau giải nén, số entry, độ sâu và tỷ lệ nén.
- Giới hạn pixel/frame/stream/thời lượng trước decode đầy đủ.
- Probe/decode trong process giới hạn CPU, RAM, thời gian và quyền.
- File nghi vấn chuyển quarantine, không dùng làm asset/model.

### 22.5. Tiến trình con

- Truyền tham số dưới dạng danh sách đối số; không ghép chuỗi người dùng thành lệnh shell.
- Chỉ hủy tiến trình do đúng worker/job tạo.
- Xác minh `owner_id`, PID và thời điểm bắt đầu trước khi gửi tín hiệu.
- Tạo process group hoặc Windows Job Object; dừng mềm, chờ grace period, rồi buộc dừng toàn bộ process tree. Ghi exit code, signal và forced kill.
- Hạn chế quyền của tiến trình theo mức cần thiết.

### 22.6. Token và bí mật

- Không lưu trong `project.json`, manifest, log hoặc file chia sẻ.
- Lưu trong kho bí mật của hệ điều hành nếu khả dụng; nếu không, dùng file riêng có quyền truy cập hạn chế.
- Giao diện chỉ hiển thị trạng thái đã cấu hình và một phần đã che.
- Khi xuất chẩn đoán phải lọc token, cookie, header và URL chứa khóa.

### 22.7. Model và tài nguyên âm thanh

- Lưu nguồn, giấy phép, kích thước và SHA-256.
- Không nạp model sai hash, file tải dở hoặc file không đọc được.
- Cảnh báo tài nguyên không rõ nguồn.
- Không tự tải, tự cập nhật hoặc thay model khi người dùng chưa đồng ý.

---

## 23. Cache và ma trận vô hiệu hóa

### 23.1. Thành phần hash

Mỗi `input_hash` phải bao gồm:

- Hash nội dung của dữ liệu đầu vào.
- Cấu hình ảnh hưởng đến kết quả.
- Hash model và tài nguyên liên quan.
- Phiên bản thuật toán.
- Phiên bản code producer.
- Chỉ gồm thông số môi trường thực sự ảnh hưởng output/khả năng tương thích (ví dụ FFmpeg build, driver khi cần); không hash toàn bộ máy vì sẽ phá cache không cần thiết. Trường ảnh hưởng phải được schema/version hóa.

Không chỉ hash đường dẫn.

### 23.2. Metadata cache

```json
{
  "schema_name": "cache_metadata",
  "schema_version": 1,
  "input_hash": "...",
  "output_hash": "...",
  "producer_name": "layer_separator",
  "producer_version": "1.0.0",
  "created_at_utc": "...",
  "verified_at_utc": "...",
  "verification_status": "passed",
  "outputs": [
    "layers/foreground.png"
  ],
  "invalidated_reason": null
}
```

### 23.3. Đồ thị nguồn gốc và phụ thuộc

```json
{
  "schema_name": "asset_provenance",
  "schema_version": 1,
  "asset_id": "image_derived_001",
  "derived_from": [{"asset_id": "audio_001", "relation": "vibe_source", "input_hash": "..."}],
  "producer": {"name": "prompt_builder", "version": "1.0.0"}
}
```

Invalidation lan truyền theo dependency graph. Ảnh local không phụ thuộc audio; ảnh AI từ vibe phụ thuộc audio/vibe, trừ khi người dùng đóng băng asset và quyết định đó được ghi provenance.

### 23.4. Ma trận vô hiệu hóa

- Đổi ambience: vô hiệu audio preview, final preview và render; giữ ảnh và layer.
- Đổi audio nguồn: vô hiệu audio preview, final preview, render và manifest; giữ ảnh nếu người dùng không yêu cầu tạo lại theo vibe.
- Đổi ảnh nguồn: vô hiệu upscale, depth, mask, background fill, preview và render.
- Chỉnh mask: giữ ảnh và depth map; vô hiệu layer đã ghép, background fill, preview và render.
- Đổi FPS hoặc độ phân giải: vô hiệu preview chuyển động, segment, video cuối và manifest.
- Đổi encoder: vô hiệu segment, video cuối và manifest; giữ nguồn và preview nội dung.
- Đổi model hoặc tham số tách lớp: vô hiệu toàn bộ cache tách lớp.
- Đổi filter builder: vô hiệu preview và render.
- Rights review bị thu hồi/hết hiệu lực hoặc bằng chứng không còn áp dụng: chặn asset khỏi job mới; invalidated mọi preview/render/manifest chưa công bố phụ thuộc asset. Đầu ra đã công bố không bị sửa lịch sử nhưng được gắn cảnh báo/revocation event và không tái sử dụng.

Cache gắn với asset đã được duyệt không tự xóa theo TTL thông thường nhưng vẫn phải invalidated khi đầu vào, quyền sử dụng hoặc producer/config ảnh hưởng kết quả thay đổi.

---

## 24. Quyền sử dụng và bằng chứng

### 24.1. Nguyên tắc

- Trend score không chứng minh quyền sử dụng.
- Credit không thay thế giấy phép.
- Metadata nền tảng không tự chứng minh người đăng có đầy đủ quyền.
- Nhạc, sample, ambience, SoundFont, model và ảnh phải được đánh giá riêng.
- Hệ thống hỗ trợ thu thập bằng chứng; người dùng chịu trách nhiệm duyệt cuối.

### 24.2. Rights review

```json
{
  "schema_name": "rights_review",
  "schema_version": 1,
  "asset_id": "...",
  "status": "requires_manual_review",
  "license": "unknown",
  "evidence": [
    {
      "type": "license_page",
      "url": "https://...",
      "captured_at_utc": "...",
      "content_hash": "...",
      "applies_to_source_id": "...",
      "notes": null
    }
  ],
  "reviewed_by": null,
  "reviewed_at_utc": null
}
```

`licensed_for_use` chỉ được đặt khi có bằng chứng áp dụng đúng tài nguyên và mục đích sử dụng. `user_attested_ownership` chỉ được đặt bằng thao tác rõ ràng của người dùng và có audit record. Nếu bằng chứng không rõ, đặt `requires_manual_review` hoặc `blocked_from_download`.

### 24.3. Bản bằng chứng bất biến

- `content_hash` là hash snapshot bằng chứng, không phải hash URL.
- Lưu MIME, kích thước, nguồn, thời điểm, asset version/hash, phạm vi và mục đích sử dụng.
- Nếu không được phép snapshot, lưu tài liệu người dùng cung cấp hoặc manual-review record; không giả vờ có snapshot.
- Thay đổi/thu hồi giấy phép tạo revision mới, không sửa lịch sử.

### 24.4. Sáng tác và tương đồng nội bộ

Tên mục “Quy tắc chống sao chép” được hiểu là **quy tắc giảm trùng lặp nội bộ**.

- Chỉ so sánh với kho bài do hệ thống nội bộ đã tạo.
- Không tuyên bố đã kiểm tra toàn bộ âm nhạc bên ngoài.
- Không coi điểm tương đồng thấp là bằng chứng pháp lý.
- Biểu diễn melody phải được chuẩn hóa để phát hiện chuyển tông và thay đổi nhịp nhẹ; hash byte chỉ dùng chống trùng chính xác.
- Cấu hình ngưỡng, số lần sinh lại tối đa và hành động khi vẫn vượt ngưỡng.

---

## 25. Kiểm tra chất lượng định lượng

Mỗi tiêu chí được gắn một loại:

- `[AUTO]`: hệ thống tự xác minh và có thể chặn bước tiếp theo.
- `[WARN]`: hệ thống cảnh báo, người dùng quyết định.
- `[HUMAN]`: bắt buộc người dùng duyệt.

### 25.1. Audio

- `AUD-ACC-001 [AUTO]`: có ít nhất một audio stream đọc được.
- `AUD-ACC-002 [AUTO]`: audio đầu ra là AAC, 48 kHz, channel layout đã ghi trong metadata.
- `AUD-ACC-003 [AUTO]`: integrated loudness nằm trong sai số cấu hình quanh target; mặc định ±1 LU.
- `AUD-ACC-004 [AUTO]`: true peak không vượt giới hạn profile; mặc định -1 dBTP.
- `AUD-ACC-005 [AUTO]`: không có lỗi decode và không có clipping vượt ngưỡng cấu hình.
- `AUD-ACC-006 [WARN]`: cảnh báo khoảng im lặng dài bất thường hoặc lệch kênh lớn.
- `AUD-ACC-007 [WARN]`: cảnh báo chênh loudness và phổ âm lớn quanh điểm loop.
- `AUD-ACC-008 [HUMAN]`: ambience không che nhạc.
- `AUD-ACC-009 [HUMAN]`: điểm loop không gây chú ý khi nghe bình thường.

### 25.2. Ảnh và layer

- `IMG-ACC-001 [AUTO]`: ảnh đọc được và không rỗng.
- `IMG-ACC-002 [AUTO]`: ảnh cuối đúng 1920×1080.
- `IMG-ACC-003 [AUTO]`: ảnh không chứa alpha bất ngờ nếu pipeline yêu cầu RGB.
- `IMG-ACC-004 [WARN]`: cảnh báo ảnh có vùng đen lớn, highlight cháy hoặc shadow mất chi tiết vượt ngưỡng.
- `LYR-ACC-001 [AUTO]`: mask đúng kích thước, giá trị hợp lệ và không rỗng bất thường.
- `LYR-ACC-002 [WARN]`: cảnh báo cạnh răng cưa, halo hoặc lỗ nhỏ vượt ngưỡng.
- `LYR-ACC-003 [HUMAN]`: người dùng duyệt cạnh mask và background fill.

### 25.3. Preview và render

- `RND-ACC-001 [AUTO]`: có video stream và audio stream.
- `RND-ACC-002 [AUTO]`: 1920×1080, 24 FPS hoặc đúng profile đã chọn.
- `RND-ACC-003 [AUTO]`: codec, pixel format, sample rate và channel layout đúng profile thực tế.
- `RND-ACC-004 [AUTO]`: tổng thời lượng trong sai số tối đa một frame cộng sai số audio cấu hình.
- `RND-ACC-005 [AUTO]`: không có lỗi decode toàn file.
- `RND-ACC-006 [WARN]`: cảnh báo đoạn đen dài, timestamp gián đoạn hoặc chênh frame lớn tại điểm nối.
- `RND-ACC-007 [HUMAN]`: không có rung, nhấp nháy, viền mask hoặc chuyển động gây khó chịu.
- `RND-ACC-008 [HUMAN]`: màu sắc, hiệu ứng và mức che chủ thể được chấp nhận.

Các ngưỡng chi tiết phải nằm trong file cấu hình có phiên bản, không hard-code rải rác.

---

## 26. Đặc tả render phân đoạn

### 26.1. Tham số phải đồng nhất

Các video segment phải thống nhất:

- Pixel format.
- Codec profile và level.
- GOP size và keyframe interval.
- Time base.
- Color space, color primaries, transfer và color range.
- Nếu capability `audio_per_segment` được bật, audio sample format, sample rate, channel layout và delay/padding policy cũng phải đồng nhất.
- Timestamp policy.
- Encoder preset và rate-control mode.

### 26.2. Segment metadata

```json
{
  "schema_name": "segment_metadata",
  "schema_version": 1,
  "segment_index": 2,
  "start_seconds_display": 1200,
  "duration_seconds_display": 600,
  "start_pts": 28800,
  "end_pts_exclusive": 43200,
  "video_time_base": "1/24",
  "contains_audio": false,
  "audio_start_sample": null,
  "audio_end_sample_exclusive": null,
  "animation_time_offset_pts": 28800,
  "processing_status": "verified",
  "review_status": "not_required",
  "video_sha256": "...",
  "probe_result_path": "...",
  "first_frame_hash": "...",
  "last_frame_hash": "...",
  "video_codec": "h264",
  "requested_video_encoder": "h264_nvenc",
  "actual_video_encoder": "h264_nvenc"
}
```

### 26.3. Timeline và biên audio/video

- Dùng time base hữu tỉ, không dùng float làm khóa timeline.
- Segment dùng khoảng nửa mở `[start, end)`: khóa chuẩn cho video là `start_pts`, `end_pts_exclusive`, `video_time_base`. Khi `contains_audio=true`, mới bắt buộc `audio_start_sample` và `audio_end_sample_exclusive`; khi video-only, hai trường phải là `null`. Trường giây chỉ để hiển thị và phải được suy ra từ khóa chuẩn, không dùng để cắt. Độ dài phải thỏa `end-start`, tránh trùng hoặc hụt một frame/sample.
- Video cắt trên biên frame; audio cắt trên biên sample.
- Audio master được xử lý một lần trên timeline liên tục. Mặc định segment chỉ chứa video; sau khi nối video, mux audio master đúng một lần để tránh AAC encoder delay/padding tại mỗi biên.
- Chỉ encode audio theo segment khi container/encoder và phép đo delay/padding đã có kiểm thử chứng minh không gap/overlap; nếu không, đường này bị disabled.
- Continuity dùng SSIM/histogram/timestamp/audio-gap; frame hash chỉ kiểm tra toàn vẹn.

### 26.4. Quy tắc nối

- Chỉ nối trực tiếp segment đã verify và có tham số tương thích.
- Kiểm tra timestamp và continuity hình ảnh. Chỉ kiểm tra audio gap/overlap giữa segment khi `audio_per_segment` được bật; đường mặc định kiểm tra audio master sau mux.
- Nếu một segment lỗi, ưu tiên render lại segment đó.
- Chỉ encode lại toàn bộ khi không thể bảo đảm tính tương thích hoặc continuity.
- Không xóa segment cho đến khi video nối đã verify và chính sách dọn dữ liệu cho phép.

---

## 27. Lập lịch tài nguyên

### 27.1. Loại công việc

- `gpu_heavy`: Stable Diffusion, depth, SAM, inpainting, AI upscale.
- `gpu_encode`: NVENC.
- `cpu_heavy`: libx264, một số bước audio và xử lý ảnh.
- `network`: API, tải model và tải asset.
- `disk_heavy`: hashing file lớn, render, nối và sao chép.
- `lightweight`: FFprobe ngắn, validation JSON và thao tác giao diện.

### 27.2. Quy tắc

- Trên GPU 4GB, chỉ một job `gpu_heavy` hoặc `gpu_encode` chạy tại một thời điểm.
- Job `lightweight` có thể chạy khi GPU bận nếu không tranh chấp file.
- `disk_heavy` phải được giới hạn để không làm render thiếu dữ liệu hoặc treo giao diện.
- `cpu_heavy` phải giới hạn luồng để hệ thống vẫn phản hồi.
- Preview người dùng đang chờ ưu tiên hơn quét trend nền.
- Hủy, kiểm tra heartbeat và phục hồi có ưu tiên cao nhất.
- Render dài có thể nhường tài nguyên giữa các segment nếu có job preview ưu tiên cao.
- Scheduler phải ngăn hai job ghi cùng một asset hoặc cùng một đường dẫn đầu ra.

---

## 28. Cấu hình, log và chẩn đoán hệ thống

### 28.1. Cấu hình tập trung

```text
config/app.json
config/render_profiles.json
config/audio_profiles.json
config/resource_limits.json
config/local_ai.json
```

Cần tách:

- Cấu hình mặc định của ứng dụng.
- Cấu hình riêng dự án.
- Bí mật và token.
- Cấu hình có thể thay đổi khi job chạy.
- Cấu hình buộc vô hiệu cache.

Mọi file cấu hình có `schema_version`. Không thay đổi cấu hình ảnh hưởng đầu ra giữa lúc job đang chạy; phải tạo job mới hoặc invalidation rõ ràng.

### 28.2. Log

- Mức: `DEBUG`, `INFO`, `WARNING`, `ERROR`.
- Mỗi job có log riêng; log hệ thống có file riêng.
- Không ghi token, cookie, header nhạy cảm hoặc đường dẫn bí mật.
- Giới hạn kích thước và xoay vòng log.
- Giao diện chỉ đọc số dòng cuối theo cấu hình.
- Log kỹ thuật đầy đủ không được hiển thị trực tiếp nếu có dữ liệu nhạy cảm.
- Mọi fallback, retry, state transition và thao tác dọn dữ liệu phải được ghi log.

### 28.3. System check

`system_check.py` phải kiểm tra:

- Phiên bản Python, FFmpeg và FFprobe.
- Khả năng encode NVENC thực tế, không chỉ kiểm tra tên encoder.
- GPU, VRAM, driver, RAM và dung lượng trống.
- Quyền ghi thư mục dữ liệu và thư mục tạm.
- SQLite có thể tạo transaction và lock.
- Stable Diffusion API và model nếu tính năng được bật.
- Port cần dùng có bị chiếm không.
- Token API đã được cấu hình hay chưa nhưng không hiển thị token.
- Khả năng tạo, theo dõi và dừng tiến trình con.

Kết quả gồm: `passed`, `warning`, `failed`, `not_applicable`. Tính năng Optional lỗi không được chặn luồng Core.

---

## 29. Quy tắc đặt tên và quản lý file

### 29.1. Định danh

- Project/job/asset/event/lock ID dùng UUID không tái sử dụng.
- Tên hiển thị không dùng làm khóa; thư mục dùng UUID/slug sinh từ UUID.
- Restore/duplicate tạo ID mới và lưu `origin_id`.

### 29.2. Tên file

- Không đổi tên file gốc do người dùng cung cấp; tạo bản sao nội bộ với tên an toàn.
- Tên nội bộ chỉ dùng chữ thường ASCII, số, dấu gạch dưới và dấu chấm phần mở rộng.
- Không dùng dấu cách, ký tự điều khiển hoặc tên dành riêng của hệ điều hành.
- File tạm dùng hậu tố `.tmp` hoặc `.part`.
- File đã verify không dùng hậu tố tạm.
- Mỗi asset có `asset_id`; tên hiển thị tách khỏi tên file lưu trữ.
- Không dùng timestamp đơn lẻ để bảo đảm duy nhất; kết hợp UUID hoặc counter an toàn.
- Metadata phải cập nhật nguyên tử nếu đường dẫn file thay đổi.

---

## 30. Dọn dữ liệu dựa trên tham chiếu

- Mỗi asset có owner và danh sách tham chiếu từ project/job.
- Không xóa asset của job đang chạy, asset có lock hoặc asset được dự án đang mở sử dụng.
- Trước khi xóa hàng loạt phải tạo danh sách xem trước.
- Nếu dung lượng cho phép, di chuyển vào thùng rác nội bộ trước khi xóa vĩnh viễn.
- Ghi log tên asset, lý do, kích thước và thời gian xóa.
- File mồ côi chỉ được xóa sau khi đối chiếu SQLite, JSON và thư mục dự án.
- Cache gắn với asset đã duyệt không tự xóa theo TTL thông thường; việc duyệt không miễn kiểm tra dependency, quyền sử dụng và hash.
- Người dùng có thể đặt giới hạn dung lượng theo loại: model, cache, preview, log và temp.

---

## 31. Mã yêu cầu và truy vết kiểm thử

### 31.1. Tiền tố

- `PRJ`: quản lý dự án.
- `TRD`: xu hướng.
- `RGT`: quyền sử dụng.
- `AUD`: âm thanh.
- `CMP`: sáng tác.
- `IMG`: hình ảnh.
- `MDL`: model.
- `LYR`: tách lớp.
- `ANM`: animation.
- `QUE`: hàng đợi.
- `RND`: render.
- `VER`: xác minh.
- `SEC`: bảo mật.
- `DAT`: dữ liệu.
- `ERR`: xử lý lỗi.

### 31.2. Yêu cầu nền tảng bắt buộc

- `PRJ-001`: lưu dự án nguyên tử sau mỗi thay đổi quan trọng.
- `PRJ-002`: một dự án chỉ có một writer tại một thời điểm.
- `PRJ-003`: mở lại ứng dụng phải phục hồi được job chưa kết thúc.
- `DAT-001`: mọi JSON quan trọng phải được xác minh theo schema.
- `DAT-002`: mọi asset dùng lại phải có hash nội dung.
- `ERR-001`: mọi lỗi chặn luồng phải có mã lỗi và hành động đề xuất.
- `SEC-001`: đường dẫn đầu ra không được thoát khỏi vùng được phép.
- `SEC-002`: token không được xuất hiện trong log hoặc manifest.
- `AUD-001`: audio được chọn phải có audio stream hợp lệ.
- `IMG-001`: ảnh cuối phải là 1920×1080 hoặc đúng profile dự án.
- `QUE-001`: chỉ một tác vụ GPU nặng chạy trên cấu hình 4GB.
- `RND-001`: render giao diện phải chạy qua worker nền.
- `RND-002`: segment chỉ được dùng lại khi input/config/producer hash, codec parameters, verification report và quyền phụ thuộc vẫn hợp lệ.
- `RND-003`: animation phải dùng absolute time offset giữa các segment.
- `VER-001`: video asset chỉ `processing_status=verified` sau khi verify đạt; output job chỉ `job_status=completed` sau verify, publish và review gate.
- `VER-002`: manifest phải ghi requested/actual cho encoder, layer mode và mọi giá trị có fallback; codec được ghi riêng, không dùng tên encoder làm codec.

Mỗi kiểm thử dùng mã `TEST-<mã yêu cầu>`. Toàn bộ yêu cầu bắt buộc nằm trong `requirements/*.md` và `traceability_matrix.csv`; release gate chặn requirement thiếu test/trạng thái.

---

## 32. Tiêu chí sẵn sàng bắt đầu code

Chỉ bắt đầu triển khai **Core nền tảng** khi đạt các điều kiện dưới đây. Module nào còn giá trị ở Mục 41.3 chưa chốt thì chưa được code hành vi phụ thuộc giá trị đó:

- Phạm vi Core/Enhanced/Optional/Future đã chốt.
- State machine và quyền cập nhật trạng thái đã chốt.
- Schema tối thiểu cho project, job, segment, error và manifest đã có.
- Cơ chế SQLite, lock, heartbeat và ghi file nguyên tử đã chốt.
- Chính sách lỗi, retry và fallback đã chốt.
- Bảo mật URL, file, token và subprocess đã chốt.
- Ma trận invalidation và thành phần hash đã chốt.
- Tiêu chí nghiệm thu Core được gắn `[AUTO]`, `[WARN]` hoặc `[HUMAN]`.
- Kiểm thử nền móng có thể liên kết với mã yêu cầu.

### Thứ tự code bắt buộc

Tuân theo Mục 15; không duy trì danh sách thứ hai.
---

## 33. Tiêu chí hoàn thành phiên bản 4.5

Phiên bản 4.5 chỉ được coi là hoàn thành khi:

- Luồng Core xuất video được khi không có Internet và không có Stable Diffusion.
- Mất điện hoặc đóng ứng dụng không làm hỏng project metadata đã lưu.
- Job dang dở có thể khôi phục hoặc kết thúc an toàn.
- Không có hai writer cùng sửa một dự án.
- Asset và cache được xác định bằng hash nội dung và phiên bản producer.
- Mọi fallback được ghi rõ trong metadata.
- Quyền sử dụng tách biệt hoàn toàn với điểm xu hướng.
- Token và dữ liệu nhạy cảm không xuất hiện trong log, project hoặc manifest.
- Render segment giữ liên tục animation; audio master sau mux liên tục và đúng sample timeline.
- Video cuối vượt qua kiểm tra tự động và các điểm duyệt bắt buộc.
- Manifest đủ dữ liệu truy vết phiên bản ứng dụng, công cụ, model, seed, video codec, encoder yêu cầu, encoder thực tế và lý do fallback.
- Toàn bộ kiểm thử bắt buộc của từng giai đoạn đạt trước khi chuyển giai đoạn tiếp theo.

---

## 34. Idempotency, giao dịch và tính nhất quán

- Mỗi lệnh tạo job phải có `idempotency_key`, `request_payload_hash` và scope người dùng/project. Database phải có unique constraint trên scope+key; tạo/nhận job diễn ra trong một transaction. Cùng khóa khác payload phải trả lỗi xung đột, không tái sử dụng job cũ.
- Một job chỉ công bố đầu ra sau khi file, hash, schema và probe đều đạt.
- SQLite và filesystem không có transaction nguyên tử chung. Dùng publish protocol: transaction tạo bản ghi `prepared` kèm temp path/hash/fencing token → ghi và fsync file tạm → đổi tên nguyên tử trong cùng filesystem → fsync thư mục cha → transaction đối chiếu token/hash rồi chuyển `published`. Recovery phải xử lý idempotent mọi điểm dừng; không cho consumer thấy asset trước trạng thái `published`.
- Nếu file đã công bố nhưng transaction thất bại, recovery manager đánh dấu asset mồ côi và không tự dùng.
- Nếu transaction đã commit nhưng file thiếu, job chuyển `interrupted` hoặc `failed`, không `completed`.
- Event và callback của worker phải có `event_id` để chống xử lý lặp.
- Các thao tác hủy, tiếp tục, duyệt và invalidation phải an toàn khi gọi nhiều lần.
- Đồng hồ hệ thống không được dùng làm nguồn duy nhất để sắp thứ tự; dùng sequence tăng dần trong database và UTC để hiển thị.

### 34.1. Ranh giới transaction

- Tạo project.
- Nhận quyền writer.
- Tạo hoặc nhận job theo idempotency constraint.
- Claim job từ queue bằng một transaction/compare-and-swap duy nhất, đồng thời gán owner, lease và fencing token; hai worker không được cùng claim một job.
- Chuyển trạng thái.
- Công bố asset đã verify.
- Duyệt hoặc thu hồi duyệt.
- Xóa hoặc chuyển asset vào thùng rác.

Mỗi transaction phải ngắn; không giữ transaction database trong suốt thời gian render hoặc tải file.

---

## 35. Capability registry và quản lý tích hợp bên ngoài

Mọi API, model server và encoder dùng adapter có phiên bản. Không hard-code giả định rằng dịch vụ luôn tồn tại hoặc luôn cung cấp cùng trường dữ liệu.

```json
{
  "schema_name": "provider_capability",
  "schema_version": 1,
  "provider_id": "youtube_data_api",
  "adapter_version": "1.0.0",
  "status": "available",
  "capabilities": [
    "search_metadata",
    "public_statistics"
  ],
  "unsupported_capabilities": [
    "audio_download",
    "license_guarantee"
  ],
  "auth_mode": "api_key",
  "quota_state": "available",
  "terms_reviewed_at_utc": "2026-07-14T18:00:00Z",
  "health_checked_at_utc": "2026-07-14T18:05:00Z"
}
```

### Quy tắc

- Kiểm tra capability trước khi bật nút hoặc tạo job.
- Không suy ra capability từ tên provider.
- Quota, rate limit và `Retry-After` do adapter quản lý.
- API phản hồi thiếu trường phải ghi `missing_metrics`, không gán số 0 nếu ý nghĩa là không có dữ liệu.
- API thay đổi schema phải làm adapter thất bại an toàn và giữ cache gần nhất còn hợp lệ.
- Lưu phiên bản API/adapter trong snapshot để diễn giải dữ liệu lịch sử.
- Tích hợp SoundCloud phải tuân thủ cơ chế xác thực hiện hành và không coi metadata là quyền tải hoặc tái sử dụng.
- Tích hợp YouTube phải dự trù quota cho từng loại lời gọi, cache kết quả và không quét nền khi ngân sách quota không đủ.
- Với AUTOMATIC1111, kiểm tra API động qua endpoint tài liệu/capability; không phụ thuộc tuyệt đối vào một wiki hoặc payload mặc định.

---

## 36. Quyền riêng tư, lưu giữ và xuất dữ liệu

- Mặc định xử lý local; tài nguyên chỉ gửi ra dịch vụ online khi người dùng chủ động chọn provider online.
- Trước lần gửi đầu tiên, giao diện phải nêu loại dữ liệu sẽ gửi: prompt, ảnh, audio, metadata hoặc định danh nguồn.
- Không gửi audio đầy đủ cho dịch vụ chỉ cần metadata hoặc vibe.
- Không thu thập telemetry nếu chưa có lựa chọn rõ ràng; telemetry nếu bật phải tối thiểu hóa và không chứa nội dung dự án.
- Có chức năng xuất toàn bộ project gồm metadata, cấu hình, rights evidence và manifest.
- Có chức năng xóa project; trước khi xóa hiển thị asset dùng chung và hậu quả.
- Chính sách lưu giữ cho log, cache, preview, file tạm và thùng rác phải cấu hình được.
- Mặc định không backup token. Nếu backup secret, dùng OS keystore hoặc khóa do người dùng quản lý, có key ID/rotation; khóa không nằm trong manifest.
- Không đưa dữ liệu cá nhân, token hoặc đường dẫn máy vào tên file đầu ra công khai.

---

## 37. Backup, khôi phục và kiểm tra thảm họa

- Backup định kỳ database, schema, cấu hình không bí mật, project metadata và rights evidence.
- Video, model và cache dung lượng lớn có thể dùng chính sách backup riêng.
- Backup database phải nhất quán giao dịch; không sao chép file SQLite đang ghi theo cách có thể tạo bản hỏng.
- Mỗi backup có manifest, hash, phiên bản schema và thời gian UTC.
- Phải kiểm thử khôi phục định kỳ vào thư mục tách biệt; backup chưa từng restore thử chỉ được xem là chưa xác minh.
- Khi restore, không ghi đè dự án đang tồn tại; tạo ID mới hoặc quy trình merge có kiểm soát.
- Có kịch bản kiểm thử: mất điện khi ghi JSON, worker chết khi render, ổ đĩa đầy khi nối, database hỏng, file segment mất và lock tồn dư.

---

## 38. Cập nhật, phụ thuộc và chuỗi cung ứng

- Pin phiên bản ứng dụng, thư viện, FFmpeg, model adapter và schema bằng lockfile hoặc manifest tương đương.
- Ghi nguồn tải và hash cho binary, model, SoundFont và dependency ngoài.
- Không tự cập nhật trong lúc có job chạy.
- Cập nhật phải có backup cấu hình/database, kiểm tra tương thích và phương án rollback.
- Dependency hoặc model không còn nguồn/giấy phép rõ phải chuyển `blocked` cho dự án mới; dự án cũ chỉ mở ở chế độ phù hợp với chính sách lưu trữ.
- Không tải và chạy script cài đặt từ URL không pin phiên bản hoặc không xác minh nguồn.
- Kiểm tra chữ ký số nếu nhà phát hành cung cấp; nếu không có, tối thiểu kiểm tra SHA-256 từ kênh tin cậy.
- Tách môi trường cho ứng dụng, Stable Diffusion, segmentation và công cụ phụ để giảm xung đột dependency.

---

## 39. Hiệu năng, giới hạn và khả năng đáp ứng

- Mỗi job khai báo ước tính RAM, VRAM, CPU, disk và thời gian trước khi xếp hàng nếu có thể.
- Đặt giới hạn cứng cho số project mở, số job chờ, dung lượng file tải, thời lượng audio và số segment.
- Giao diện không chờ đồng bộ tác vụ nặng; mọi thao tác trên 2 giây phải có trạng thái tiến độ hoặc thông báo đang xử lý.
- Progress phải dựa trên dữ liệu công cụ hoặc số đơn vị công việc; không tăng giả để tạo cảm giác đang chạy.
- Nếu không thể ước lượng phần trăm, hiển thị trạng thái không xác định cùng thời gian đã chạy.
- Có timeout mềm để cảnh báo và timeout cứng chỉ khi thao tác có thể dừng an toàn.
- Ghi benchmark theo profile máy mục tiêu nhưng không dùng một kết quả benchmark làm bảo đảm thời gian cho mọi máy.

---

## 40. Ma trận kiểm thử hoàn chỉnh

Ngoài kiểm thử module, phải có:

### 40.1. Unit test
- Schema, hash, invalidation, scoring, path validation và state transition.

### 40.2. Integration test
- SQLite + file publish tại mọi điểm crash trước/sau rename, fsync và commit.
- FFmpeg/FFprobe: CFR/VFR, timestamp monotonic, join video-only, mux audio master và verify sau mux.
- Worker + queue + cancellation, fencing token và stale-writer rejection.
- Adapter API với mock response, timeout, quota và schema thay đổi.

### 40.3. End-to-end test
- Audio local + ảnh local → asset video `verified`, output job `completed` và manifest `published`.
- Stable Diffusion → ảnh → fallback ảnh phẳng.
- Audio preview → duyệt → render phân đoạn → resume → manifest.

### 40.4. Fault-injection test
- Kill worker tại từng ranh giới transaction và giữa claim queue/ghi lease/publish asset.
- Mất mạng, hết quota, file tải thiếu, hash sai.
- CUDA out of memory, NVENC lỗi, disk full và permission denied.
- JSON hỏng, database locked, heartbeat hết hạn, stale writer dùng fencing token cũ và PID bị tái sử dụng.

### 40.5. Security test
- Path traversal, symlink escape, tên file độc hại và command injection.
- URL redirect, URL nội bộ, MIME giả và file nén/binary bất thường.
- Token redaction trong log, export và thông báo lỗi.

### 40.6. Regression và golden test
- Giữ bộ input nhỏ có quyền sử dụng rõ để kiểm tra output metadata, filter graph và continuity.
- Không yêu cầu video giống từng pixel giữa driver/GPU khác nhau; so sánh thuộc tính, hash của thành phần xác định và ngưỡng chất lượng.

### 40.7. Soak test
- Chạy nhiều project liên tiếp, render dài, pause/resume lặp và dọn cache để phát hiện rò bộ nhớ, lock kẹt và tăng dung lượng không kiểm soát.

Mỗi lỗi đã sửa phải có regression test trước khi đóng.

---

## 41. Cổng phát hành và vấn đề còn mở

### 41.0. Phân loại mức độ lỗi

- **Critical:** mất/hỏng dữ liệu không phục hồi, thực thi ngoài ý muốn, ghi ngoài vùng cho phép, lộ secret hoặc tự nâng quyền sử dụng.
- **High:** verifier báo đạt sai, Core không phục hồi, output hỏng hoặc mất nhất quán nghiêm trọng.
- **Medium:** có workaround an toàn, không mất dữ liệu, hoặc chỉ ảnh hưởng Enhanced/Optional.
- **Low:** lỗi trình bày/trải nghiệm không làm sai dữ liệu hay kết quả.

### 41.1. Release gate

Không phát hành nếu còn bất kỳ điều kiện nào:

- Có lỗi Critical hoặc High chưa xử lý.
- Core end-to-end chưa đạt trên cấu hình mục tiêu.
- Recovery test chưa đạt.
- Có khả năng ghi ra ngoài thư mục cho phép hoặc lộ token.
- Output verifier có thể báo đạt với file thiếu stream, sai timestamp/frame rate, sai thời lượng, hoặc chưa verify lại sau join/mux.
- Migration chưa có rollback/backup hoặc chưa kiểm thử schema downgrade/read-only fallback.
- License/rights state có thể bị tự động nâng thành hợp lệ khi thiếu bằng chứng, hoặc quyền đã thu hồi nhưng dependency vẫn được tái sử dụng.

Lỗi Medium có thể phát hành chỉ khi có workaround, không gây mất dữ liệu, được ghi rõ và người dùng chấp nhận. Lỗi Low được đưa vào backlog.

### 41.2. Definition of Done cho mỗi yêu cầu

- Yêu cầu có mã và tiêu chí nghiệm thu.
- Có implementation và review.
- Có test phù hợp; test đạt.
- Có log/telemetry tối thiểu cần thiết nhưng không lộ dữ liệu.
- Có tài liệu cấu hình và hành vi fallback.
- Có migration nếu thay đổi schema.
- Đã kiểm tra hủy, retry, phục hồi và invalidation nếu liên quan.

### 41.3. Vấn đề phải chốt trước khi code module tương ứng

- Ngưỡng cụ thể cho silence, clipping, halo, black-frame và scene discontinuity.
- Danh sách provider online thực tế được phép bật khi phát hành.
- Giới hạn tải file và thời lượng dự án mặc định.
- Chu kỳ heartbeat, lease, retention và backup.
- Profile encoder, bitrate/rate-control và dung lượng mục tiêu.
- Bộ model/SoundFont/ambience ban đầu cùng giấy phép và hash.

Các giá trị này phải nằm trong cấu hình phiên bản hóa và được xác nhận bằng benchmark/kiểm thử, không tự chọn tùy tiện trong lúc code.
