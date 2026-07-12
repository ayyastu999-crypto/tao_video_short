"""Dựng clip dọc 9:16 bằng ffmpeg: video + phụ đề, tuỳ chọn logo + nhạc nền.

Khung hình:
  - face_track: cắt BÁM ĐỘNG theo mặt. Vị trí khung theo thời gian được ghi ra
    FILE LỆNH sendcmd (điều khiển crop x/y) — tránh nhồi 1 biểu thức khổng lồ
    khiến ffmpeg lỗi "Invalid argument".
  - face_track=None: nền mờ phóng to + video giữa (cũng tự lấp kín nếu nguồn đã 9:16).

Dùng ffmpeg seek nhanh (-ss trước -i). Chạy với cwd = thư mục chứa .ass để
tham chiếu .ass và .cmd bằng tên ngắn (né escape đường dẫn Windows).
"""
from pathlib import Path
import bisect
import subprocess

from .. import config

W, H = config.VERTICAL_W, config.VERTICAL_H
_CMD_FPS = 15  # số lần cập nhật khung bám mặt mỗi giây (mượt)


def _interp(samples, t):
    """Nội suy tuyến tính vị trí khung tại thời điểm t."""
    ts = [s[0] for s in samples]
    if t <= ts[0]:
        return samples[0][1]
    if t >= ts[-1]:
        return samples[-1][1]
    i = bisect.bisect_right(ts, t)
    (t0, x0), (t1, x1) = samples[i - 1], samples[i]
    return x0 + (x1 - x0) * (t - t0) / max(1e-6, t1 - t0)


def _write_sendcmd(face_track, ass_path: Path) -> str:
    """Ghi file lệnh sendcmd điều khiển crop x/y theo thời gian. Trả tên file."""
    _cw, _ch, axis, samples = face_track
    path = ass_path.with_suffix(".cmd")
    t0, tn = samples[0][0], samples[-1][0]
    steps = max(1, int((tn - t0) * _CMD_FPS))
    lines = [f"{t0 + (tn - t0) * i / steps:.3f} crop {axis} "
             f"{_interp(samples, t0 + (tn - t0) * i / steps):.1f};"
             for i in range(steps + 1)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path.name


def _face_base(ass_name: str, face_track, cmd_name: str) -> str:
    cw, ch, axis, samples = face_track
    x0 = samples[0][1] if axis == "x" else 0
    y0 = samples[0][1] if axis == "y" else 0
    return (f"[0:v]sendcmd=f={cmd_name},"
            f"crop=w={cw}:h={ch}:x={x0:.1f}:y={y0:.1f},"
            f"scale={W}:{H}:flags=lanczos[base];[base]ass={ass_name}[vbase]")


def _blur_base(ass_name: str) -> str:
    return (
        "[0:v]split=2[bg][fg];"
        f"[bg]scale={W}:{H}:force_original_aspect_ratio=increase,"
        f"crop={W}:{H},boxblur=24:2[bgb];"
        f"[fg]scale={W}:{H}:force_original_aspect_ratio=decrease[fgs];"
        "[bgb][fgs]overlay=(W-w)/2:(H-h)/2[base];"
        f"[base]ass={ass_name}[vbase]"
    )


def _video_filter(ass_name: str, logo_idx, face_track, cmd_name) -> tuple[str, str]:
    base = _face_base(ass_name, face_track, cmd_name) if face_track else _blur_base(ass_name)
    if logo_idx is not None:
        base += (f";[{logo_idx}:v]scale={config.LOGO_WIDTH}:-1[logo];"
                 f"[vbase][logo]overlay=W-w-{config.LOGO_MARGIN}:{config.LOGO_MARGIN}[vout]")
        return base, "[vout]"
    return base, "[vbase]"


def render_clip(video_path: Path, start: float, end: float, ass_path: Path,
                out_path: Path, logo_path: Path | None = None,
                music_path: Path | None = None, face_track=None) -> Path:
    """Cắt [start,end] → clip dọc + phụ đề (+ logo/nhạc/bám mặt nếu có)."""
    duration = max(0.1, end - start)
    cmd_name = _write_sendcmd(face_track, ass_path) if face_track else None

    cmd = ["ffmpeg", "-y", "-ss", f"{start:.3f}", "-i", str(video_path)]
    idx = 1
    logo_idx = music_idx = None
    if logo_path:
        cmd += ["-loop", "1", "-i", str(logo_path)]
        logo_idx = idx
        idx += 1
    if music_path:
        cmd += ["-stream_loop", "-1", "-i", str(music_path)]
        music_idx = idx
        idx += 1

    video_parts, vlabel = _video_filter(ass_path.name, logo_idx, face_track, cmd_name)
    parts = [video_parts]

    if music_idx is not None:
        parts.append(f"[{music_idx}:a]volume={config.MUSIC_VOLUME}[mus]")
        parts.append("[0:a][mus]amix=inputs=2:duration=first:dropout_transition=0[aout]")
        audio_map = ["-map", "[aout]"]
    else:
        audio_map = ["-map", "0:a:0?"]

    # Chừa chỗ chèn bộ mã hoá video (NVENC/CPU) giữa head và tail để retry được.
    head = cmd + ["-filter_complex", ";".join(parts), "-map", vlabel, *audio_map,
                  "-t", f"{duration:.3f}"]
    tail = ["-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(out_path)]

    used_nvenc = config.use_nvenc()
    try:
        subprocess.run(head + config.video_encoder_args() + tail,
                       check=True, capture_output=True, cwd=str(ass_path.parent))
    except subprocess.CalledProcessError:
        if not used_nvenc:
            raise  # đã libx264 mà vẫn lỗi → lỗi thật, ném lên
        # NVENC lỗi lúc encode thật (hiếm) → lùi libx264 rồi thử lại, khỏi hỏng cả buổi
        subprocess.run(head + config.video_encoder_args(force_cpu=True) + tail,
                       check=True, capture_output=True, cwd=str(ass_path.parent))
    return out_path
