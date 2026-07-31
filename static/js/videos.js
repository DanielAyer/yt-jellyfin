/*
 * videos.js — per-channel video grid with selection and download.
 *
 * TODO: refactor tile state updates to use real-time push (SSE) or a
 *       batched polling loop once multi-user / remote deployment is targeted.
 *       Currently uses manual refresh via the ↺ Refresh button.
 */

const CHANNEL_ID = window.CHANNEL_ID;

let allVideos   = [];
let selected    = new Set();   // video_ids selected for download
let activeFilter = "all";

/* ── API ─────────────────────────────────────────────────────────────── */
async function api(path, { method = "GET", body } = {}) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const r = await fetch(path, opts);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
  return data;
}

/* ── date formatting ─────────────────────────────────────────────────── */
function fmtUploadDate(dateStr) {
  if (!dateStr || dateStr.length !== 8) return dateStr || "";
  const y = dateStr.slice(0, 4);
  const m = dateStr.slice(4, 6);
  const d = dateStr.slice(6, 8);
  return new Date(`${y}-${m}-${d}`).toLocaleDateString(undefined, {
    year: "numeric", month: "short", day: "numeric"
  });
}

/* ── load videos ─────────────────────────────────────────────────────── */
async function loadVideos() {
  try {
    allVideos = await api(`/api/channels/${CHANNEL_ID}/videos`);
    renderGrid();
  } catch (e) {
    document.getElementById("grid-empty").innerHTML =
      `<span class="empty-icon">⚠</span><p>Failed to load videos: ${e.message}</p>`;
  }
}

/* ── filter ──────────────────────────────────────────────────────────── */
function filteredVideos() {
  if (activeFilter === "pending")    return allVideos.filter(v => v.status !== "downloaded");
  if (activeFilter === "downloaded") return allVideos.filter(v => v.status === "downloaded");
  return allVideos;
}

/* ── render grid ─────────────────────────────────────────────────────── */
function renderGrid() {
  const grid  = document.getElementById("video-grid");
  const empty = document.getElementById("grid-empty");
  const tpl   = document.getElementById("tpl-video-tile");
  const videos = filteredVideos();

  [...grid.querySelectorAll(".video-tile")].forEach(t => t.remove());

  if (videos.length === 0) {
    empty.classList.remove("hidden");
    empty.innerHTML = `<span class="empty-icon">📭</span><p>No videos in this view.</p>`;
    return;
  }
  empty.classList.add("hidden");

  videos.forEach(v => {
    const frag = tpl.content.cloneNode(true);
    const tile = frag.querySelector(".video-tile");

    tile.dataset.videoId = v.video_id;
    tile.dataset.status  = v.status;

    const isDownloaded = v.status === "downloaded";
    if (isDownloaded) tile.classList.add("downloaded");
    if (selected.has(v.video_id)) tile.classList.add("selected");

    // thumbnail
    const img = tile.querySelector(".tile-thumb");
    if (v.thumbnail_url) {
      img.src = v.thumbnail_url;
      img.alt = v.title;
    } else {
      // fallback: construct YouTube thumbnail URL from video_id
      img.src = `https://i.ytimg.com/vi/${v.video_id}/hqdefault.jpg`;
      img.alt = v.title;
    }

    // badges
    const selBadge  = tile.querySelector(".tile-badge");
    const dlBadge   = tile.querySelector(".tile-downloaded-badge");
    if (selected.has(v.video_id)) selBadge.classList.remove("hidden");
    if (isDownloaded) dlBadge.classList.remove("hidden");

    // info
    tile.querySelector(".tile-title").textContent = v.title || v.video_id;
    tile.querySelector(".tile-date").textContent  = fmtUploadDate(v.upload_date);

    // click handler
    tile.addEventListener("click", () => toggleTile(tile, v.video_id));

    grid.appendChild(frag);
  });

  updateDownloadBtn();
}

/* ── tile toggle ─────────────────────────────────────────────────────── */
function toggleTile(tile, videoId) {
  // press animation
  tile.classList.remove("pressing");
  void tile.offsetWidth; // reflow to restart animation
  tile.classList.add("pressing");
  tile.addEventListener("animationend", () => tile.classList.remove("pressing"), { once: true });

  const badge = tile.querySelector(".tile-badge");

  if (selected.has(videoId)) {
    selected.delete(videoId);
    tile.classList.remove("selected");
    badge.classList.add("hidden");
  } else {
    selected.add(videoId);
    tile.classList.add("selected");
    badge.classList.remove("hidden");
  }
  updateDownloadBtn();
}

/* ── select all / deselect all ───────────────────────────────────────── */
document.getElementById("btn-select-all").addEventListener("click", () => {
  filteredVideos().forEach(v => selected.add(v.video_id));
  renderGrid();
});

document.getElementById("btn-deselect-all").addEventListener("click", () => {
  selected.clear();
  renderGrid();
});

/* ── channel settings (N and M) ──────────────────────────────────────── */
let _chSettings = { n_catalog: 5, m_recent: 5 };

async function loadChannelSettings() {
  try {
    _chSettings = await api(`/api/channels/${CHANNEL_ID}/settings`);
    document.getElementById("n-label").textContent = _chSettings.n_catalog;
    document.getElementById("m-label").textContent = _chSettings.m_recent;
  } catch (_) {}
}

/* ── size estimate ───────────────────────────────────────────────────── */
async function showSizeEstimate(videoIds) {
  const el = document.getElementById("size-estimate");
  if (!videoIds || videoIds.length === 0) { el.classList.add("hidden"); return; }
  el.classList.remove("hidden");
  el.className = "size-estimate";
  el.textContent = "Estimating size…";
  try {
    const est = await api(`/api/channels/${CHANNEL_ID}/estimate`, {
      method: "POST", body: { video_ids: videoIds },
    });
    el.className = "size-estimate loaded";
    el.textContent = `${videoIds.length} video${videoIds.length !== 1 ? "s" : ""} — estimated ${est.estimated_gb >= 1 ? est.estimated_gb + " GB" : est.estimated_mb + " MB"} (${est.note})`;
  } catch (_) {
    el.textContent = "Could not estimate size.";
  }
}

/* ── active download tracking ────────────────────────────────────────── */
let _downloadActive = false;

function setDownloadActive(active) {
  _downloadActive = active;
  const stopBtn = document.getElementById("btn-force-stop");
  const dlBtns  = ["btn-dl-all", "btn-dl-next-n", "btn-dl-latest-m", "btn-dl-selected"];
  stopBtn.classList.toggle("hidden", !active);
  dlBtns.forEach(id => {
    const btn = document.getElementById(id);
    if (btn) btn.disabled = active;
  });
}

/* ── download buttons ────────────────────────────────────────────────── */
function updateDownloadBtn() {
  const btn = document.getElementById("btn-dl-selected");
  const cnt = document.getElementById("selected-count");
  cnt.textContent = selected.size;
  btn.disabled = selected.size === 0 || _downloadActive;
  // show size estimate for selection
  if (selected.size > 0) showSizeEstimate([...selected]);
  else document.getElementById("size-estimate").classList.add("hidden");
}

async function _startDownload(endpoint, label, body = {}) {
  const statusEl = document.getElementById("download-status");
  statusEl.classList.remove("hidden");
  statusEl.innerHTML = `<span class="spinner"></span> ${label}…`;
  setDownloadActive(true);
  try {
    await api(endpoint, { method: "POST", body });
    statusEl.innerHTML = `✓ ${label} started. Refresh when complete.`;
    selected.clear();
    updateDownloadBtn();
  } catch (e) {
    if (e.message === "low_disk") {
      checkDiskSpace();
      showAlert("Download blocked: disk space is below threshold. Free up space or adjust in Settings.");
      statusEl.classList.add("hidden");
    } else {
      statusEl.innerHTML = `✗ Failed: ${e.message}`;
    }
    setDownloadActive(false);
  }
}

document.getElementById("btn-dl-all").addEventListener("click", async () => {
  const pending = allVideos.filter(v => v.status !== "downloaded");
  await showSizeEstimate(pending.map(v => v.video_id));
  if (!confirm(`Download all ${pending.length} undownloaded videos?`)) return;
  await _startDownload(`/api/channels/${CHANNEL_ID}/download/all`, `Downloading all ${pending.length} videos`);
});

document.getElementById("btn-dl-next-n").addEventListener("click", async () => {
  const n = _chSettings.n_catalog;
  const pending = allVideos.filter(v => v.status !== "downloaded")
    .sort((a, b) => a.upload_date?.localeCompare(b.upload_date)).slice(0, n);
  await showSizeEstimate(pending.map(v => v.video_id));
  if (!confirm(`Download next ${n} back-catalog videos?`)) return;
  await _startDownload(`/api/channels/${CHANNEL_ID}/download/next-n`, `Downloading next ${n} videos`, { n });
});

document.getElementById("btn-dl-latest-m").addEventListener("click", async () => {
  const m = _chSettings.m_recent;
  const pending = allVideos.filter(v => v.status !== "downloaded")
    .sort((a, b) => b.upload_date?.localeCompare(a.upload_date)).slice(0, m);
  await showSizeEstimate(pending.map(v => v.video_id));
  if (!confirm(`Download latest ${m} recent videos?`)) return;
  await _startDownload(`/api/channels/${CHANNEL_ID}/download/latest-m`, `Downloading latest ${m} videos`, { m });
});

document.getElementById("btn-dl-selected").addEventListener("click", async () => {
  if (selected.size === 0) return;
  const ids = [...selected];
  if (!confirm(`Download ${ids.length} selected video${ids.length !== 1 ? "s" : ""}?`)) return;
  await _startDownload(
    `/api/channels/${CHANNEL_ID}/download`,
    `Downloading ${ids.length} selected videos`,
    { video_ids: ids }
  );
});

/* ── force stop ──────────────────────────────────────────────────────── */
document.getElementById("btn-force-stop").addEventListener("click", async () => {
  if (!confirm("Stop the current download? Completed videos are safe. The current in-progress file will be cleaned up.")) return;
  try {
    const result = await api(`/api/channels/${CHANNEL_ID}/stop`, { method: "POST" });
    const statusEl = document.getElementById("download-status");
    statusEl.innerHTML = `⏹ ${result.message}`;
    setDownloadActive(false);
  } catch (e) {
    showAlert(`Stop failed: ${e.message}`);
  }
});

/* ── refresh ─────────────────────────────────────────────────────────── */
document.getElementById("btn-refresh").addEventListener("click", async () => {
  const btn = document.getElementById("btn-refresh");
  btn.textContent = "↺ Loading…";
  btn.disabled = true;
  await loadVideos();
  btn.textContent = "↺ Refresh";
  btn.disabled = false;
});

/* ── filter buttons ──────────────────────────────────────────────────── */
document.querySelectorAll(".filter-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".filter-btn").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    activeFilter = btn.dataset.filter;
    renderGrid();
  });
});

/* ── disk warning banner ─────────────────────────────────────────────── */
let _diskWarningDismissed = false;

async function checkDiskSpace() {
  try {
    const status = await fetch("/api/disk/status").then(r => r.json());
    const banner = document.getElementById("disk-warning-banner");
    const text   = document.getElementById("disk-warning-text");
    if (!status.ok) {
      text.textContent = `⚠ Warning: Low Disk Space — ${status.free_pct?.toFixed(1)}% free`;
      if (!_diskWarningDismissed) banner.classList.remove("hidden");
      else { _diskWarningDismissed = false; banner.classList.remove("hidden"); }
    } else {
      banner.classList.add("hidden");
      _diskWarningDismissed = false;
    }
  } catch (_) {}
}

document.getElementById("disk-warning-close")?.addEventListener("click", () => {
  document.getElementById("disk-warning-banner").classList.add("hidden");
  _diskWarningDismissed = true;
});

document.getElementById("alert-bar-close")?.addEventListener("click", () => {
  document.getElementById("alert-bar").classList.add("hidden");
});

function showAlert(message) {
  const bar  = document.getElementById("alert-bar");
  const text = document.getElementById("alert-bar-text");
  if (!bar || !text) return;
  text.textContent = message;
  bar.classList.remove("hidden");
}

setInterval(checkDiskSpace, 60_000);

/* ── init ────────────────────────────────────────────────────────────── */
checkDiskSpace();
loadChannelSettings();
loadVideos();
