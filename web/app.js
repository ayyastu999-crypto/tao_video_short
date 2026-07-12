// Giao diện: gửi link đi xử lý, hỏi thăm tiến độ, hiện clip kết quả.
const $ = (id) => document.getElementById(id);
let pollTimer = null;

$("form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const url = $("url").value.trim();
  const file = $("file").files[0];
  const fpath = $("filepath").value.trim();
  const lang = $("lang").value;

  if (!url && !file && !fpath) {
    showError("Hãy dán link, chọn file, hoặc dán đường dẫn file trên máy.");
    return;
  }

  resetUI();
  $("go").disabled = true;
  $("progress").classList.remove("hidden");

  try {
    // Luôn gửi form-data (kèm được cả file video, logo, nhạc nền)
    const fd = new FormData();
    fd.append("url", url);
    if (file) fd.append("file", file);
    if (fpath) fd.append("file_path", fpath);
    const logo = $("logo").files[0];
    if (logo) fd.append("logo", logo);
    const music = $("music").files[0];
    if (music) fd.append("music", music);
    const reface = $("reface").files[0];
    if (reface) fd.append("reference_face", reface);
    fd.append("language", lang || "");
    fd.append("max_minutes", String(parseInt($("maxmin").value, 10) || 0));
    fd.append("reframe", $("reframe").checked ? "1" : "0");
    fd.append("keywords", $("keywords").value.trim());
    fd.append("use_ai", $("useai").checked ? "1" : "0");
    fd.append("prefer_repeat", $("repeat").checked ? "1" : "0");

    const res = await fetch("/api/process", { method: "POST", body: fd });
    if (!res.ok) {
      const j = await res.json().catch(() => ({}));
      throw new Error(j.detail || "Lỗi gửi yêu cầu");
    }
    const { job_id } = await res.json();
    pollTimer = setInterval(() => poll(job_id), 1500);
  } catch (err) {
    showError(err.message);
    $("go").disabled = false;
  }
});

async function poll(jobId) {
  try {
    const res = await fetch(`/api/jobs/${jobId}`);
    const job = await res.json();
    setProgress(job.percent || 0, job.message || "Đang xử lý…");

    if (job.status === "done") {
      clearInterval(pollTimer);
      $("go").disabled = false;
      renderResults(job.result);
    } else if (job.status === "error") {
      clearInterval(pollTimer);
      $("go").disabled = false;
      showError((job.error || "Lỗi không rõ") + (job.trace ? "\n\n" + job.trace : ""));
    }
  } catch (err) {
    // Lỗi mạng tạm thời khi poll — bỏ qua, lần sau thử lại
  }
}

function setProgress(pct, msg) {
  $("barfill").style.width = Math.min(100, pct) + "%";
  $("status").textContent = `${msg}  (${Math.round(pct)}%)`;
}

function renderResults(result) {
  const grid = $("grid");
  grid.innerHTML = "";
  const aiNote = result.ai_used
    ? " · 🤖 AI đã chọn"
    : " · ⚙️ chọn bằng LUẬT" +
      (result.ai_note ? ` — ⚠️ AI không dùng được: ${result.ai_note}` : "");
  $("results-title").textContent =
    `Kết quả: ${result.clips.length} clip từ "${result.title}"${aiNote}`;

  for (const c of result.clips) {
    const enc = encodeURIComponent(c.file);
    const tags = (c.hashtags || []).join(" ");
    // Ưu tiên caption AI viết; thiếu thì lùi về tiêu đề + hashtag
    const caption = (c.caption ? `${c.caption}\n\n${tags}` : `${c.title || ""}\n\n${tags}`).trim();
    const reasons = (c.reasons || []).map((r) => `<span class="chip">${esc(r)}</span>`).join("");

    const isHook = c.kind === "hook";
    const label = (isHook ? "★ HOOK MỒI" : "Clip " + String(c.index).padStart(2, "0"))
      + (c.by_ai ? " 🤖" : "");
    // AI có điểm viral → hiện 🔥 x/10; luật cũ → ★ điểm heuristic
    const scoreBadge = (c.by_ai && c.viral_score)
      ? "🔥 " + c.viral_score + "/10"
      : (c.by_ai ? "🤖 AI" : "★ " + c.score);
    const catChip = c.category ? `<span class="chip cat">${esc(c.category)}</span>` : "";
    const hookLine = c.hook_line
      ? `<div class="hookline">🎬 ${esc(c.hook_line)}</div>` : "";

    const card = document.createElement("div");
    card.className = "card" + (isHook ? " is-hook" : "");
    card.innerHTML = `
      <video controls preload="metadata" src="/output/${enc}"></video>
      <div class="meta">
        <div class="row">
          <span class="idx">${label}</span>
          <span class="score">${scoreBadge}</span>
        </div>
        <div class="range">✂️ ${mmss(c.start)} → ${mmss(c.end)} · ${c.duration}s (trong video gốc)</div>
        <div class="title">${esc(c.title || "")}</div>
        ${hookLine}
        <div class="tags">${esc(tags)}</div>
        <div class="reasons">${catChip}${reasons}</div>
      </div>
      <div class="actions">
        <button class="copy" type="button">Copy caption</button>
        <a class="dl" href="/output/${enc}" download>Tải</a>
      </div>`;

    const btn = card.querySelector(".copy");
    btn.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(caption);
        btn.textContent = "Đã copy ✓";
        setTimeout(() => (btn.textContent = "Copy caption"), 1500);
      } catch {
        btn.textContent = "Không copy được";
      }
    });

    grid.appendChild(card);
  }
  $("results").classList.remove("hidden");
}

function showError(msg) {
  const box = $("error");
  box.textContent = "Có lỗi xảy ra:\n" + msg;
  box.classList.remove("hidden");
  $("progress").classList.add("hidden");
}

function resetUI() {
  if (pollTimer) clearInterval(pollTimer);
  $("error").classList.add("hidden");
  $("results").classList.add("hidden");
  $("grid").innerHTML = "";
  setProgress(0, "Đang chuẩn bị…");
}

function esc(s) {
  return String(s).replace(/[&<>"]/g, (m) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[m]));
}

function mmss(s) {
  s = Math.max(0, Math.round(s || 0));
  return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
}
