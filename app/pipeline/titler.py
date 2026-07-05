"""Tự sinh tiêu đề + hashtag cho mỗi clip (bản heuristic, không cần API).

Tiêu đề = câu mở đầu (câu "móc") của clip, rút gọn, thêm emoji theo chủ đề.
Hashtag = suy từ chủ đề nhận ra trong lời thoại + vài tag mặc định.
Thiết kế để dễ thay bằng LLM sau: chỉ cần đổi hàm make_caption.
"""
import re

from .segmenter import Candidate

# Chủ đề → emoji (chèn vào tiêu đề — an toàn vì chỉ hiện ở text/caption, không nung video)
THEME_EMOJI = [
    (("ai", "trí tuệ", "công nghệ", "chatgpt"), "🤖"),
    (("tiền", "doanh thu", "lợi nhuận", "giàu", "thu nhập"), "💰"),
    (("kinh doanh", "bán hàng", "khách hàng", "doanh nghiệp"), "📈"),
    (("marketing", "content", "quảng cáo", "thương hiệu"), "🎯"),
    (("thành công", "phát triển", "bứt phá", "cơ hội"), "🚀"),
    (("sai lầm", "thất bại", "cẩn thận", "rủi ro"), "⚠️"),
]

# Chủ đề → hashtag
THEME_TAGS = [
    (("ai", "trí tuệ", "công nghệ", "chatgpt"), "#AI"),
    (("tiền", "doanh thu", "lợi nhuận", "kinh doanh", "bán"), "#kinhdoanh"),
    (("marketing", "content", "quảng cáo", "thương hiệu"), "#marketing"),
    (("thành công", "phát triển", "bứt phá", "cơ hội"), "#pháttriển"),
    (("kỹ năng", "học", "bài học", "kinh nghiệm"), "#kỹnăng"),
]

DEFAULT_TAGS = ["#shorts", "#reels", "#viral"]
MAX_TITLE_LEN = 70


def _themes(low: str, table):
    hits = []
    for keys, val in table:
        if any(k in low for k in keys):
            hits.append(val)
    return hits


def make_caption(cand: Candidate) -> dict:
    """Trả về {title, hashtags, caption} cho 1 clip."""
    text = cand.text
    low = text.lower()

    # Tiêu đề: lấy câu đầu (câu móc), gọn gàng
    title = cand.sentences[0].text.strip() if cand.sentences else text
    title = re.sub(r"\s+", " ", title).strip(" .,-")
    if len(title) > MAX_TITLE_LEN:
        title = title[:MAX_TITLE_LEN].rsplit(" ", 1)[0] + "…"

    emojis = _themes(low, THEME_EMOJI)
    if emojis:
        title = f"{emojis[0]} {title}"

    # Hashtag: theo chủ đề + mặc định, khử trùng, tối đa 6
    tags = _themes(low, THEME_TAGS) + DEFAULT_TAGS
    seen, hashtags = set(), []
    for t in tags:
        if t.lower() not in seen:
            seen.add(t.lower())
            hashtags.append(t)
        if len(hashtags) >= 6:
            break

    caption = f"{title}\n\n{' '.join(hashtags)}"
    return {"title": title, "hashtags": hashtags, "caption": caption}
