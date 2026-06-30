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

/* ── download button ─────────────────────────────────────────────────── */
function updateDownloadBtn() {
  const btn = document.getElementById("btn-download-selected");
  const cnt = document.getElementById("selected-count");
  cnt.textContent = selected.size;
  btn.disabled = selected.size === 0;
}

document.getElementById("btn-download-selected").addEventListener("click", async () => {
  if (selected.size === 0) return;
  const btn    = document.getElementById("btn-download-selected");
  const status = document.getElementById("download-status");

  btn.disabled = true;
  const ids = [...selected];

  status.classList.remove("hidden");
  status.innerHTML = `<span class="spinner"></span> Queuing ${ids.length} download${ids.length !== 1 ? "s" : ""}…`;

  try {
    await api(`/api/channels/${CHANNEL_ID}/download`, {
      method: "POST",
      body: { video_ids: ids },
    });
    status.innerHTML = `✓ ${ids.length} download${ids.length !== 1 ? "s" : ""} started in background. Refresh when complete.`;
    selected.clear();
    updateDownloadBtn();
  } catch (e) {
    status.innerHTML = `✗ Failed: ${e.message}`;
    btn.disabled = false;
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

/* ── init ────────────────────────────────────────────────────────────── */
loadVideos();
