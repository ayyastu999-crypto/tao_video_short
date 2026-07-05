"""Chấm điểm "tiềm năng viral" cho mỗi ứng viên clip.

Tiêu chí tuỳ biến từ giao diện:
  - keywords: từ khóa người dùng ưu tiên.
  - hook_types: các loại "hook" được BẬT.
  - target/max_sec: độ dài clip người dùng đặt (ảnh hưởng điểm độ dài).
  - prefer_repeat: cộng điểm cho đoạn có từ/câu LẶP LẠI (nhấn mạnh).
Ngoài ra: kết trọn ý, cảm xúc, mật độ lời nói.
"""
from collections import Counter
import re

from .. import config
from .segmenter import Candidate

# Loại hook + từ nhận diện (khớp ô tick / prompt)
HOOK_CATEGORIES = {
    "question": {"tại sao", "vì sao", "làm sao", "làm thế nào", "thế nào",
                 "có nên", "bạn có biết", "how", "why", "what"},
    "number":   set(),
    "warning":  {"đừng", "sai lầm", "cẩn thận", "tránh", "chớ", "nguy hiểm",
                 "thất bại", "mistake", "never", "stop"},
    "cta":      {"hãy", "đăng ký", "làm ngay", "bắt đầu", "nhớ", "theo dõi",
                 "đừng bỏ lỡ", "subscribe", "follow", "comment"},
    "secret":   {"bí quyết", "bí mật", "mẹo", "cách", "tuyệt chiêu",
                 "tip", "secret", "hack"},
}
ALL_HOOKS = set(HOOK_CATEGORIES)

_HOOK_POINTS = {"question": 2.0, "number": 1.5, "warning": 2.0, "cta": 1.5, "secret": 2.0}
_HOOK_LABEL = {"question": "câu hỏi", "number": "có số", "warning": "cảnh báo",
               "cta": "kêu gọi", "secret": "bí quyết"}

EMOTION_WORDS = {
    "tuyệt", "cực", "rất", "khủng", "sốc", "bất ngờ", "thất bại",
    "thành công", "tiền", "giàu", "nghèo", "sợ", "yêu", "ghét",
    "amazing", "huge", "shocking", "fail", "success", "money", "love",
}

_STOPWORDS = {
    "và", "là", "của", "có", "cho", "một", "những", "các", "này", "đó",
    "khi", "để", "với", "thì", "mà", "ở", "cái", "người", "tôi", "bạn",
    "nó", "được", "cũng", "rất", "hay", "tìm", "đoạn", "về", "trong",
    "the", "and", "of", "to", "a", "in", "on",
}

NUM_RE = re.compile(r"\d")
WORD_RE = re.compile(r"[0-9a-zA-ZÀ-ỹ]+")
END_PUNCT = (".", "!", "?", "…")


def _bell(value: float, center: float, width: float) -> float:
    return max(0.0, 1.0 - abs(value - center) / max(1.0, width))


def _has_hook(cat: str, low: str, opening: str, text: str) -> bool:
    if cat == "number":
        return bool(NUM_RE.search(text))
    if cat == "question":
        return "?" in text or any(w in opening for w in HOOK_CATEGORIES["question"])
    return any(w in low for w in HOOK_CATEGORIES.get(cat, ()))


def _repeat_count(low: str) -> int:
    """Số từ 'có nghĩa' xuất hiện >= 2 lần (dấu hiệu người nói nhấn mạnh)."""
    words = [w for w in WORD_RE.findall(low) if len(w) >= 3 and w not in _STOPWORDS]
    return sum(1 for c in Counter(words).values() if c >= 2)


def score_candidate(cand: Candidate, keywords: list, hook_types: set,
                    target: float, max_sec: float, prefer_repeat: bool) -> Candidate:
    text = cand.text
    low = text.lower()
    opening = low[:100]
    reasons: list[str] = []
    score = 0.0

    # 1) Độ dài gần mức người dùng đặt
    score += _bell(cand.duration, target, max_sec) * 3.0

    # 2) Từ khóa người dùng
    matched = [k for k in keywords if k and k.lower() in low]
    if matched:
        score += 3.0
        reasons.append("từ khóa: " + ", ".join(matched[:3]))

    # 3) Loại hook được bật
    for cat in hook_types:
        if _has_hook(cat, low, opening, text):
            score += _HOOK_POINTS.get(cat, 1.0)
            reasons.append(_HOOK_LABEL.get(cat, cat))

    # 4) Từ/câu lặp lại (nhấn mạnh)
    if prefer_repeat:
        reps = _repeat_count(low)
        if reps:
            score += min(reps, 3) * 1.2
            reasons.append("có từ lặp lại (nhấn mạnh)")

    # 5) Kết trọn ý
    if text.endswith(END_PUNCT):
        score += 1.0

    # 6) Từ cảm xúc
    emo = sum(1 for w in EMOTION_WORDS if w in low)
    if emo:
        score += min(emo, 3) * 0.5
        reasons.append("giàu cảm xúc")

    # 7) Mật độ lời nói
    wps = len(cand.words) / cand.duration if cand.duration else 0
    if wps >= 1.5:
        score += 1.0
    elif wps < 0.7:
        score -= 1.0

    cand.score = round(score, 2)
    cand.reasons = reasons or ["đoạn nói liền mạch"]
    return cand


def rank(candidates: list[Candidate], keywords: list | None = None,
         hook_types: set | None = None, target: float | None = None,
         max_sec: float | None = None, prefer_repeat: bool = True) -> list[Candidate]:
    """Chấm điểm tất cả rồi sắp xếp giảm dần."""
    keywords = keywords or []
    hook_types = hook_types if hook_types else ALL_HOOKS
    target = target or config.CLIP_TARGET_SEC
    max_sec = max_sec or config.CLIP_MAX_SEC
    for c in candidates:
        score_candidate(c, keywords, hook_types, target, max_sec, prefer_repeat)
    return sorted(candidates, key=lambda c: c.score, reverse=True)


def extract_keywords(prompt: str, limit: int = 8) -> list:
    """Rút từ khóa có nghĩa từ 1 câu 'prompt' (bỏ từ dừng, từ quá ngắn)."""
    out = []
    for w in WORD_RE.findall((prompt or "").lower()):
        if len(w) >= 3 and w not in _STOPWORDS and w not in out:
            out.append(w)
        if len(out) >= limit:
            break
    return out
