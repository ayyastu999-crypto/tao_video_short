"""Cấu hình trung tâm: đường dẫn thư mục, model Whisper, tham số cắt clip.

Đọc override từ biến môi trường (.env) nếu có, còn lại dùng mặc định an toàn
cho máy không có GPU.
"""
from pathlib import Path
import os

# HuggingFace Hub mặc định tạo symlink khi cache model. Windows không bật
# Developer Mode / không chạy admin sẽ lỗi WinError 1314. Tắt symlink → copy file.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

# ---- Đường dẫn ----
BASE_DIR = Path(__file__).resolve().parent.parent
WEB_DIR = BASE_DIR / "web"          # giao diện web tĩnh
OUTPUT_DIR = BASE_DIR / "output"    # clip xuất ra (user tải về)
WORK_DIR = BASE_DIR / "work"        # file tạm: video gốc, wav, ass
MODELS_DIR = BASE_DIR / "models"    # cache model Whisper (tải 1 lần)

for _d in (OUTPUT_DIR, WORK_DIR, MODELS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ---- Tăng tốc GPU (NVIDIA/CUDA) — tự dò, tự lùi CPU nếu không có ----
def _setup_cuda_dll_dirs() -> None:
    """Windows: thư viện CUDA của pip (nvidia-cublas-cu12, nvidia-cudnn-cu12) nằm ở
    site-packages/nvidia/*/bin. ctranslate2 nạp cuBLAS TRỄ bằng LoadLibrary theo PATH
    nên phải VỪA add_dll_directory VỪA prepend PATH. An toàn: máy không có thì bỏ qua."""
    if os.name != "nt":
        return
    import glob
    import site
    roots: list[str] = []
    try:
        roots += list(site.getsitepackages())
    except Exception:
        pass
    for _sp in roots:
        for _bin in glob.glob(os.path.join(_sp, "nvidia", "*", "bin")):
            if not os.path.isdir(_bin):
                continue
            try:
                os.add_dll_directory(_bin)
            except OSError:
                pass
            if _bin not in os.environ.get("PATH", ""):
                os.environ["PATH"] = _bin + os.pathsep + os.environ.get("PATH", "")


def _has_cuda() -> bool:
    """True nếu có GPU NVIDIA dùng được (đếm được thiết bị CUDA qua ctranslate2)."""
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


_setup_cuda_dll_dirs()
_GPU = _has_cuda()

# ---- Whisper (faster-whisper) — mặc định TỰ chọn theo phần cứng ----
# tiny < base < small < medium < large-v3 (càng lớn càng chính xác nhưng càng nặng).
# Có GPU  → large-v3 + cuda + float16 (chính xác nhất, ~7x realtime).
# Không   → small + cpu + int8 (chạy mọi máy). Đặt biến trong .env để ép tuỳ ý.
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "large-v3" if _GPU else "small")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cuda" if _GPU else "cpu")
WHISPER_COMPUTE = os.getenv("WHISPER_COMPUTE", "float16" if _GPU else "int8")
# VAD lọc khoảng lặng → giảm "ảo giác" (hallucination). Cần onnxruntime (đã có sẵn).
WHISPER_VAD = os.getenv("WHISPER_VAD", "1") == "1"

# ---- Video encoder — NVENC (GPU) tự dò, tự lùi libx264 (CPU) ----
# Encode clip 9:16 là khâu NẶNG NHẤT của pipeline. NVENC (GPU NVIDIA) nhanh ~3-5x
# so với libx264. VIDEO_ENCODER: "auto" (tự dò) | "nvenc" (ép GPU) | "cpu" (ép CPU).
VIDEO_ENCODER = os.getenv("VIDEO_ENCODER", "auto").lower()

# CPU: giữ chuẩn cũ (CRF 20, veryfast) — vừa là mặc định khi không GPU, vừa là đích
# fallback. NVENC: VBR chất-lượng-cố-định CQ 20 (~ tương đương CRF 20), preset p5 cân
# bằng tốc độ/chất lượng, pix_fmt yuv420p cho tương thích mọi nền tảng (TikTok/Reels).
_X264_ARGS = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]
_NVENC_ARGS = ["-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq",
               "-rc", "vbr", "-cq", "20", "-b:v", "0", "-pix_fmt", "yuv420p"]

_nvenc_ok: bool | None = None  # cache probe (None = chưa dò lần nào)


def _probe_nvenc() -> bool:
    """Thử encode 1 frame bằng h264_nvenc. True nếu chạy được (GPU + driver + ffmpeg đủ)."""
    import subprocess
    try:
        r = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error",
             "-f", "lavfi", "-i", "color=c=black:s=256x256:d=1",
             *_NVENC_ARGS, "-f", "null", "-"],
            capture_output=True, timeout=30,
        )
        return r.returncode == 0
    except Exception:
        return False


def use_nvenc() -> bool:
    """Có dùng NVENC không: theo VIDEO_ENCODER + probe thực tế (cache). 'cpu' → luôn False."""
    global _nvenc_ok
    if VIDEO_ENCODER == "cpu":
        return False
    if _nvenc_ok is None:
        _nvenc_ok = _probe_nvenc()
    return _nvenc_ok  # "auto" lẫn "nvenc": có NVENC thì dùng, không thì lùi CPU (khỏi crash)


def video_encoder_args(force_cpu: bool = False) -> list[str]:
    """List tham số -c:v cho ffmpeg. force_cpu=True → luôn libx264 (dùng khi retry fallback)."""
    return list(_X264_ARGS) if force_cpu or not use_nvenc() else list(_NVENC_ARGS)

# ---- Tham số cắt clip ----
CLIP_MIN_SEC = 18       # clip ngắn hơn mức này bị bỏ
CLIP_MAX_SEC = 60       # clip dài hơn mức này bị bỏ
CLIP_TARGET_SEC = 40    # độ dài "đẹp" để chấm điểm
DEFAULT_NUM_CLIPS = 8   # số clip mặc định muốn lấy

# ---- Khung dọc 9:16 (dọc cho Shorts/Reels/TikTok) ----
VERTICAL_W = 1080
VERTICAL_H = 1920

# ---- Phụ đề động (màu ASS dạng BBGGRR) ----
CAPTION_BASE_COLOR = "FFFFFF"    # chữ thường: trắng
CAPTION_ACTIVE_COLOR = "4AB0EB"  # chữ đang nói: vàng cam (RGB 235,176,74)

# ---- Thương hiệu (tuỳ chọn) ----
LOGO_WIDTH = 200        # px — bề ngang logo
LOGO_MARGIN = 48        # px — cách mép trên/phải
MUSIC_VOLUME = 0.12     # âm lượng nhạc nền so với tiếng gốc (0..1)

# ---- Face tracking (YuNet — bám mặt cho khung 9:16) ----
YUNET_MODEL = MODELS_DIR / "yunet.onnx"
YUNET_URL = ("https://github.com/opencv/opencv_zoo/raw/main/models/"
             "face_detection_yunet/face_detection_yunet_2023mar.onnx")
FACE_SCORE_THRESHOLD = 0.5  # hạ ngưỡng để bắt mặt tốt hơn (đỡ rớt về nền mờ)

# ---- Nhận diện ĐÚNG người qua ảnh mẫu (SFace) ----
SFACE_MODEL = MODELS_DIR / "sface.onnx"
SFACE_URL = ("https://github.com/opencv/opencv_zoo/raw/main/models/"
             "face_recognition_sface/face_recognition_sface_2021dec.onnx")
FACE_MATCH_THRESHOLD = 0.36  # cosine ≥ mức này = cùng 1 người
