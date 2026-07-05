"""Bóc lời video bằng faster-whisper → danh sách câu, mỗi câu có mốc thời gian.

Model được nạp 1 lần rồi cache lại (nạp model tốn vài giây).
Âm thanh được ffmpeg tách ra wav 16kHz mono trước để không phụ thuộc PyAV.
"""
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
import subprocess

from .. import config


@dataclass
class Word:
    start: float
    end: float
    text: str


@dataclass
class Sentence:
    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)


@lru_cache(maxsize=1)
def _load_model(size: str, device: str, compute: str):
    from faster_whisper import WhisperModel
    try:
        return WhisperModel(
            size, device=device, compute_type=compute,
            download_root=str(config.MODELS_DIR),
        )
    except Exception:
        # GPU/CUDA không sẵn sàng (thiếu cuBLAS…) → tự lùi về CPU cho chắc chạy
        return WhisperModel(
            size, device="cpu", compute_type="int8",
            download_root=str(config.MODELS_DIR),
        )


def extract_audio(video_path: Path, max_seconds: float = 0) -> Path:
    """Tách audio sang wav 16kHz mono. max_seconds>0 = chỉ lấy phần đầu."""
    wav_path = config.WORK_DIR / (video_path.stem + ".wav")
    cmd = ["ffmpeg", "-y", "-i", str(video_path)]
    if max_seconds and max_seconds > 0:
        cmd += ["-t", f"{max_seconds:.0f}"]
    cmd += ["-vn", "-ac", "1", "-ar", "16000", "-f", "wav", str(wav_path)]
    subprocess.run(cmd, check=True, capture_output=True)
    return wav_path


def transcribe(audio_path: Path, language: str | None = None, on_progress=None):
    """Bóc lời. Trả về (danh_sách_Sentence, ngôn_ngữ, thời_lượng_giây)."""
    model = _load_model(config.WHISPER_MODEL, config.WHISPER_DEVICE, config.WHISPER_COMPUTE)

    segments, info = model.transcribe(
        str(audio_path),
        language=language or None,
        word_timestamps=True,
        vad_filter=config.WHISPER_VAD,
    )

    total = info.duration or 0.0
    sentences: list[Sentence] = []
    for seg in segments:  # generator — chạy tới đâu tính tới đó
        words = [Word(w.start, w.end, w.word) for w in (seg.words or [])]
        sentences.append(Sentence(seg.start, seg.end, seg.text.strip(), words))
        if on_progress:
            frac = min(1.0, seg.end / total) if total else 0.0
            on_progress(f"Đang bóc lời… {seg.end:.0f}/{total:.0f} giây", frac)

    return sentences, info.language, info.duration
