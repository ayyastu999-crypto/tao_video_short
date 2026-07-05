"""Bám mặt ĐỘNG, MƯỢT và KHÓA ĐÚNG NGƯỜI.

- Nhận diện mặt dày (~4 lần/giây) bằng YuNet → đường đi khung 9:16 bám sát mặt.
- Nếu có ẢNH MẪU: dùng SFace so "vân mặt" → chỉ bám đúng người trong ảnh mẫu,
  kệ người khác xuất hiện. Không có ảnh mẫu → bám mặt to nhất (như cũ).

Đọc frame TUẦN TỰ (nhanh hơn seek), thu nhỏ frame khi nhận diện cho lẹ.
Model YuNet + SFace tự tải nếu chưa có.
"""
from pathlib import Path
import math
import urllib.request

from .. import config

_TARGET = 9 / 16
_DETECT_WIDTH = 640
_SAMPLES_PER_SEC = 5          # nhận diện mỗi giây (dày → mượt)
_MAX_CTRL = 120               # điểm điều khiển tối đa cho biểu thức ffmpeg
_COSINE = 0                   # cv2.FaceRecognizerSF_FR_COSINE
# Làm mượt ĐỐI XỨNG (zero-lag) — xử lý offline nên nhìn cả trước lẫn sau,
# khung nằm ĐÚNG trên mặt (không trễ) mà vẫn mượt.
_SMOOTH_SIGMA = 1.5          # lớn hơn = mượt hơn; nhỏ hơn = bám sát/nhạy hơn

_sface = None  # cache recognizer


def _download(url: str, dest: Path) -> bool:
    if dest.exists():
        return True
    try:
        urllib.request.urlretrieve(url, dest)
        return dest.exists()
    except OSError:
        return False


def ensure_model() -> bool:
    """Tải YuNet (nhận diện mặt) nếu chưa có."""
    return _download(config.YUNET_URL, config.YUNET_MODEL)


def ensure_sface() -> bool:
    """Tải SFace (nhận diện người) nếu chưa có."""
    return _download(config.SFACE_URL, config.SFACE_MODEL)


def _recognizer():
    global _sface
    if _sface is None:
        import cv2
        _sface = cv2.FaceRecognizerSF.create(str(config.SFACE_MODEL), "")
    return _sface


def _window_dims(w: int, h: int):
    if w / h > _TARGET:
        return round(h * _TARGET), h, "x"
    return w, round(w / _TARGET), "y"


def _median_filter(vals, w=1):
    """Lọc trung vị: loại điểm nhiễu lẻ (chống nhảy đột ngột)."""
    n = len(vals)
    out = []
    for i in range(n):
        window = sorted(vals[max(0, i - w):min(n, i + w + 1)])
        out.append(window[len(window) // 2])
    return out


def _gaussian_smooth(vals, sigma=1.5):
    """Làm mượt Gaussian ĐỐI XỨNG (zero-lag): mượt mà KHÔNG bị trễ theo mặt."""
    if len(vals) < 2:
        return vals
    radius = max(1, int(sigma * 3))
    kernel = [math.exp(-(d * d) / (2 * sigma * sigma))
              for d in range(-radius, radius + 1)]
    n = len(vals)
    out = []
    for i in range(n):
        acc = wsum = 0.0
        for j, kw in enumerate(kernel):
            idx = i + j - radius
            if 0 <= idx < n:
                acc += vals[idx] * kw
                wsum += kw
        out.append(acc / wsum)
    return out


def _downsample(times, vals, maxn):
    n = len(times)
    if n <= maxn:
        return times, vals
    idx = [round(i * (n - 1) / (maxn - 1)) for i in range(maxn)]
    return [times[i] for i in idx], [vals[i] for i in idx]


def compute_target_feature(image_path: Path):
    """Trích 'vân mặt' từ ảnh mẫu người cần bám. None nếu không thấy mặt."""
    if not (ensure_model() and ensure_sface()):
        return None
    import cv2
    img = cv2.imread(str(image_path))
    if img is None:
        return None
    h, w = img.shape[:2]
    det = cv2.FaceDetectorYN.create(str(config.YUNET_MODEL), "", (w, h),
                                    config.FACE_SCORE_THRESHOLD)
    det.setInputSize((w, h))
    _, faces = det.detect(img)
    if faces is None or not len(faces):
        return None
    face = max(faces, key=lambda ff: ff[2] * ff[3])  # mặt to nhất trong ảnh
    return _recognizer().feature(_recognizer().alignCrop(img, face))


def _pick_face(faces, frame, target_feature):
    """Chọn mặt để bám: khớp ảnh mẫu (nếu có) hoặc mặt to nhất."""
    if target_feature is None:
        return max(faces, key=lambda ff: ff[2] * ff[3])
    import cv2
    rec = _recognizer()
    best, best_score = None, -1.0
    for ff in faces:
        score = rec.match(target_feature, rec.feature(rec.alignCrop(frame, ff)), _COSINE)
        if score > best_score:
            best, best_score = ff, score
    return best if best_score >= config.FACE_MATCH_THRESHOLD else None


def compute_face_track(video_path: Path, start: float, end: float,
                       target_feature=None):
    """Trả về (cw, ch, axis, samples) khung bám mặt. None nếu không thấy mặt."""
    if not ensure_model():
        return None
    import cv2

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return None
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    if fps <= 0:
        fps = 30.0
    if not w or not h:
        cap.release()
        return None

    # Nguồn đã ~9:16 → không cần bám mặt (nền mờ tự lấp kín khung), tránh crop vô nghĩa
    if abs(w / h - _TARGET) < 0.02:
        cap.release()
        return None

    cw, ch, axis = _window_dims(w, h)
    sc = _DETECT_WIDTH / w if w > _DETECT_WIDTH else 1.0
    dw, dh = round(w * sc), round(h * sc)
    det = cv2.FaceDetectorYN.create(str(config.YUNET_MODEL), "", (dw, dh),
                                    config.FACE_SCORE_THRESHOLD)
    det.setInputSize((dw, dh))

    cap.set(cv2.CAP_PROP_POS_MSEC, start * 1000)
    dur = max(0.1, end - start)
    step = max(1, round(fps / _SAMPLES_PER_SEC))
    max_frames = int(dur * fps) + 2

    times, centers = [], []
    fidx = 0
    while fidx < max_frames:
        ok, frame = cap.read()
        if not ok:
            break
        if fidx % step == 0:
            small = cv2.resize(frame, (dw, dh)) if sc != 1.0 else frame
            _, faces = det.detect(small)
            if faces is not None and len(faces):
                face = _pick_face(faces, small, target_feature)
                if face is not None:  # chỉ ghi khung THẬT SỰ thấy mặt (bỏ giữ-rồi-nhảy)
                    c = (face[0] + face[2] / 2) if axis == "x" else (face[1] + face[3] / 2)
                    times.append(round(fidx / fps, 3))
                    centers.append(float(c) / sc)
        fidx += 1
    cap.release()

    if not centers:
        return None

    win = cw if axis == "x" else ch
    limit = (w - cw) if axis == "x" else (h - ch)
    centers = _median_filter(centers, 1)                 # bỏ điểm nhiễu lẻ
    centers = _gaussian_smooth(centers, _SMOOTH_SIGMA)   # mượt & KHÔNG trễ (zero-lag)
    times, centers = _downsample(times, centers, _MAX_CTRL)
    samples = [(t, round(min(max(c - win / 2, 0), limit), 1))
               for t, c in zip(times, centers)]
    return cw, ch, axis, samples
