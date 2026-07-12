# tạo video short 🎬

Công cụ AI **tự động cắt 1 video dài thành nhiều clip ngắn có phụ đề** (dọc 9:16 cho Shorts/Reels/TikTok). Chạy **local** trên máy bạn — không tốn phí server, không giới hạn số video.

Dán link YouTube → AI bóc lời → chọn đoạn hay → cắt clip + tự tạo phụ đề → tải về.

---

## Cần chuẩn bị (chỉ 1 lần)

1. **Python 3.14** — máy bạn đã có ✅
2. **ffmpeg** — máy bạn đã có ✅ (dùng để cắt/ghép video)
3. Kết nối mạng (để tải video và, lần đầu, tải model bóc lời)

> 💡 Có card đồ họa (GPU) NVIDIA thì **bóc lời VÀ dựng clip (NVENC)** đều nhanh hơn nhiều. Không có cũng chạy được (chậm hơn). Tất cả tự dò, không cần chỉnh gì.

## Cách chạy (dễ nhất)

**Double-click file `run.bat`.**

Lần đầu sẽ hơi lâu (tự cài thư viện + tải model). Xong, trình duyệt tự mở tại:

```
http://127.0.0.1:8000
```

Dán link video, chọn số clip, bấm **Bắt đầu cắt**. Chờ xử lý xong là có clip để xem & tải.

Muốn tắt: đóng cửa sổ đen (hoặc bấm `Ctrl + C`).

## Chạy bằng dòng lệnh (nếu thích)

```powershell
py -3.14 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --port 8000
```

---

## Đang làm được gì

- ✅ Tải video YouTube + **upload file từ máy**
- ✅ Bóc lời tự động (Whisper, hỗ trợ tiếng Việt)
- ✅ **AI đọc bản bóc lời chọn đoạn viral** — hook 3 giây làm cổng lọc, chấm điểm viral 0-10, phân loại clip (mẹo/quan điểm ngược/quote đắt/nỗi đau…); tự lùi về chấm điểm heuristic khi không có AI
- ✅ Cắt clip dọc 9:16 nền mờ + **phụ đề động** (chữ đổi màu theo lời nói)
- ✅ **Chèn logo + nhạc nền** (tuỳ chọn)
- ✅ **AI tự viết tiêu đề + caption + hashtag** đúng giọng thương hiệu (kèm nút Copy caption)
- ✅ Xem & tải clip ngay trên web

## Sẽ làm tiếp (theo roadmap)

- Ghép intro/outro CTA + xuất sang CapCut
- Đẩy lên Lark Base + lên lịch đăng
- Tự động đăng đa nền tảng (bước khó nhất, để sau)

---

## Cấu trúc thư mục

```
tao_video_short/
├── run.bat                  # double-click để chạy
├── requirements.txt         # thư viện Python
├── app/
│   ├── main.py              # máy chủ web (FastAPI)
│   ├── config.py            # cấu hình (model, đường dẫn, tham số cắt)
│   └── pipeline/            # các bước xử lý
│       ├── downloader.py        # tải video (yt-dlp)
│       ├── transcriber.py       # bóc lời (faster-whisper)
│       ├── segmenter.py         # cắt ứng viên clip
│       ├── scorer.py            # chấm điểm viral
│       ├── subtitle_builder.py  # dựng phụ đề .ass
│       ├── clipper.py           # ghép clip dọc + phụ đề (ffmpeg)
│       └── orchestrator.py      # nối tất cả các bước
├── web/                     # giao diện (HTML/CSS/JS)
├── output/                  # clip xuất ra (tự tạo)
└── work/                    # file tạm (tự tạo)
```

## Chỉnh nhanh

Đổi model bóc lời cho nhanh/chậm: sao chép `.env.example` thành `.env`, sửa `WHISPER_MODEL`
(ví dụ `tiny` cho nhanh, `medium` cho chính xác hơn).

Dựng clip mặc định dùng **NVENC (GPU)** nếu có, tự lùi CPU nếu không — không cần chỉnh. Muốn ép CPU: đặt `VIDEO_ENCODER=cpu` trong `.env`.
