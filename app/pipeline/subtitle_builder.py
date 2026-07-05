"""Dựng phụ đề .ass ĐỘNG cho 1 clip: chữ to giữa dưới, viền dày (kiểu Shorts),
và từ đang được nói sẽ ĐỔI MÀU nổi bật.

Cách làm: gom từ thành cụm ngắn (mặc định 3 từ). Với mỗi cụm, mỗi từ sinh 1
dòng Dialogue hiển thị cả cụm nhưng tô màu từ đang nói → hiệu ứng chữ "chạy"
theo lời. Mốc thời gian quy về gốc clip (clip bắt đầu = 0).
"""
from pathlib import Path

from .. import config
from .segmenter import Candidate

WORDS_PER_CHUNK = 3

_ASS_HEADER = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {config.VERTICAL_W}
PlayResY: {config.VERTICAL_H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,Arial,96,&H00{config.CAPTION_BASE_COLOR},&H000000FF,&H00202020,&H80000000,-1,0,0,0,100,100,0,0,1,7,3,2,90,90,360,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _ass_time(seconds: float) -> str:
    """Đổi giây → định dạng thời gian ASS H:MM:SS.cc."""
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = min(99, int(round((seconds - int(seconds)) * 100)))
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _clean(text: str) -> str:
    return text.replace("{", "(").replace("}", ")").replace("\n", " ").strip()


def _highlight(word: str) -> str:
    """Bọc từ trong mã đổi màu ASS rồi trả về màu thường sau đó."""
    return ("{\\c&H" + config.CAPTION_ACTIVE_COLOR + "&}" + word
            + "{\\c&H" + config.CAPTION_BASE_COLOR + "&}")


def _chunk_lines(chunk, base: float) -> list[str]:
    """Sinh các dòng Dialogue cho 1 cụm từ, tô màu từ đang nói."""
    lines = []
    n = len(chunk)
    for i, word in enumerate(chunk):
        start = word.start - base
        # Kéo dài tới lúc từ kế tiếp bắt đầu → phụ đề liền mạch, highlight "chạy"
        end = (chunk[i + 1].start - base) if i + 1 < n else (word.end - base)
        text = " ".join(
            _highlight(_clean(w.text)) if j == i else _clean(w.text)
            for j, w in enumerate(chunk)
        )
        lines.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Cap,,0,0,0,,{text}")
    return lines


def build_ass(cand: Candidate, clip_id: str) -> Path:
    """Ghi file .ass động cho clip, trả về đường dẫn."""
    base = cand.start
    lines: list[str] = []
    words = [w for w in cand.words if w.text.strip()]

    if words:
        for i in range(0, len(words), WORDS_PER_CHUNK):
            lines.extend(_chunk_lines(words[i:i + WORDS_PER_CHUNK], base))
    else:
        # Không có mốc từ → hiện nguyên câu (không highlight)
        for s in cand.sentences:
            lines.append(
                f"Dialogue: 0,{_ass_time(s.start - base)},{_ass_time(s.end - base)},"
                f"Cap,,0,0,0,,{_clean(s.text)}"
            )

    ass_path = config.WORK_DIR / f"{clip_id}.ass"
    ass_path.write_text(_ASS_HEADER + "\n".join(lines) + "\n", encoding="utf-8")
    return ass_path
