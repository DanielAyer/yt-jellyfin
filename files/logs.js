/*
 * logs.js — log viewer with history load and live SSE follow mode
 *
 * On load: fetches last 200 lines from /api/logs
 * Follow mode: connects to /api/logs/stream via SSE, appends new lines live
 */

let _activeFilter = "all";
let _following    = false;
let _evtSource    = null;
let _allLines     = [];   // full history for client-side filtering

/* ── API helper ──────────────────────────────────────────────────────── */
async function api(path) {
  const r = await fetch(path);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
  return data;
}

/* ── log level detection ─────────────────────────────────────────────── */
function _detectLevel(line) {
  if (line.includes(" ERROR ")   || line.includes("[ERROR]"))   return "ERROR";
  if (line.includes(" WARNING ")  || line.includes("[WARNING]")) return "WARNING";
  if (line.includes(" INFO ")    || line.includes("[INFO]"))    return "INFO";
  if (line.includes(" DEBUG ")   || line.includes("[DEBUG]"))   return "DEBUG";
  return "other";
}

/* ── render a single line ────────────────────────────────────────────── */
function _makeLine(text) {
  const level = _detectLevel(text);
  const el    = document.createElement("span");
  el.className   = `log-line level-${level}`;
  el.textContent = text;
  el.dataset.level = level;
  if (_activeFilter !== "all" && level !== _activeFilter) {
    el.classList.add("hidden");
  }
  return el;
}

function _appendLine(text) {
  const output = document.getElementById("log-output");
  const el = _makeLine(text);
  _allLines.push(el);
  output.appendChild(el);
  if (_following) {
    el.scrollIntoView({ behavior: "smooth", block: "end" });
  }
}

/* ── load history ────────────────────────────────────────────────────── */
async function loadHistory() {
  const status = document.getElementById("log-status");
  try {
    const data = await api("/api/logs?lines=200");
    const lines = data.lines || [];

    if (lines.length === 0) {
      status.textContent = "No log entries found.";
      return;
    }

    lines.forEach(line => _appendLine(line));
    status.textContent = `${lines.length} lines loaded.`;

    // Scroll to bottom on initial load
    const container = document.getElementById("log-container");
    container.scrollTop = container.scrollHeight;

  } catch (e) {
    status.textContent = `Failed to load logs: ${e.message}`;
    status.style.color = "var(--danger)";
  }
}

/* ── filter ──────────────────────────────────────────────────────────── */
document.querySelectorAll(".log-filter-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    _activeFilter = btn.dataset.level;
    document.querySelectorAll(".log-filter-btn").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");

    // Show/hide existing lines
    _allLines.forEach(el => {
      const level = el.dataset.level;
      const show  = _activeFilter === "all" || level === _activeFilter;
      el.classList.toggle("hidden", !show);
    });

    const visible = _allLines.filter(el => !el.classList.contains("hidden")).length;
    document.getElementById("log-status").textContent =
      `Showing ${visible} of ${_allLines.length} lines.`;
  });
});

/* ── follow live ─────────────────────────────────────────────────────── */
const followBtn = document.getElementById("btn-follow");

followBtn.addEventListener("click", () => {
  if (_following) {
    _stopFollow();
  } else {
    _startFollow();
  }
});

function _startFollow() {
  _following = true;
  followBtn.textContent = "Stop following";
  followBtn.classList.add("follow-active");
  document.getElementById("log-status").textContent = "Following live…";

  _evtSource = new EventSource("/api/logs/stream");

  _evtSource.onmessage = (e) => {
    if (e.data) _appendLine(e.data);
  };

  _evtSource.onerror = () => {
    document.getElementById("log-status").textContent = "Live stream disconnected. Click Follow to reconnect.";
    _stopFollow();
  };
}

function _stopFollow() {
  _following = false;
  followBtn.textContent = "Follow live";
  followBtn.classList.remove("follow-active");
  if (_evtSource) {
    _evtSource.close();
    _evtSource = null;
  }
  const visible = _allLines.filter(el => !el.classList.contains("hidden")).length;
  document.getElementById("log-status").textContent =
    `${visible} lines — follow stopped.`;
}

/* ── download ────────────────────────────────────────────────────────── */
document.getElementById("btn-download-log").addEventListener("click", () => {
  const text = _allLines.map(el => el.textContent).join("\n");
  const blob = new Blob([text], { type: "text/plain" });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement("a");
  a.href     = url;
  a.download = `yt-jellyfin-${new Date().toISOString().slice(0,19).replace(/:/g,"-")}.log`;
  a.click();
  URL.revokeObjectURL(url);
});

/* ── disk warning (shared) ───────────────────────────────────────────── */
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

setInterval(checkDiskSpace, 60_000);

/* ── init ────────────────────────────────────────────────────────────── */
loadHistory();
checkDiskSpace();
