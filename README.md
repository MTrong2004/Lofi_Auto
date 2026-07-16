# 🎵 Lofi Studio AI — Tự động hoá tạo video Lofi

> **Bộ công cụ tự động hoàn chỉnh** để tạo video Lofi chất lượng cao: từ tìm kiếm nhạc bản quyền tự do → sinh ảnh nền AI → dựng video với hiệu ứng → tải lên YouTube, tất cả điều khiển qua một Dashboard trực quan.

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue?style=flat-square&logo=python)](https://python.org)
[![Streamlit](https://img.shields.io/badge/Dashboard-Streamlit-ff4b4b?style=flat-square&logo=streamlit)](https://streamlit.io)
[![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)](LICENSE)

---

## 📋 Mục lục

- [Tính năng nổi bật](#-tính-năng-nổi-bật)
- [Yêu cầu hệ thống](#-yêu-cầu-hệ-thống)
- [Cài đặt & Khởi chạy nhanh](#-cài-đặt--khởi-chạy-nhanh)
- [Cấu trúc dự án](#-cấu-trúc-dự-án)
- [Hướng dẫn sử dụng Dashboard](#-hướng-dẫn-sử-dụng-dashboard)
- [Cài đặt Stable Diffusion Local](#-cài-đặt-stable-diffusion-local)
- [Cấu hình nâng cao](#-cấu-hình-nâng-cao)
- [Kiến trúc kỹ thuật](#-kiến-trúc-kỹ-thuật)
- [Bộ kiểm thử tự động](#-bộ-kiểm-thử-tự-động)

---

## ✨ Tính năng nổi bật

| Tính năng | Mô tả |
|---|---|
| 🎵 **Tìm nhạc đa nguồn** | Duyệt song song 4 nguồn whitelist (SoundCloud NCS/free + YouTube NCS/free) qua `yt-dlp`, tự khử trùng lặp, lọc mix dài & label thương mại |
| ▶️ **Nghe thử không cần tải** | Phát stream trực tiếp trong Dashboard qua `yt-dlp -g`, không tải file về máy |
| 🎨 **Sinh ảnh nền AI (5 nguồn)** | Pollinations AI, AI Horde, Hugging Face, Cloudflare Workers AI, Stable Diffusion Local — thứ tự ưu tiên cấu hình được qua `.env` |
| ✍️ **LLM viết prompt theo bài nhạc** | Mỗi lần bấm sinh một bối cảnh khác cùng mood (miễn phí qua Pollinations, hoặc cắm key Groq/OpenRouter/Gemini) |
| 🏔️ **Parallax 2.5D theo độ sâu thật** | Tách 3 lớp bằng Depth Anything V2 (onnxruntime CPU, ~1s/ảnh); chuyển động camera-pan vật lý (lớp gần dịch nhiều) + zoom Ken Burns chậm, nối segment liền mạch |
| 🍃 **Chuyển động sống theo từng lớp** | Tự nhận diện từ prompt: mây hậu cảnh trôi chậm, đèn thành phố nhấp nháy nhè nhẹ — trung cảnh giữ yên |
| 🌿 **Lá cây lay riêng từng vùng** | Segmentation thực vật (SegFormer ONNX ~4MB, CPU) + warp displace FFmpeg với sóng sin lệch pha — từng cụm lá lay nhịp riêng như gió thổi, vật thể khác đứng yên |
| ✨ **Hiệu ứng sinh bằng code** | Mưa rơi/tuyết rơi/bụi bay có quỹ đạo thật + scanline + film grain, lặp khít (seamless loop), không cần tải footage |
| 🟢 **Chroma key phông xanh thật** | Tự nhận diện video phông xanh/nền đen/alpha (phân tích khung hình local), tách nền bằng `chromakey + despill + feather` trong FFmpeg và WebGL shader trong Live Preview — không còn viền xanh như blend screen |
| 🧩 **Filter builder dùng chung** | `core/effect_compositor.py` sinh một chuỗi filter duy nhất cho cả preview 10 giây lẫn render cuối — chỉnh opacity/tốc độ/chroma ở đâu thì mọi nơi khớp nhau |
| 🤖 **AI đề xuất hiệu ứng local-first** | AI tạo hồ sơ (effect_type + query đúng loại asset), xếp hạng thư viện local trước, thiếu mới gọi Pixabay — tiết kiệm quota, chạy được offline |
| 🎧 **Chất âm lofi đặc trưng** | Slowed 0.88x + lowpass ấm + compressor; tiếng mưa & vinyl crackle tự sinh bằng code |
| 🎬 **Dựng video GPU/CPU tự động** | Tự dò GPU bằng test encode NVENC thật: máy có GPU dùng `h264_nvenc`, không có tự chuyển `libx264` — không cần cấu hình tay |
| ☁️ **Upload YouTube + AI caption** | Bước 6 của wizard: AI viết title/description/hashtag (chỉnh tay được), chọn chế độ đăng/lịch đăng, upload resumable có tiến độ |
| 🛡️ **Kiểm soát bản quyền** | Hệ thống schema & kiểm duyệt quyền tác giả trước khi xuất bản |
| 📊 **Dashboard trực quan** | Giao diện Streamlit wizard 6 bước, điều hướng có điều kiện, preview trực tiếp |
| 🤖 **Quản lý SD Local** | Trỏ đến AUTOMATIC1111 có sẵn **hoặc** để App tự tải & cài đặt — xem [hướng dẫn bên dưới](#-cài-đặt-stable-diffusion-local) |
| ✅ **Bộ kiểm thử đầy đủ** | 13 unit test tự động bao phủ DB, lock, scheduler, render, SD gates, audio vibe, upscale, parallax |

---

## 💻 Yêu cầu hệ thống

| Thành phần | Tối thiểu | Khuyến nghị |
|---|---|---|
| **OS** | Windows 10 64-bit | Windows 11 64-bit |
| **Python** | 3.10+ | 3.11+ |
| **RAM** | 8 GB | 16 GB |
| **GPU (tuỳ chọn)** | NVIDIA 4GB VRAM | NVIDIA RTX 3050 Ti+ |
| **Ổ cứng trống** | 5 GB | 15 GB (nếu cài SD Local) |
| **FFmpeg** | Bắt buộc | Bắt buộc |
| **Git** | Cần nếu cài SD Auto | Cần nếu cài SD Auto |

> **Lưu ý:** App chạy hoàn toàn offline sau khi cài đặt. Kết nối Internet chỉ cần cho bước tìm nhạc và tải ảnh từ nguồn trực tuyến.

---

## 🚀 Cài đặt & Khởi chạy nhanh

### Bước 1: Clone repository

```bash
git clone https://github.com/MTrong2004/Lofi_Auto.git
cd Lofi_Auto
```

### Bước 2: Cài đặt dependencies

```bash
pip install -r requirements.txt
```

> **Đảm bảo FFmpeg đã được cài đặt và có trong PATH:**
> Tải từ https://ffmpeg.org/download.html hoặc dùng `winget install ffmpeg`

> **Lưu ý:** Lần đầu render với Parallax, app tự tải 2 model ONNX từ HuggingFace
> về `data/models/` (chỉ tải một lần): Depth Anything V2 Small (~25MB, tách lớp
> theo độ sâu) và SegFormer-B0 ADE20K (~4.4MB, mask vùng cây lá cho hiệu ứng lay).

### Bước 3: Khởi chạy Dashboard

```bash
python -m streamlit run review_app.py
```

Dashboard tự động mở trên trình duyệt tại `http://localhost:8501`.

### (Tuỳ chọn) Chạy qua dòng lệnh

```bash
# Kiểm tra hệ thống nhanh
python system_check.py

# Chạy pipeline test 10 giây
python main.py --test

# Chạy pipeline đầy đủ
python main.py
```

---

## 📁 Cấu trúc dự án

```
lofi_automation/
├── 📄 main.py                    # Pipeline orchestrator chính
├── 📄 app_server.py              # Server API FastAPI (REST backend)
├── 📄 config.py                  # Cấu hình toàn cục (paths, API keys)
├── 📄 system_check.py            # Kiểm tra phần cứng & dependencies
├── 📄 test_suite.py              # Bộ 13 unit test tự động
├── 📄 requirements.txt           # Danh sách dependencies
│
├── 📄 .env.example               # Mẫu cấu hình môi trường (chép thành .env)
│
├── 🎵 step1_music_hunter.py      # Tìm nhạc đa nguồn (SoundCloud + YouTube) & stream preview
├── 🎨 step2_image_provider.py    # Sinh ảnh nền AI (5 provider)
├── ✨ step3_effect_provider.py   # Thư viện hiệu ứng: builtin pack, Pixabay, manifest, đề xuất local-first
├── 🖥️  review_app.py              # Streamlit Dashboard chính (wizard 6 bước)
├── 🎬 step4_render.py            # Engine dựng video FFmpeg (encoder auto GPU/CPU)
├── ☁️  step5_uploader.py          # Tải video lên YouTube (resumable, tiến độ, lịch đăng)
│
├── utils/
│   └── 📄 helpers.py             # Các hàm bổ trợ (LLM prompt, metadata, retry)
│
├── components/
│   └── effect_live_preview/     # Live Preview trình duyệt: CSS blend + WebGL chroma key, xem matte
│
└── core/                        # Các module lõi
    ├── 📄 db.py                  # SQLite database & migrations
    ├── 📄 schemas.py             # Schema validation (12 schemas)
    ├── 📄 project_manager.py     # Quản lý vòng đời Project
    ├── 📄 lock_manager.py        # Distributed locking & fencing tokens
    ├── 📄 resource_scheduler.py  # GPU/CPU job scheduler
    ├── 📄 render_worker.py       # Worker render phân đoạn video
    ├── 📄 audio_processor.py     # Chất âm lofi, LUFS, loop crossfade, ambience code
    ├── 📄 effect_compositor.py   # Filter builder FFmpeg dùng chung (chroma key, despill, feather, blend)
    ├── 📄 effect_analyzer.py     # Tự nhận diện loại nền hiệu ứng (phông xanh/nền đen/alpha)
    ├── 📄 effect_recommender.py  # Hồ sơ AI + xếp hạng local-first cho hiệu ứng
    ├── 📄 effect_manifest.py     # Manifest thư viện hiệu ứng (nguồn, license, effect_type)
    ├── 📄 caption_writer.py      # AI viết title/description/hashtag cho upload YouTube
    ├── 📄 depth_estimator.py     # Depth Anything V2 (onnxruntime) cho parallax
    ├── 📄 vegetation_masker.py   # SegFormer ADE20K - mask vùng cây lá cho warp
    ├── 📄 parallax_processor.py  # Tách lớp theo depth, sway maps & filter parallax FFmpeg
    ├── 📄 image_upscaler.py      # Upscale ảnh nền
    ├── 📄 cache_manager.py       # SHA-256 cache & dedup
    ├── 📄 media_probe.py         # FFprobe audio/video analysis
    ├── 📄 output_verifier.py     # Xác minh video đầu ra & manifest
    ├── 📄 provider_capability.py # Registry khả năng image providers
    └── 📄 sd_manager.py          # Quản lý Stable Diffusion Local WebUI (adapter, health, installer, process, model)
```

---

## 🖥️ Hướng dẫn sử dụng Dashboard & API

Dự án cung cấp hai giao diện chính để thao tác và kiểm soát:

### 1. Dashboard Trực quan (Khuyến nghị)
Giao diện **Streamlit** (tích hợp wizard 6 bước) giúp người dùng thực hiện toàn bộ quy trình: cấu hình, duyệt/tải nhạc, sinh ảnh AI, chọn hiệu ứng, render và upload YouTube.

Khởi chạy bằng lệnh:
```bash
python -m streamlit run review_app.py
```
Mở trình duyệt truy cập: **`http://localhost:8501`**

#### Các bước thực hiện trên Dashboard:
- **Bước 1 — ⚙️ Kiểm tra hệ thống**: Kiểm tra FFmpeg/CPU/GPU, chọn AI Image Provider (Pollinations / AI Horde / Hugging Face / SD Local) và quản lý SD WebUI.
- **Bước 2 — 🎵 Chọn nhạc**: Lọc theo danh mục hoặc tìm tự do, nghe thử trực tuyến không cần tải, xem bản quyền và chọn nhạc.
- **Bước 3 — 🎨 Tạo ảnh nền**: Nhập prompt mô tả (hoặc sinh prompt bằng LLM theo bài nhạc) và tạo ảnh nền.
- **Bước 4 — ✨ Chọn hiệu ứng**: Giao diện 3 tab:
  - **Đề xuất** — AI phân tích nhạc/ảnh, xếp hạng thư viện local trước, thiếu mới tìm Pixabay; ứng viên hiển thị dạng card (thumbnail, điểm AI, thời lượng, nguồn, license).
  - **Điều chỉnh** — Loại hiệu ứng (tự nhận diện phông xanh/nền đen/alpha), opacity, tốc độ, chế độ hòa trộn và bộ thông số chroma key (màu phông, similarity, softness, despill, feather, xem matte).
  - **Thư viện** — Chọn hiệu ứng local, tìm Pixabay thủ công, quản lý manifest và phân tích cảnh (nâng cao).
  Live Preview chạy WebGL chroma key ngay trong trình duyệt; nút "Preview FFmpeg 10 giây" dùng đúng filter của render cuối.
- **Bước 5 — 🚀 Render video**: Encoder "Tự động" dò GPU thật (có NVENC dùng GPU, không có chuyển CPU libx264), theo dõi tiến độ/ETA.
- **Bước 6 — 📤 Upload YouTube**: Bấm "Tạo caption + hashtag bằng AI" (sửa tay được), chọn chế độ đăng (riêng tư + lịch tự động/chọn giờ, unlisted, công khai) rồi upload có thanh tiến độ.

> **Điều kiện upload YouTube:** cài thư viện `pip install google-auth-oauthlib google-api-python-client` và đặt file OAuth `client_secret.json` (Google Cloud Console → bật YouTube Data API v3 → tạo OAuth client Desktop) vào thư mục `secrets/`. Lần upload đầu tiên sẽ mở trình duyệt để đăng nhập.

### 2. Backend REST API
Hệ thống cung cấp một REST API (FastAPI) để tích hợp với các công cụ tự động hóa hoặc giao diện bên ngoài.

Khởi chạy backend API bằng lệnh:
```bash
python app_server.py
```
Xem tài liệu API (Swagger UI) và test trực tuyến tại: **`http://127.0.0.1:8000/docs`**

---

## 🤖 Cài đặt Stable Diffusion Local

Dashboard cung cấp **hai chế độ** để tích hợp AUTOMATIC1111 WebUI vào pipeline tạo ảnh:

### Chế độ 1 — 📁 Trỏ đến bản đã cài sẵn (Khuyến nghị)

Nếu bạn **đã cài AUTOMATIC1111** trên máy, chọn chế độ này:

1. Trong Tab 1, chọn **Stable Diffusion Local** làm nhà cung cấp ảnh
2. Cuộn xuống phần **🛠️ Trình quản lý Stable Diffusion**
3. Chọn radio **"📁 Trỏ đến bản đã cài"**
4. Nhập đường dẫn thư mục gốc AUTOMATIC1111 (ví dụ: `D:/stable-diffusion-webui`)
5. App tự động phát hiện `webui-user.bat` / `launch.py` và xác nhận
6. Bấm **💾 Lưu đường dẫn & Áp dụng**
7. Bật API flag trong `webui-user.bat`:
   ```batch
   set COMMANDLINE_ARGS=--api --medvram
   ```
8. Khởi động trực tiếp bằng cách bấm nút **🟢 Bật Stable Diffusion** ngay trên giao diện (hoặc khởi chạy thủ công) → Bấm **🔗 Kiểm tra kết nối**

> **App không thay đổi bất kỳ file nào** trong thư mục cài đặt của bạn.

### Chế độ 2 — 🚀 Để App tự động tải & cài đặt

Nếu bạn **chưa có AUTOMATIC1111**, App sẽ cài đặt hoàn toàn tự động:

1. Chọn radio **"🚀 Để App tự động tải & cài đặt"**
2. Nhập đường dẫn thư mục đích (cần ≥10GB trống)
3. Bấm **🩺 Kiểm tra phần cứng** để xác minh điều kiện
4. Bấm **🚀 Bắt đầu tải & cài đặt tự động**
5. App sẽ tự động:
   - Chạy kiểm tra phần cứng (OS, GPU/VRAM, RAM, Disk, Port, Python/Git)
   - Clone AUTOMATIC1111 v1.6.0 và tạo Python Virtual Environment trong thư mục Staging cô lập
   - Cài PyTorch CUDA + các thư viện cần thiết
   - Quét và vô hiệu hóa các extension không nằm trong allowlist đã phê duyệt
   - Swapping/Promoting atomic từ staging sang active (và Rollback tự động phục hồi bản cũ nếu có lỗi)
6. Sau khi cài xong, dùng **🎛️ Bảng điều khiển Server** để Start/Stop

> **An toàn & Bảo mật:** App chỉ cài vào thư mục bạn chỉ định, tự động cô lập staging/rollback, kiểm duyệt extension an toàn và chỉ lắng nghe trên cổng loopback 127.0.0.1.

---

## ⚙️ Cấu hình nâng cao

Cấu hình qua file **`.env`** (chép từ [`.env.example`](.env.example), đã nằm trong `.gitignore`):

```bash
# Thứ tự ưu tiên provider ảnh
# Máy KHÔNG có GPU:  pollinations,aihorde,sdlocal
# Máy CÓ GPU:        sdlocal,pollinations,aihorde
IMAGE_PROVIDER_ORDER=pollinations,aihorde,sdlocal

# Stable Diffusion Local (xem hướng dẫn bên dưới để cài trên máy GPU)
SD_LOCAL_API_URL=http://127.0.0.1:7860
SD_LOCAL_CHECKPOINT=meinamix_v12Final.safetensors
SD_LOCAL_WIDTH=1024
SD_LOCAL_HEIGHT=576
SD_LOCAL_STEPS=28
SD_LOCAL_CFG_SCALE=7
SD_LOCAL_SAMPLER=DPM++ 2M Karras

# API keys tùy chọn (provider nào thiếu key sẽ tự bị bỏ qua)
# AI_HORDE_API_KEY=          # stablehorde.net - ưu tiên hàng đợi
# HUGGINGFACE_TOKEN=         # huggingface.co/settings/tokens
# CLOUDFLARE_ACCOUNT_ID=     # dash.cloudflare.com -> Workers AI
# CLOUDFLARE_API_TOKEN=
# PEXELS_API_KEY=            # tải video hiệu ứng overlay
# POLLINATIONS_API_KEY=

# LLM viết prompt ảnh theo bài nhạc (mặc định Pollinations miễn phí, không cần key)
# Cùng endpoint này được dùng cho AI đề xuất hiệu ứng và AI viết caption upload.
# PROMPT_API_URL=https://text.pollinations.ai/openai
# PROMPT_API_KEY=            # key Groq / OpenRouter / Gemini... nếu muốn
# PROMPT_API_MODEL=openai

# Thư viện hiệu ứng online (tùy chọn - có key mới tìm được video Pixabay)
# PIXABAY_API_KEY=           # pixabay.com/api/docs - key hiển thị "Đã kết nối" trong UI

# Encoder video: mặc định không cần đặt - app tự dò GPU bằng test encode.
# Chỉ đặt khi muốn ép cứng: NVENC_CODEC=h264_nvenc (hoặc chọn "CPU ổn định" trong UI)
```

Các mặc định khác (prompt ảnh, negative prompt, tempo lofi, bitrate video...)
nằm trong [`config.py`](config.py) và đều đọc được từ biến môi trường cùng tên.

### Ghi chú chất lượng ảnh

- Prompt mặc định mô tả tường minh **3 tầng cảnh** (tiền/trung/hậu cảnh) để phục vụ hiệu ứng Parallax 2.5D.
- Negative prompt chặn ảnh tả thực (`photorealistic, photo, 3d render...`) — đầu ra luôn là tranh vẽ/anime.
- Payload SD dùng sampler DPM++ 2M Karras, CFG 7, Clip skip 2 (chuẩn checkpoint anime SD 1.5).

### Hiệu ứng overlay & Chroma key

- **4 loại hiệu ứng** (`effect_type`): `screen_black` (overlay nền đen, xóa vùng gần đen bằng colorkey), `chroma_key` (phông xanh, tách nền bằng `chromakey → despill → feather alpha → overlay`), `alpha` (video có kênh alpha) và `normal` (ghép đè thường).
- Sau khi tải/quét, `core/effect_analyzer.py` lấy mẫu ~5 khung hình, phân tích màu vùng biên và tự ghi `effect_type`, màu nền phát hiện được và bộ thông số đề xuất vào `data/effects/manifest.json` — chạy hoàn toàn local, không gọi AI.
- Preset chroma mặc định: key `#00FF00`, similarity `0.18`, softness `0.08`, despill `0.35`, feather `1.5px` — chỉnh được theo từng video trong tab **Điều chỉnh**.
- Preview FFmpeg 10 giây và render cuối dùng **cùng một filter builder** (`core/effect_compositor.py`); cache preview chứa toàn bộ thông số compositing nên đổi bất kỳ thông số nào preview cũ tự vô hiệu.
- Live Preview trình duyệt dùng WebGL shader cho chroma key (CSS blend không tách được phông xanh), có chế độ **Xem matte** và 2 mức chất lượng (640×360 / 960×540). Feather viền chỉ áp ở phía FFmpeg.

### Encoder GPU/CPU tự động

- `step4_render.detect_best_encoder()` chạy một lần encode thử NVENC thật (không tin danh sách `-encoders` vì FFmpeg build kèm NVENC cả trên máy không có GPU).
- Máy có GPU NVIDIA → `h264_nvenc`; không có → `libx264`. Kết quả cache theo tiến trình; NVENC hỏng giữa chừng thì các segment sau tự chuyển CPU.
- UI bước Render có 3 lựa chọn: **Tự động** (khuyên dùng), **GPU NVENC**, **CPU ổn định**.

### Tăng tốc render mà không giảm chất lượng

Renderer hiện giữ nguyên độ phân giải 1920×1080, FPS, bitrate và filter khi dùng GPU. Để tối ưu tốc độ mà không thay đổi chất lượng đầu ra:

- Chọn **Tự động** hoặc **GPU NVENC** trên màn hình Render. Chế độ tự động kiểm tra một lần encode NVENC thực tế rồi chỉ dùng GPU khi encoder hoạt động được.
- Dùng SSD cho thư mục dự án và thư mục output. Render phân đoạn tạo các file trung gian trước khi ghép và mux audio; ổ đĩa chậm có thể trở thành nút thắt.
- Giữ mux video ở chế độ stream copy. Pipeline đã ưu tiên `-c:v copy` khi ghép audio; chỉ encode video lại bằng CPU khi stream copy thất bại.
- Theo dõi mức sử dụng CPU, GPU và VRAM khi render. NVENC tăng tốc khâu encode, nhưng các filter như scale, rotate, chroma key và ASS subtitle vẫn có thể chạy trên CPU.

> Không nên giảm `VIDEO_FPS`, độ phân giải hoặc đổi scale `lanczos` sang filter nhẹ hơn nếu mục tiêu là giữ nguyên chất lượng. Các thay đổi này có thể nhanh hơn nhưng là đánh đổi chất lượng.

> Hướng nâng cấp tiếp theo là render các segment song song với số worker giới hạn (CPU: thường 2–4; NVENC: thường 1–2) và cấu hình `filter_threads`. Đây là tối ưu kiến trúc chưa được bật trong renderer hiện tại, nên cần benchmark theo CPU, VRAM và tốc độ SSD trước khi áp dụng.

---

## 🏗️ Kiến trúc kỹ thuật

```mermaid
graph TD
    UI[Streamlit Dashboard<br/>review_app.py - wizard 6 bước] --> S1[Step 1: Music Hunter<br/>yt-dlp đa nguồn + stream preview]
    UI --> S2[Step 2: Image Provider<br/>5 backends]
    UI --> S3[Step 3: Effect Provider<br/>builtin + Pixabay + manifest]
    UI --> S4[Step 4: Render Engine<br/>FFmpeg + auto GPU/CPU]
    UI --> S5[Step 5: Uploader<br/>YouTube API + AI caption]

    S2 --> P1[Pollinations AI]
    S2 --> P2[AI Horde]
    S2 --> P3[HuggingFace]
    S2 --> P5[Cloudflare Workers AI]
    S2 --> P4[SD Local]

    P4 --> SDA[SD Adapter<br/>OpenAPI discover]
    SDA --> SDH[SD Health Checker<br/>256x256 test]

    S3 --> ER[Effect Recommender<br/>hồ sơ AI + local-first]
    S3 --> EA[Effect Analyzer<br/>nhận diện phông xanh/nền đen]
    S3 --> LP[Live Preview<br/>WebGL chroma key]

    S4 --> EC[Effect Compositor<br/>chromakey + despill + feather<br/>dùng chung preview và render]
    S4 --> PX[Parallax Processor<br/>3 lớp theo depth]
    PX --> DE[Depth Estimator<br/>Depth Anything V2 ONNX]
    PX --> VM[Vegetation Masker<br/>SegFormer + sway warp lá cây]
    S4 --> AP[Audio Processor<br/>lofi character + ambience]

    S5 --> CW[Caption Writer<br/>AI title/hashtag + fallback]

    UI --> C[Core Platform]
    C --> DB[(SQLite DB)]
    C --> LM[Lock Manager<br/>Fencing Tokens]
    C --> RS[Resource Scheduler<br/>GPU/CPU Jobs]
    C --> PM[Project Manager<br/>State Machine]
    C --> OV[Output Verifier<br/>Manifest + Hash]
```

### Cơ chế bảo vệ dữ liệu

- **Atomic Write:** Mọi file metadata đều được ghi nguyên tử (`write → fsync → os.replace`)
- **Fencing Tokens:** Ngăn race condition khi nhiều worker cùng truy cập tài nguyên
- **Exclusive Model Lease:** Ngăn xung đột checkpoint SD khi nhiều job GPU chạy đồng thời
- **Schema Validation:** 12 schemas kiểm tra toàn vẹn dữ liệu trước khi ghi DB

---

## 🧪 Bộ kiểm thử tự động

Chạy toàn bộ 13 unit test:

```bash
python -m unittest test_suite
```

| Test | Phạm vi kiểm thử |
|---|---|
| `test_01` | Kiểm tra bảng và schema SQLite |
| `test_02` | Atomic file writer & fsync |
| `test_03` | Lock Manager & Fencing Token |
| `test_04` | Resource Scheduler job lifecycle |
| `test_05` | Worker process tree termination |
| `test_06` | Output Verifier & Manifest |
| `test_07` | **SD Gates G3/G4** — SDAdapter, SDModel, Health Check (mocked) |
| `test_08` | **SD Installer Preflight** — Giả lập phần cứng & điều kiện |
| `test_09` | **SD Installer Staging/Rollback** — Quy trình promote & rollback tự động |
| `test_10` | **SD Installer Extension Allowlist** — Lọc và vô hiệu hóa extension lạ |
| `test_11` | **Audio chuẩn hóa & Vibe** — LUFS, mix ambience, preview 3 vibe |
| `test_12` | **Image Upscale fallback** — Phóng ảnh khi thiếu SD API |
| `test_13` | **Parallax rendering** — Tách lớp (depth/geometric) & filter complex |

---

## 📜 Giấy phép

Dự án phát hành dưới giấy phép **MIT License**.  
Âm nhạc được sử dụng phải tuân thủ giấy phép của nguồn gốc (CC, SoundCloud terms...).

---

## 👤 Tác giả

**MTrong2004** — [GitHub](https://github.com/MTrong2004/Lofi_Auto)

> *Được phát triển với sự hỗ trợ của Antigravity AI (Google DeepMind)*
