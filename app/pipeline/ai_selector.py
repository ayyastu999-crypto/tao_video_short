"""Dùng AI đọc bản bóc lời → chọn đoạn hay + điểm kết hợp lý.

Ưu tiên OLLAMA (local, miễn phí, không cần auth — vd qwen2.5). Nếu không có
Ollama thì thử Claude CLI. Trả (list[{start,end,title,reason}] | None, lý_do_lỗi).
AI trả JSON; lỗi/parse fail → orchestrator tự lùi về chấm điểm luật.
"""
import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request

_OLLAMA_URL = "http://localhost:11434"
_MODEL_PREFER = ["qwen2.5", "qwen", "llama3.1", "llama3", "gemma2", "gemma", "mistral"]


# ---------- Ollama (local) ----------

def _ollama_models() -> list:
    try:
        with urllib.request.urlopen(_OLLAMA_URL + "/api/tags", timeout=5) as r:
            data = json.loads(r.read().decode("utf-8"))
        return [m["name"] for m in data.get("models", [])]
    except (OSError, ValueError, KeyError):
        return []


def _pick_model(models: list):
    for pref in _MODEL_PREFER:
        for m in models:
            if m.startswith(pref):
                return m
    return models[0] if models else None


def _call_ollama(model: str, prompt: str, timeout: int):
    # num_ctx đủ CHỨA HẾT bản bóc lời (bị cắt cụt → AI không hiểu trọn ý)
    num_ctx = min(24576, max(4096, int(len(prompt) / 2.5) + 1024))
    last_err = ""
    # Context lớn (>8192) khó vừa GPU 6GB → chạy thẳng CPU (RAM 16GB) cho khỏi cụt.
    # Nhỏ → thử GPU trước (nhanh), OOM thì lùi CPU.
    attempts = ({"num_gpu": 0},) if num_ctx > 8192 else ({}, {"num_gpu": 0})
    for extra in attempts:
        opts = {"temperature": 0.3, "num_ctx": num_ctx, **extra}
        body = json.dumps({"model": model, "prompt": prompt, "stream": False,
                           "format": "json", "options": opts}).encode("utf-8")
        req = urllib.request.Request(_OLLAMA_URL + "/api/generate", data=body,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8")).get("response", ""), ""
        except urllib.error.HTTPError as exc:
            last_err = f"Ollama HTTP {exc.code}: {exc.read().decode('utf-8', 'ignore')[:120]}"
            if "out of memory" not in last_err.lower():
                break  # lỗi khác → khỏi thử CPU
        except (OSError, ValueError) as exc:
            last_err = f"Ollama lỗi: {exc}"
            break
    return None, last_err


# ---------- Claude CLI (dự phòng) ----------

def claude_path():
    return shutil.which("claude")


def _call_claude(prompt: str, timeout: int):
    path = claude_path()
    if not path:
        return None, "Máy chưa có lệnh 'claude'"
    directive = "Read the content above and return ONLY the requested JSON array."
    if os.name == "nt":
        cmd = [os.environ.get("COMSPEC", "cmd.exe"), "/c", path, "-p", directive]
    else:
        cmd = [path, "-p", directive]
    try:
        proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, "claude phản hồi quá lâu"
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"claude không gọi được: {exc}"
    out = (proc.stdout or "").strip()
    if proc.returncode != 0 or out.startswith("Failed to authenticate") or "401" in out[:80]:
        first = (out or (proc.stderr or "").strip()).splitlines()
        return None, f"claude lỗi: {(first[0] if first else 'exit ' + str(proc.returncode))[:140]}"
    return out, ""


def is_available() -> bool:
    return bool(_ollama_models()) or claude_path() is not None


# ---------- Prompt + parse ----------

def _instructions(criteria: str, num_clips: int, clip_min: int, clip_max: int,
                  prefer_repeat: bool) -> str:
    extra = (f'\n- ⚑ YÊU CẦU RIÊNG CỦA NGƯỜI DÙNG — BẮT BUỘC tuân theo: "{criteria.strip()}". '
             "CHỈ chọn đoạn ĐÚNG yêu cầu này (đúng chủ đề/nội dung được yêu cầu); "
             "đoạn KHÔNG liên quan thì BỎ, dù có hay."
             if criteria and criteria.strip() else "")
    rep = ("\n- ƯU TIÊN đoạn có TỪ/CÂU LẶP LẠI (người nói nhấn mạnh) — thường là câu chốt đắt."
           if prefer_repeat else "")
    if clip_min and clip_max:
        length = (f"\n- Độ dài tham khảo {clip_min}-{clip_max} giây, "
                  "nhưng ĐỦ Ý quan trọng hơn — thà dài hơn còn hơn cụt.")
    else:
        length = ("\n- Độ dài KHÔNG giới hạn: clip DÀI cũng tốt (thậm chí nên dài) miễn TRỌN Ý. "
                  "Thà dài mà đủ ý còn hơn ngắn mà cụt.")
    return (
        "Bạn là biên tập viên video ngắn (Shorts/Reels/TikTok) chuyên nghiệp, hiểu tiếng Việt.\n"
        "Từ bản bóc lời bên dưới (mỗi dòng có mốc [giây_đầu-giây_cuối]), chọn ra các đoạn HAY NHẤT để cắt clip.\n"
        f"- SỐ LƯỢNG: nếu người dùng yêu cầu số clip cụ thể thì làm ĐÚNG số đó; nếu không thì tự chọn số hợp lý. KHÔNG quá {num_clips} đoạn.\n"
        "Nguyên tắc (theo thứ tự quan trọng):\n"
        "- QUAN TRỌNG NHẤT: mỗi đoạn phải TRỌN VẸN MỘT Ý / CÂU CHUYỆN. Bắt đầu từ lúc mở ý, "
        "KẾT THÚC khi người nói đã nói HẾT ý đó (đủ để người xem hiểu trọn). "
        "TUYỆT ĐỐI KHÔNG ngắt khi câu/ý chưa nói xong.\n"
        "- Nếu một ý kéo dài nhiều câu, lấy TRỌN cả đoạn — đừng ngại clip dài.\n"
        "- Mở đầu bằng 'hook' hút người xem (câu hỏi, con số, tuyên bố mạnh, gây tò mò)."
        f"{length}{rep}{extra}\n"
        "CHỈ trả về JSON, KHÔNG giải thích, KHÔNG markdown. Dạng mảng object:\n"
        '[{"start": <giây>, "end": <giây>, "title": "<tiêu đề hook ngắn hấp dẫn>", '
        '"reason": "<vì sao đoạn này hay>"}]'
    )


def _transcript(sentences) -> str:
    return "\n".join(f"[{s.start:.0f}-{s.end:.0f}] {s.text}" for s in sentences)


def _parse(output: str):
    """Bóc mảng JSON từ output của model (chịu được lỡ có markdown/chữ thừa)."""
    text = (output or "").strip()
    a, b = text.find("["), text.rfind("]")
    if a == -1 or b == -1 or b < a:
        return None
    try:
        data = json.loads(text[a:b + 1])
    except json.JSONDecodeError:
        return None
    segs = []
    for d in data if isinstance(data, list) else []:
        try:
            s, e = float(d["start"]), float(d["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if e - s < 5:
            continue
        segs.append({
            "start": s, "end": e,
            "title": str(d.get("title", "")).strip(),
            "reason": str(d.get("reason", "")).strip(),
        })
    return segs or None


def select_segments(sentences, criteria: str, num_clips: int,
                    clip_min: int = 15, clip_max: int = 60,
                    prefer_repeat: bool = True, timeout: int = 300):
    """Nhờ AI chọn đoạn hay. Trả (list | None, lý_do_lỗi)."""
    if not sentences:
        return None, "Không có lời thoại để phân tích"
    prompt = (_instructions(criteria, num_clips, clip_min, clip_max, prefer_repeat)
              + "\n\n=== BẢN BÓC LỜI ===\n" + _transcript(sentences))

    errors = []

    # 1) Ollama (ưu tiên — local, miễn phí)
    models = _ollama_models()
    if models:
        out, err = _call_ollama(_pick_model(models), prompt, timeout)
        if out:
            segs = _parse(out)
            if segs:
                return segs, ""
            errors.append("Ollama trả JSON không hợp lệ")
        elif err:
            errors.append(err)

    # 2) Claude CLI (dự phòng)
    out, err = _call_claude(prompt, timeout)
    if out:
        segs = _parse(out)
        if segs:
            return segs, ""
        errors.append("claude trả JSON không hợp lệ")
    elif err:
        errors.append(err)

    return None, " | ".join(errors) or "Không có engine AI khả dụng"
