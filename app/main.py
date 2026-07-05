"""Máy chủ web (FastAPI): phục vụ giao diện + 1 API xử lý video chạy nền.

/api/process nhận form-data: link HOẶC file video, kèm tuỳ chọn logo + nhạc nền.
Job chạy trong thread riêng; giao diện hỏi thăm (poll) tiến độ định kỳ.
"""
from pathlib import Path
import os
import re
import shutil
import threading
import traceback
import uuid

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .pipeline.orchestrator import run_job
from .pipeline.scorer import extract_keywords

app = FastAPI(title="tao_video_short")

_jobs: dict[str, dict] = {}
_lock = threading.Lock()


def _update(job_id: str, **fields):
    with _lock:
        if job_id in _jobs:
            _jobs[job_id].update(fields)


def _new_job(message: str) -> str:
    job_id = uuid.uuid4().hex[:8]
    with _lock:
        _jobs[job_id] = {
            "status": "queued", "percent": 0,
            "message": message, "result": None, "error": None,
        }
    return job_id


def _save_upload(upload: UploadFile, prefix: str) -> Path:
    """Lưu 1 file upload vào work/ với tên an toàn. Trả về đường dẫn."""
    safe = "".join(c for c in (upload.filename or prefix)
                   if c.isalnum() or c in "._-") or prefix
    dest = config.WORK_DIR / f"{prefix}_{uuid.uuid4().hex[:6]}_{safe}"
    with open(dest, "wb") as out:
        shutil.copyfileobj(upload.file, out)
    return dest


def _worker(job_id: str, source: str, num_clips: int, language: str | None,
            is_upload: bool, logo_path: Path | None, music_path: Path | None,
            max_minutes: int, reframe_faces: bool,
            keywords: list, hook_types: set, ref_face_path: Path | None,
            use_ai: bool, criteria_text: str,
            clip_min: int, clip_max: int, prefer_repeat: bool):
    def progress(message: str, percent: int | None = None):
        fields = {"message": message}
        if percent is not None:
            fields["percent"] = percent
        _update(job_id, **fields)

    try:
        _update(job_id, status="running")
        result = run_job(source, num_clips, language, progress,
                         is_upload=is_upload, logo_path=logo_path,
                         music_path=music_path, max_minutes=max_minutes,
                         reframe_faces=reframe_faces,
                         keywords=keywords, hook_types=hook_types,
                         ref_face_path=ref_face_path,
                         use_ai=use_ai, criteria_text=criteria_text,
                         clip_min=clip_min, clip_max=clip_max,
                         prefer_repeat=prefer_repeat)
        _update(job_id, status="done", percent=100, result=result)
    except Exception as exc:  # báo lỗi rõ ràng cho giao diện
        _update(job_id, status="error", error=str(exc),
                trace=traceback.format_exc()[-1500:])


@app.post("/api/process")
async def process(
    url: str = Form(""),
    file_path: str = Form(""),
    file: UploadFile | None = File(None),
    logo: UploadFile | None = File(None),
    music: UploadFile | None = File(None),
    reference_face: UploadFile | None = File(None),
    num_clips: int = Form(12),  # trần an toàn; số thực do prompt quyết định
    language: str = Form(""),
    max_minutes: int = Form(0),
    reframe: int = Form(1),
    keywords: str = Form(""),
    hook_types: str = Form(""),
    use_ai: int = Form(1),
    clip_min: int = Form(0),
    clip_max: int = Form(0),
    prefer_repeat: int = Form(1),
):
    """Nạp video bằng link hoặc file, kèm tuỳ chọn logo + nhạc nền."""
    has_file = file is not None and bool(file.filename)
    fpath = file_path.strip().strip('"').strip("'")
    if not url.strip() and not has_file and not fpath:
        raise HTTPException(400, "Hãy dán link, chọn file, hoặc dán đường dẫn file.")

    if has_file:  # upload qua trình duyệt (file nhỏ)
        source, is_upload = str(_save_upload(file, "video")), True
    elif fpath:   # dán đường dẫn file trên máy (video to — đọc thẳng, không upload)
        if not os.path.isfile(fpath):
            raise HTTPException(400, f"Không tìm thấy file: {fpath}")
        source, is_upload = fpath, True
    else:         # link YouTube…
        source, is_upload = url.strip(), False

    logo_path = _save_upload(logo, "logo") if (logo and logo.filename) else None
    music_path = _save_upload(music, "music") if (music and music.filename) else None
    ref_face_path = (_save_upload(reference_face, "face")
                     if (reference_face and reference_face.filename) else None)
    kw_list = extract_keywords(keywords)  # 'keywords' giờ là câu prompt tự do
    hook_set = {h.strip() for h in hook_types.split(",") if h.strip()}
    if clip_min > 0 and clip_max > 0:               # user đặt độ dài → kẹp hợp lệ
        cmin = max(5, min(clip_min, clip_max))
        cmax = min(120, max(cmin + 5, clip_max))
    else:                                            # 0 = tự động (kết theo ý trọn vẹn)
        cmin, cmax = 0, 0
    # Nếu prompt ghi số clip cụ thể (vd "cắt 5 clip") → dùng ĐÚNG số đó
    _m = re.search(r"(\d+)\s*(?:clip|đoạn|video|shorts?)", (keywords or "").lower())
    if _m:
        num_clips = max(1, min(20, int(_m.group(1))))

    job_id = _new_job("Đang xếp hàng…")
    threading.Thread(
        target=_worker,
        args=(job_id, source, num_clips, language or None, is_upload,
              logo_path, music_path, max_minutes, bool(reframe),
              kw_list, hook_set, ref_face_path, bool(use_ai), keywords,
              cmin, cmax, bool(prefer_repeat)),
        daemon=True,
    ).start()
    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    with _lock:
        job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Không thấy job.")
    return job


@app.get("/")
def index():
    return FileResponse(config.WEB_DIR / "index.html")


# File tĩnh: clip xuất ra + tài nguyên giao diện
app.mount("/output", StaticFiles(directory=str(config.OUTPUT_DIR)), name="output")
app.mount("/static", StaticFiles(directory=str(config.WEB_DIR)), name="static")
