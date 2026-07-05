"""Tạo các "ứng viên clip" từ danh sách câu đã bóc lời.

Ý tưởng: gộp các câu liên tiếp thành cửa sổ dài trong khoảng [MIN, MAX] giây,
luôn cắt đúng ranh giới câu (không cắt giữa câu). Nhiều ứng viên sẽ chồng lấn —
bước chấm điểm + chọn lọc phía sau sẽ lấy ra các clip tốt & không trùng nhau.
"""
from dataclasses import dataclass, field

from .. import config
from .transcriber import Sentence, Word


@dataclass
class Candidate:
    start: float
    end: float
    sentences: list[Sentence]
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    ai_title: str = ""      # tiêu đề do AI đặt (nếu chọn bằng AI)
    by_ai: bool = False     # True nếu đoạn này do AI chọn

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def text(self) -> str:
        return " ".join(s.text for s in self.sentences).strip()

    @property
    def words(self) -> list[Word]:
        out: list[Word] = []
        for s in self.sentences:
            out.extend(s.words)
        return out


def build_candidates(sentences: list[Sentence], min_sec: float | None = None,
                     max_sec: float | None = None) -> list[Candidate]:
    """Sinh mọi cửa sổ câu-liên-tiếp có độ dài trong [min_sec, max_sec] giây."""
    min_sec = min_sec or config.CLIP_MIN_SEC
    max_sec = max_sec or config.CLIP_MAX_SEC
    candidates: list[Candidate] = []
    n = len(sentences)
    for i in range(n):
        j = i
        while j < n:
            dur = sentences[j].end - sentences[i].start
            if dur > max_sec:
                break
            if dur >= min_sec:
                candidates.append(
                    Candidate(sentences[i].start, sentences[j].end, sentences[i:j + 1])
                )
            j += 1
    return candidates


def select_non_overlapping(ranked: list[Candidate], k: int) -> list[Candidate]:
    """Chọn top-k clip điểm cao nhất mà không chồng lấn thời gian nhau."""
    picked: list[Candidate] = []
    for cand in ranked:  # đã sắp theo điểm giảm dần
        if len(picked) >= k:
            break
        if all(cand.end <= p.start or cand.start >= p.end for p in picked):
            picked.append(cand)
    # Trả về theo thứ tự thời gian cho dễ theo dõi
    return sorted(picked, key=lambda c: c.start)
