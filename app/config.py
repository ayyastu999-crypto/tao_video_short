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

# ---- Whisper (faster-whisper) ----
# tiny < base < small < medium < large-v3  (càng lớn càng chính xác nhưng chậm hơn)
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "small")
# cpu = chạy mọi máy (mặc định an toàn). cuda = nhanh hơn nhưng cần cài CUDA + cuBLAS.
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cpu")
WHISPER_COMPUTE = os.getenv("WHISPER_COMPUTE", "int8")   # int8 nhẹ cho CPU
# VAD lọc khoảng lặng cần onnxruntime — tắt mặc định để tránh lỗi cài đặt.
WHISPER_VAD = os.getenv("WHISPER_VAD", "0") == "1"

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
