"""Điều phối toàn bộ pipeline: 1 link video → nhiều clip ngắn có phụ đề.

Gọi lần lượt: tải video → tách audio → bóc lời → cắt ứng viên → chấm điểm →
chọn top không trùng → dựng từng clip. Báo tiến độ qua callback on_progress.
"""
from pathlib import Path
import re
import subprocess

from .. import config
from .downloader import download_video
from .transcriber import extract_audio, transcribe
from .segmenter import build_candidates, select_non_overlapping, Candidate
from .scorer import rank
from .subtitle_builder import build_ass
from .clipper import render_clip
from .titler import make_caption
from . import reframe
from . import ai_selector


def _probe_duration(path: Path) -> float:
    """Lấy thời lượng video (giây) bằng ffprobe; lỗi thì trả 0."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True,
        )
        return float(out.stdout.strip())
    except (ValueError, OSError):
        return 0.0


def _candidate_from_range(sentences, start: float, end: float):
    """Dựng Candidate từ khoảng [start,end] AI chọn — bám ranh giới câu."""
    subset = [s for s in sentences if s.end > start and s.start < end]
    if not subset:
        return None
    return Candidate(subset[0].start, subset[-1].end, subset)


def run_job(source: str, num_clips: int, language: str | None,
            on_progress, is_upload: bool = False,
            logo_path: Path | None = None, music_path: Path | None = None,
            max_minutes: int = 0, reframe_faces: bool = True,
            keywords: list | None = None, hook_types: set | None = None,
            ref_face_path: Path | None = None,
            use_ai: bool = False, criteria_text: str = "",
            clip_min: int = 15, clip_max: int = 60, prefer_repeat: bool = True) -> dict:
    """Chạy full pipeline, trả về dict kết quả (tiêu đề + danh sách clip).

    source = link video (khi is_upload=False) hoặc đường dẫn file đã lưu
    (khi is_upload=True, bỏ qua bước tải).
    """
    if is_upload:
        on_progress("Đang đọc video từ máy…", 10)
        video_path = Path(source)
        title = video_path.stem
    else:
        on_progress("Đang tải video…", 5)
        video_path, title = download_video(source, lambda m: on_progress(m, 12))

    dur = _probe_duration(video_path)
    max_seconds = max_minutes * 60 if max_minutes and max_minutes > 0 else 0
    effective = min(dur, max_seconds) if max_seconds else dur

    on_progress("Đang tách âm thanh…", 20)
    wav = extract_audio(video_path, max_seconds)

    if max_seconds and dur > max_seconds:
        on_progress(f"Chỉ xử lý {max_minutes} phút đầu (video dài ~{dur / 60:.0f} phút).", 22)
    if effective >= 1200:  # phần xử lý >= 20 phút → cảnh báo sẽ lâu
        on_progress(
            f"Phần xử lý ~{effective / 60:.0f} phút — bóc lời trên CPU sẽ lâu "
            f"(có thể vài chục phút). Cứ để yên cho chạy nhé…", 24)

    on_progress("Đang bóc lời bằng Whisper (bước lâu nhất)…", 25)
    sentences, lang, duration = transcribe(
        wav, language,
        lambda msg, frac: on_progress(msg, 25 + int(30 * frac)))
    if not sentences:
        raise RuntimeError("Không bóc được lời nào — video có tiếng nói không?")

    picks, hook, ai_used, ai_note = None, None, False, ""

    # Ưu tiên nhờ AI (Claude) đọc bản bóc lời chọn đoạn hay
    if use_ai:
        on_progress("Đang nhờ AI đọc & chọn đoạn hay…", 55)
        segs, ai_note = ai_selector.select_segments(
            sentences, criteria_text, num_clips, clip_min, clip_max, prefer_repeat)
        if segs:
            ai_picks = []
            for seg in segs:
                cand = _candidate_from_range(sentences, seg["start"], seg["end"])
                if cand is None:
                    continue
                cand.by_ai = True
                cand.ai_title = seg["title"]
                cand.reasons = [seg["reason"]] if seg["reason"] else ["AI chọn"]
                cand.hook_line = seg.get("hook_line", "")
                cand.category = seg.get("category", "")
                cand.viral_score = seg.get("viral_score", 0.0)
                cand.ai_caption = seg.get("caption", "")
                cand.ai_hashtags = seg.get("hashtags", [])
                ai_picks.append(cand)
            if ai_picks:
                picks = ai_picks[:num_clips]
                hook = picks[0]        # AI xếp đoạn tốt trước → đoạn đầu là hook mồi
                ai_used = True
                ai_note = ""

    # Không dùng AI (hoặc AI lỗi) → chấm điểm luật cũ
    if picks is None:
        on_progress("Đang chấm điểm & chọn đoạn hay…", 60)
        # 0 = tự động (theo ý trọn vẹn) → dùng biên rộng cho luật
        hmin = clip_min if clip_min else 15
        hmax = clip_max if clip_max else 90
        candidates = build_candidates(sentences, hmin, hmax)
        ranked = rank(candidates, keywords, hook_types,
                      target=(hmin + hmax) / 2, max_sec=hmax,
                      prefer_repeat=prefer_repeat)
        picks = select_non_overlapping(ranked, num_clips)
        if not picks:
            raise RuntimeError("Không tìm được đoạn phù hợp (video quá ngắn?).")
        early_limit = max(120.0, (effective or duration) * 0.3)
        hook = next((c for c in ranked if c.start <= early_limit), None)
        if hook is not None:
            others = [p for p in picks if not (p.start < hook.end and p.end > hook.start)]
            picks = [hook] + others[:max(0, num_clips - 1)]

    # Tên file an toàn (bỏ dấu tiếng Việt, khoảng trắng…) để ffmpeg/URL không lỗi
    safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", video_path.stem).strip("_") or "clip"

    # Nếu có ảnh mẫu → trích "vân mặt" người cần bám (dùng cho mọi clip)
    target_feature = None
    if reframe_faces and ref_face_path is not None:
        on_progress("Đang đọc ảnh mặt người cần bám…", 58)
        try:
            target_feature = reframe.compute_target_feature(ref_face_path)
        except Exception:
            target_feature = None

    clips = []
    total = len(picks)
    for idx, cand in enumerate(picks, start=1):
        on_progress(f"Đang bám mặt & dựng clip {idx}/{total}…", 60 + int(38 * idx / total))
        is_hook = cand is hook
        clip_id = f"{safe_stem}_{'hook' if is_hook else f'{idx:02d}'}"
        ass_path = build_ass(cand, clip_id)
        out_path = config.OUTPUT_DIR / f"{clip_id}.mp4"

        # Bám mặt động: tính đường đi khung 9:16 theo mặt; lỗi/không thấy mặt → nền mờ
        face_track = None
        if reframe_faces:
            try:
                face_track = reframe.compute_face_track(
                    video_path, cand.start, cand.end, target_feature)
            except Exception:
                face_track = None

        render_clip(video_path, cand.start, cand.end, ass_path, out_path,
                    logo_path=logo_path, music_path=music_path, face_track=face_track)

        caption = make_caption(cand)   # fallback heuristic khi AI không trả caption/hashtag
        clip_title = cand.ai_title or caption["title"]  # AI đặt tên thì ưu tiên
        # Ưu tiên caption + hashtag do AI viết; thiếu thì lùi về heuristic
        hashtags = cand.ai_hashtags or caption["hashtags"]
        post_caption = cand.ai_caption or clip_title
        cap_text = f"{post_caption}\n\n{' '.join(hashtags)}".strip()
        (config.OUTPUT_DIR / f"{clip_id}.txt").write_text(cap_text, encoding="utf-8")

        clips.append({
            "file": out_path.name,
            "caption_file": f"{clip_id}.txt",
            "kind": "hook" if is_hook else "clip",
            "by_ai": cand.by_ai,
            "index": idx,
            "start": round(cand.start, 1),
            "end": round(cand.end, 1),
            "duration": round(cand.duration, 1),
            "score": cand.score,
            "viral_score": round(cand.viral_score, 1),
            "category": cand.category,
            "hook_line": cand.hook_line,
            "reasons": cand.reasons,
            "title": clip_title,
            "caption": post_caption,
            "hashtags": hashtags,
            "text": cand.text[:280],
        })

    on_progress("Hoàn tất!", 100)
    return {
        "title": title,
        "language": lang,
        "source_duration": round(duration, 1),
        "ai_used": ai_used,
        "ai_note": ai_note,
        "clips": clips,
    }
