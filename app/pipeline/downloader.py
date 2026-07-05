"""Tải video từ YouTube (hoặc link hỗ trợ bởi yt-dlp) về thư mục work/.

Trả về đường dẫn file mp4 + tiêu đề video. Dùng yt-dlp như thư viện Python
để kiểm soát tiến trình tốt hơn gọi lệnh ngoài.
"""
from pathlib import Path
from .. import config


def download_video(url: str, on_progress=None) -> tuple[Path, str]:
    """Tải video về work/. Trả về (đường_dẫn_mp4, tiêu_đề)."""
    from yt_dlp import YoutubeDL

    out_tmpl = str(config.WORK_DIR / "%(id)s.%(ext)s")

    def _hook(d):
        if on_progress and d.get("status") == "downloading":
            pct = d.get("_percent_str", "").strip()
            on_progress(f"Đang tải video… {pct}")

    ydl_opts = {
        # Ưu tiên mp4 progressive <=720p: 1 file (có sẵn tiếng), tải nhanh,
        # không cần ghép, né lỗi thiếu JS runtime của YouTube.
        "format": "best[height<=720][ext=mp4]/best[ext=mp4]/best",
        "merge_output_format": "mp4",
        "outtmpl": out_tmpl,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 30,
        "retries": 3,
        "progress_hooks": [_hook],
    }

    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        title = info.get("title", "video")
        # Đường dẫn thực tế sau khi merge
        video_id = info.get("id", "video")

    # Tìm file đã tải (đuôi có thể là mp4/mkv/webm tuỳ nguồn)
    candidates = sorted(config.WORK_DIR.glob(f"{video_id}.*"))
    video_files = [p for p in candidates if p.suffix.lower() in (".mp4", ".mkv", ".webm")]
    if not video_files:
        raise RuntimeError("Không tìm thấy file video sau khi tải.")
    return video_files[0], title
