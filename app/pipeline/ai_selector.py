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


def _win_short_path(p: str) -> str:
    """Đổi sang 8.3 short path (không khoảng trắng). Máy có username chứa dấu cách
    (vd 'C:\\Users\\MSSI  GE67HX\\...') khiến cmd.exe /c cắt path ở dấu cách đầu → lỗi
    'C:\\Users\\MSSI is not recognized'. Short path khử khoảng trắng nên cmd.exe chạy được."""
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(512)
        if ctypes.windll.kernel32.GetShortPathNameW(p, buf, 512):
            return buf.value
    except Exception:
        pass
    return p


def _call_claude(prompt: str, timeout: int):
    path = claude_path()
    if not path:
        return None, "Máy chưa có lệnh 'claude'"
    directive = "Read the content above and return ONLY the requested JSON array."
    if os.name == "nt":
        # claude là .cmd (npm) nên cần cmd.exe; dùng 8.3 short path để path không chứa
        # khoảng trắng (tránh cmd.exe cắt path ở dấu cách -> 'C:\\Users\\MSSI' lỗi).
        cmd = [os.environ.get("COMSPEC", "cmd.exe"), "/c",
               _win_short_path(path), "-p", directive]
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
    extra = (f'\n\n⚑ YÊU CẦU RIÊNG CỦA NGƯỜI DÙNG — BẮT BUỘC tuân theo: "{criteria.strip()}".\n'
             "CHỈ chọn đoạn ĐÚNG yêu cầu này (đúng chủ đề/nội dung được yêu cầu). "
             "Đoạn KHÔNG liên quan thì BỎ, dù hook có mạnh tới đâu."
             if criteria and criteria.strip() else "")
    rep = ("\n- Trong hai đoạn ngang tài, ƯU TIÊN đoạn có TỪ/CÂU LẶP LẠI hoặc được NHẤN GIỌNG "
           "(người nói cố ý nhắc lại) — thường là câu chốt đắt."
           if prefer_repeat else "")
    lo = clip_min or 20
    hi = clip_max or 40
    return (
        # ---------- LỚP 1: VAI + KHÁN GIẢ + MỤC TIÊU ----------
        "Bạn là NHÀ SẢN XUẤT video ngắn viral (TikTok/Reels/Shorts) cho tệp CHỦ DOANH NGHIỆP "
        "và người kinh doanh Việt Nam. Nguồn là bài giảng của cô Đỗ Trương San San — chuyên gia "
        "đào tạo kinh doanh, marketing, vận hành, quản trị, phát triển bản thân; giọng THỰC CHIẾN, "
        "thẳng thắn, truyền cảm hứng.\n"
        "Bạn CHỈ đọc được LỜI NÓI (bản bóc lời bên dưới, mỗi dòng có mốc [giây_đầu-giây_cuối]) — "
        "không xem được hình/tiếng. Mọi lựa chọn phải dựa hoàn toàn vào CÂU CHỮ.\n\n"
        "ĐỊNH NGHĨA 'VIRAL' cho kênh này = cân bằng ĐỦ CẢ 3, KHÔNG chỉ 'hay':\n"
        "  (1) REACH — chặn-lướt 3 giây đầu, kéo lượt xem thuần.\n"
        "  (2) LƯU & CHIA SẺ — người xem rút ra một điều đáng lưu, hoặc thấy 'đúng nỗi đau mình' muốn gửi cho người khác.\n"
        "  (3) KÉO VỀ HỌC — đoạn thể hiện CHIỀU SÂU và UY TÍN của cô San, để lại lý do muốn học thêm.\n\n"

        # ---------- LỚP 2: HOOK = CỔNG LỌC CỨNG ----------
        "▓ LUẬT SỐ 1 — HOOK 3 GIÂY LÀ CỔNG VÀO BẮT BUỘC (đoạn không qua thì LOẠI):\n"
        "Chủ DN lướt điện thoại rất nhanh. Với MỖI đoạn, làm 'bài test ngón tay cái': 5-8 CHỮ ĐẦU TIÊN "
        "của clip có làm một chủ shop/chủ DN đang lướt DỪNG ngón tay lại không?\n"
        "Câu mở của clip PHẢI là MỘT trong các dạng chặn-lướt sau:\n"
        "  • Con số / tỷ lệ / mốc tiền cụ thể ('90% chủ shop chết vì...', 'Bán 300 đơn mà...').\n"
        "  • Tuyên bố ngược đời, đi ngược đám đông ('Đừng vội tuyển thêm người', 'Giảm giá là tự sát').\n"
        "  • Câu hỏi chạm túi tiền / nỗi đau ('Sao bán chạy mà vẫn lỗ?').\n"
        "  • Mở vòng tò mò — hé nửa vấn đề ('Có 1 sai lầm khiến tôi mất sạch...').\n"
        "  • Gọi thẳng tên một nỗi đau cụ thể của chủ DN (nhân viên nghỉ ôm mất khách, tự ôm hết việc, "
        "chạy ads mãi không ra đơn, làm quần quật mà không có lãi).\n"
        "→ Nếu đoạn hay nhưng MỞ ĐẦU NHẠT (bắt đầu bằng từ dẫn nhập: 'à thì', 'ừm', 'tiếp theo', "
        "'như tôi vừa nói', 'cho nên', 'và rồi', 'nói chung là'...): DỜI điểm 'start' tới đúng CÂU PUNCH "
        "mạnh nhất trong đoạn, cắt bỏ phần dẫn nhập. Câu đầu của clip PHẢI là câu mạnh nhất.\n"
        "  ⚠ Chỉ được đặt start ở ĐẦU MỘT CÂU (không cắt giữa câu).\n"
        "→ Đoạn giảng giải đều đều, không có mũi nhọn ở đầu → BỎ, dù nội dung đúng.\n\n"

        # ---------- LỚP 3: TRỌN Ý + HIỂU ĐỘC LẬP ----------
        "▓ SAU KHI đã có hook mạnh, đoạn phải:\n"
        "- TRỌN VẸN MỘT Ý: được dời START tới câu punch, nhưng TUYỆT ĐỐI KHÔNG dời END vào giữa ý. "
        "End phải đóng ở CÂU CHỐT trọn nghĩa (người nói đã nói HẾT ý). Cắt cụt phần kết = người xem bị "
        "'hút rồi hụt' = mất niềm tin vào cô San.\n"
        "- HIỂU ĐỘC LẬP: người xem chưa xem gì trước đó vẫn hiểu trọn. KHÔNG bắt đầu bằng / KHÔNG dựa vào "
        "'cái đó', 'như vậy', 'phần một', 'ví dụ vừa rồi'... mà người xem chưa nghe. Nếu cần một câu "
        "setup ngắn, câu đó phải NẰM TRONG đoạn.\n"
        f"- ĐỘ DÀI {lo}-{hi} GIÂY (chuẩn TikTok/Reels). Ý trọn quan trọng hơn con số: nếu một ý cần dài "
        f"hơn mới trọn, lấy KHÚC ĐẮT NHẤT vẫn trọn nghĩa, cố không vượt ~{hi + 5}s.\n"
        "- start/end PHẢI khớp mốc giây CÓ THẬT trong bản bóc lời.\n\n"

        # ---------- LỚP 4: viral_score ĐA-TIÊU-CHÍ + TRẦN ----------
        "▓ CHẤM viral_score (0-10) — KHÔNG chỉ theo hook, phải phản ánh CẢ 3 mục tiêu:\n"
        "  • ~35% sức CHẶN-LƯỚT (hook mạnh cỡ nào).\n"
        "  • ~30% khả năng LƯU/CHIA SẺ (rút ra được điều đáng lưu / chạm đúng nỗi đau).\n"
        "  • ~35% KÉO-VỀ-HỌC + UY TÍN (chiều sâu, dấu ấn riêng cô San).\n"
        "CỘNG ĐIỂM cho đoạn có: reframe lật niềm tin ('X không phải Y, X là Z' / 'Đừng hỏi A, hãy hỏi B'); "
        "nỗi đau gọi đúng tên; con số / ca học viên / khách thật; câu 'maxim' ngắn gọn đáng chép lại; "
        "và TIP CÓ CHIỀU SÂU (gắn nguyên lý gốc, chừa bước thực thi chi tiết).\n"
        "⛔ TRẦN 6 ĐIỂM: đoạn hook mạnh NHƯNG RỖNG (tung câu sốc rồi không giải thích, không có insight, "
        "không mang dấu ấn cô San) → TỐI ĐA 6 điểm, đừng để lên top.\n"
        "Thang tham chiếu: 9-10 = hook tức thì + giá trị rút ra được + có chiều sâu/uy tín; "
        "7-8 = hook rõ + có mũi nhọn, giá trị khá; 5-6 = tạm ổn nhưng không bùng; <5 = mở nhạt → ĐỪNG trả về.\n"
        "⇒ XẾP đoạn viral_score CAO NHẤT LÊN ĐẦU mảng (đoạn đầu sẽ được dùng làm clip mồi).\n\n"

        # ---------- LỚP 5: ANTI-GOAL ----------
        "▓ TUYỆT ĐỐI LOẠI (đừng chọn các dạng sau):\n"
        "  ✗ Clickbait rỗng: tung câu sốc/hứa hẹn rồi BỎ LỬNG, không hề nói ra 'điều đó' là gì.\n"
        "  ✗ Đoạn cắt CỤT khi cô San chưa chốt xong ý (câu/kết luận đang dở).\n"
        "  ✗ Đoạn định-nghĩa-khái-niệm-khô / dạo đầu / chuyển ý, không có cao trào.\n"
        "  ✗ Tip phổ thông ai cũng nói, không mang dấu ấn riêng cô San.\n"
        "  ✗ Đoạn cần nghe phần trước mới hiểu (tham chiếu ngoài đoạn).\n"
        "  ⚑ PHÂN BIỆT RÕ: 'chừa BƯỚC THỰC THI CHI TIẾT' để người xem muốn học thêm = TỐT (được cộng điểm). "
        "'Giấu Ý CHÍNH / cắt giữa câu / để đoạn mập mờ khó hiểu' = CẤM. Chừa cách-làm ≠ giấu vấn đề.\n\n"

        # ---------- SỐ LƯỢNG ----------
        f"▓ SỐ LƯỢNG: nếu người dùng yêu cầu số clip cụ thể thì làm ĐÚNG số đó; nếu không, tự chọn số hợp lý, "
        f"TỐI ĐA {num_clips} đoạn. Thà TRẢ ÍT HƠN {num_clips} còn hơn độn thêm đoạn nhạt — chỉ lấy đoạn "
        f"thật sự đạt chuẩn."
        f"{rep}{extra}\n\n"

        # ---------- FEW-SHOT ĐỐI CHIẾU ----------
        "▓ VÍ DỤ MẪU (học độ ưu tiên bằng đối chiếu — KHÔNG có trong bản bóc lời thật):\n\n"
        "— VÍ DỤ A (DỜI start bỏ dẫn nhập). Bản bóc lời giả định:\n"
        "[40-46] Ừ thì hôm nay tôi muốn chia sẻ một chút về chuyện quản lý nhân sự.\n"
        "[46-52] Nó cũng là điều nhiều người hỏi tôi.\n"
        "[52-59] Tôi từng sa thải cả một phòng ban chỉ trong một buổi sáng, mười hai con người.\n"
        "[59-66] Và đó là quyết định cứu cả công ty tôi khỏi phá sản.\n"
        "[66-72] Vì giữ người sai còn tốn kém hơn để trống ghế đó.\n"
        "→ SAI: start=40 để 'trọn ý' — nhưng 3 giây đầu 'Ừ thì hôm nay tôi muốn chia sẻ' quá nhạt, người lướt trôi mất.\n"
        "→ ĐÚNG (dời start tới câu punch):\n"
        '[{"start": 52, "end": 72, "title": "Tôi sa thải cả phòng ban trong 1 buổi sáng", '
        '"hook_line": "Tôi từng sa thải cả một phòng ban chỉ trong một buổi sáng, mười hai con người", '
        '"reason": "Mở bằng hành động sốc + con số (12 người) chặn-lướt tức thì; chốt bằng insight ngược đời '
        '\'giữ người sai tốn hơn ghế trống\' chạm đúng nỗi đau nhân sự của chủ DN", '
        '"category": "quan_diem_nguoc", "viral_score": 9, '
        '"caption": "Tôi sa thải cả một phòng ban trong 1 buổi sáng — và nó cứu công ty tôi khỏi phá sản. '
        'Bạn có đang giữ nhầm người vì sợ để trống ghế?", '
        '"hashtags": ["#dotruongsansan", "#kinhdoanh", "#quanlynhansu", "#chudoanhnghiep", "#khoinghiep"]}]\n\n'
        "— VÍ DỤ B (reframe chạm nỗi đau + chừa khoảng trống → CHỌN điểm cao). Bản bóc lời giả định:\n"
        "[120-125] Nhiều chủ doanh nghiệp nói với tôi là em tuyển hoài mà nhân viên cứ nghỉ.\n"
        "[125-131] Mỗi lần một bạn giỏi nghỉ là ôm theo một mớ khách, doanh thu tụt một cục.\n"
        "[131-138] Tôi hỏi lại một câu thôi: khách đó là khách của công ty em, hay khách của bạn nhân viên đó?\n"
        "[138-145] Nếu không trả lời được thì vấn đề không nằm ở người nghỉ. Nó nằm ở chỗ em chưa xây hệ thống để khách thuộc về công ty.\n"
        "[145-150] Người ta lo giữ người. Tôi lo em xây cái mà không ai lấy đi được.\n"
        "→ ĐÚNG:\n"
        '[{"start": 125, "end": 150, "title": "Nhân viên nghỉ ôm mất khách? Bạn đang lo sai chỗ", '
        '"hook_line": "Mỗi lần một bạn giỏi nghỉ là ôm theo một mớ khách, doanh thu tụt một cục", '
        '"reason": "Chạm nỗi đau kinh điển của chủ DN nhỏ (mất người mất khách) rồi LẬT: vấn đề là chưa có hệ '
        'thống, không phải người nghỉ. Nêu nguyên lý nhưng CHỪA cách xây hệ thống → để hở khoảng trống kéo về học", '
        '"category": "quan_diem_nguoc", "viral_score": 9, '
        '"caption": "Bạn đang lo giữ người, hay đang xây thứ không ai lấy đi được? Khách rời đi cùng nhân viên '
        'không phải lỗi người nghỉ — đó là lỗ hổng hệ thống. Khách của bạn đang thuộc về công ty hay một cá nhân?", '
        '"hashtags": ["#dotruongsansan", "#chudoanhnghiep", "#quantridoanhnghiep", "#kinhdoanh", "#hethong"]}]\n\n'
        "— VÍ DỤ C (clickbait rỗng + tham chiếu ngoài đoạn → LOẠI). Bản bóc lời giả định:\n"
        "[40-43] Có một điều mà 90% chủ doanh nghiệp không bao giờ nhận ra.\n"
        "[43-45] Và nó giết chết doanh nghiệp của bạn từ bên trong.\n"
        "[45-47] Như vậy mình vừa nói xong phần một, qua phần hai nhé.\n"
        "→ LOẠI: hook rất mạnh (90% + tuyên bố sốc) NHƯNG đoạn KHÔNG hề nói 'điều đó' là gì — tung ra rồi cắt "
        "sang phần khác; câu cuối còn tham chiếu 'phần một/phần hai' nên KHÔNG hiểu độc lập. Đây là clickbait "
        "rỗng, kéo view rác + hạ uy tín. Nếu buộc phải dùng vùng này, phải kéo end tới lúc cô San THỰC SỰ giải "
        "thích xong 'điều đó'. Nếu ai cố chấm thì tối đa 5 điểm.\n\n"

        # ---------- OUTPUT ----------
        "▓ CHỈ trả về JSON THUẦN (KHÔNG markdown, KHÔNG giải thích ngoài JSON). Một mảng object, mỗi object:\n"
        '{"start": <số giây, khớp mốc transcript, ngay câu punch>, '
        '"end": <số giây, khớp mốc transcript, đóng ở câu chốt trọn nghĩa>, '
        '"title": "<tít viral theo công thức con số/nghịch lý/nỗi đau/câu hỏi — KHÔNG mô tả trung tính kiểu '
        '\'đoạn nói về...\'>", '
        '"hook_line": "<câu mở 3 giây đầu, TRÍCH SÁT hoặc rút gọn ĐÚNG lời có thật trong transcript — KHÔNG bịa>", '
        '"reason": "<vì sao đoạn này viral: nêu rõ chất hook (số? ngược đời? đau?) và chạm tiêu chí nào>", '
        '"category": "<một trong: tip | tip_chieu_sau | cau_chuyen | quote_dat | quan_diem_nguoc | khung_tu_duy | noi_dau>", '
        '"viral_score": <số 0-10 theo rubric trên>, '
        '"caption": "<mô tả bài đăng 1-3 câu, giọng cô San (thực chiến, thẳng), mở bằng hook_line hoặc biến thể, '
        'kết bằng 1 câu hỏi kéo comment hoặc CTA ngầm; KHÔNG bịa số liệu không có trong đoạn>", '
        '"hashtags": [<mảng 4-8 chuỗi có dấu #, gồm #dotruongsansan + tag tệp chủ DN (vd #chudoanhnghiep '
        '#kinhdoanh #quantri) + 1-2 tag ngách theo chủ đề đoạn>]}\n'
        "LƯU Ý: start/end/viral_score là SỐ THỰC, KHÔNG kèm chữ 'giây' hay dấu nháy. hashtags là mảng string thật."
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
        try:
            score = float(d.get("viral_score", 0))
        except (TypeError, ValueError):
            score = 0.0
        tags = d.get("hashtags", [])
        hashtags = ([str(t).strip() for t in tags if str(t).strip()]
                    if isinstance(tags, list) else [])
        segs.append({
            "start": s, "end": e,
            "title": str(d.get("title", "")).strip(),
            "hook_line": str(d.get("hook_line", "")).strip(),
            "reason": str(d.get("reason", "")).strip(),
            "category": str(d.get("category", "")).strip(),
            "viral_score": score,
            "caption": str(d.get("caption", "")).strip(),
            "hashtags": hashtags,
        })
    # XẾP theo điểm viral giảm dần — top-1 dùng làm clip mồi (orchestrator kỳ vọng)
    segs.sort(key=lambda x: x["viral_score"], reverse=True)
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
