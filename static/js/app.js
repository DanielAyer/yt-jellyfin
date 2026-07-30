/* ── state ────────────────────────────────────────────────────────────── */
let channels = [];
let activeCatalogId = null;

/* ── API helpers ─────────────────────────────────────────────────────── */
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

/* ── channel rendering ───────────────────────────────────────────────── */
function fmtDate(iso) {
  if (!iso) return "never";
  const d = new Date(iso.endsWith("Z") ? iso : iso + "Z");
  return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function buildCard(ch) {
  const tpl = document.getElementById("tpl-channel-card");
  const frag = tpl.content.cloneNode(true);
  const card = frag.querySelector(".channel-card");

  card.dataset.channelId = ch.channel_id;
  card.querySelector(".card-name").textContent = ch.channel_name;

  // thumbnail
  const thumb = card.querySelector(".card-thumbnail");
  if (ch.thumbnail_url) {
    thumb.src = ch.thumbnail_url;
    thumb.alt = ch.channel_name;
  } else {
    thumb.style.display = "none";
    card.querySelector(".card-hero").style.background = "var(--surface2)";
  }

  const urlEl = card.querySelector(".card-url");
  urlEl.href = ch.channel_url;
  urlEl.textContent = ch.channel_url.replace("https://www.youtube.com/", "yt/");

  updateCardStats(card, ch);

  card.querySelector(".card-remove").addEventListener("click", (e) => {
    e.stopPropagation();
    removeChannel(ch.channel_id, card);
  });
  card.querySelector(".btn-sync").addEventListener("click", () => syncChannel(ch.channel_id, card));
  card.querySelector(".btn-rebase").addEventListener("click", () => rebaseChannel(ch.channel_id, card));
  card.querySelector(".btn-catalog").addEventListener("click", () => openCatalog(ch));
  card.querySelector(".btn-more-videos").addEventListener("click", () => {
    window.location.href = `/channel/${ch.channel_id}/videos`;
  });

  return card;
}

function updateCardStats(card, ch) {
  card.querySelector(".stat-available").textContent  = ch.total_available ?? "—";
  card.querySelector(".stat-downloaded").textContent = ch.downloaded_count ?? ch.total_downloaded ?? "—";
  card.querySelector(".stat-available-count").textContent = ch.pending_count ?? "—";
  card.querySelector(".last-checked-val").textContent = fmtDate(ch.last_checked);
}

function setCardBusy(card, busy, label = "Working…") {
  card.classList.toggle("busy", busy);
  card.querySelector(".card-busy").classList.toggle("hidden", !busy);
  card.querySelector(".busy-label").textContent = label;
  card.querySelectorAll(".btn-sync, .btn-rebase, .btn-catalog, .btn-more-videos, .card-remove")
      .forEach(b => b.disabled = busy);
}

function renderChannels() {
  const grid = document.getElementById("channel-grid");
  const empty = document.getElementById("empty-state");
  [...grid.querySelectorAll(".channel-card")].forEach(c => c.remove());

  if (channels.length === 0) {
    empty.classList.remove("hidden");
    return;
  }
  empty.classList.add("hidden");
  channels.forEach(ch => grid.appendChild(buildCard(ch)));
}

/* ── load channels ───────────────────────────────────────────────────── */
async function loadChannels() {
  try {
    channels = await api("/api/channels");
    renderChannels();
  } catch (e) {
    console.error("Failed to load channels", e);
  }
}

/* ── add channel ─────────────────────────────────────────────────────── */
document.getElementById("btn-add").addEventListener("click", async () => {
  const input  = document.getElementById("channel-url-input");
  const status = document.getElementById("add-status");
  const url    = input.value.trim();
  if (!url) return;

  status.className = "add-status";
  status.textContent = "Resolving channel…";
  document.getElementById("btn-add").disabled = true;

  try {
    const r = await api("/api/channels", { method: "POST", body: { url } });
    status.className = "add-status ok";
    status.textContent = `✓ Added "${r.channel_name}" — fetching video list in background…`;
    input.value = "";
    await loadChannels();
    setTimeout(loadChannels, 4000);
    setTimeout(loadChannels, 10000);
  } catch (e) {
    status.className = "add-status err";
    status.textContent = `✗ ${e.message}`;
  } finally {
    document.getElementById("btn-add").disabled = false;
  }
});

document.getElementById("channel-url-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter") document.getElementById("btn-add").click();
});

/* ── remove channel ──────────────────────────────────────────────────── */
async function removeChannel(channelId, card) {
  if (!confirm("Remove this channel? Downloaded files on disk won't be deleted.")) return;
  card.style.opacity = "0.4";
  card.style.pointerEvents = "none";
  try {
    await api(`/api/channels/${channelId}`, { method: "DELETE" });
    channels = channels.filter(c => c.channel_id !== channelId);
    card.remove();
    if (channels.length === 0) document.getElementById("empty-state").classList.remove("hidden");
  } catch (e) {
    card.style.opacity = "";
    card.style.pointerEvents = "";
    alert(`Failed to remove: ${e.message}`);
  }
}

/* ── rebase channel ──────────────────────────────────────────────────── */
async function rebaseChannel(channelId, card) {
  setCardBusy(card, true, "Rebasing…");
  try {
    await api(`/api/channels/${channelId}/rebase`, { method: "POST" });
    await pollUntilDone(`rebase_${channelId}`, card, channelId);
  } catch (e) {
    handleApiError(e, card);
  }
}

/* ── sync channel ────────────────────────────────────────────────────── */
async function syncChannel(channelId, card) {
  setCardBusy(card, true, "Syncing…");
  try {
    await api(`/api/channels/${channelId}/sync`, { method: "POST" });
    await pollUntilDone(`sync_${channelId}`, card, channelId);
  } catch (e) {
    setCardBusy(card, false);
    alert(`Sync failed: ${e.message}`);
  }
}

/* ── sync all ────────────────────────────────────────────────────────── */
document.getElementById("btn-sync-all").addEventListener("click", async () => {
  const btn = document.getElementById("btn-sync-all");
  btn.disabled = true;
  btn.textContent = "Syncing…";
  try {
    await api("/api/sync-all", { method: "POST" });
    for (let i = 0; i < 6; i++) { await sleep(5000); await loadChannels(); }
  } catch (e) {
    alert(`Sync all failed: ${e.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = "Sync All";
    await loadChannels();
  }
});

/* ── back catalog modal ──────────────────────────────────────────────── */
function openCatalog(ch) {
  activeCatalogId = ch.channel_id;
  const pending = ch.pending_count ?? "?";
  document.getElementById("catalog-info").textContent =
    `${ch.channel_name} · ${pending} videos not yet downloaded`;
  document.getElementById("catalog-count-input").value = Math.min(20, pending);
  document.getElementById("catalog-status").textContent = "";
  document.getElementById("catalog-status").className = "add-status";
  document.getElementById("modal-catalog").classList.remove("hidden");
}

document.getElementById("close-catalog").addEventListener("click", () => {
  document.getElementById("modal-catalog").classList.add("hidden");
});

document.getElementById("btn-confirm-catalog").addEventListener("click", async () => {
  const count  = parseInt(document.getElementById("catalog-count-input").value, 10);
  const status = document.getElementById("catalog-status");
  if (!count || count < 1) return;
  status.className = "add-status";
  status.textContent = `Queuing ${count} downloads…`;
  document.getElementById("btn-confirm-catalog").disabled = true;
  try {
    await api(`/api/channels/${activeCatalogId}/catalog`, { method: "POST", body: { count } });
    status.className = "add-status ok";
    status.textContent = "✓ Downloads started in background.";
    setTimeout(() => {
      document.getElementById("modal-catalog").classList.add("hidden");
      loadChannels();
    }, 1500);
  } catch (e) {
    status.className = "add-status err";
    status.textContent = `✗ ${e.message}`;
  } finally {
    document.getElementById("btn-confirm-catalog").disabled = false;
  }
});

/* ── settings modal ──────────────────────────────────────────────────── */
document.getElementById("btn-settings").addEventListener("click", async () => {
  try {
    const s = await api("/api/settings");
    document.querySelectorAll('input[name="schedule_mode"]').forEach(r => {
      r.checked = r.value === s.schedule_mode;
    });
    document.getElementById("schedule-hours").value = s.schedule_hours || 6;
    document.getElementById("schedule-time").value  = s.schedule_time  || "03:00";
    document.getElementById("recent-count").value   = s.recent_count   || 5;
    document.getElementById("catalog-count").value  = s.catalog_count  || 5;
    document.getElementById("disk-threshold").value = s.disk_threshold_pct || 10;
    document.querySelectorAll('input[name="rebase_missing_action"]').forEach(r => {
      r.checked = r.value === (s.rebase_missing_action || "download");
    });
    document.getElementById("settings-status").textContent = "";

    // Load local version into update badge
    try {
      const upd = await api("/api/updates/status");
      document.getElementById("update-local-version").textContent =
        upd.local_version ? `v${upd.local_version}` : "unknown";
    } catch (_) {}

  } catch (e) { console.error(e); }
  document.getElementById("modal-settings").classList.remove("hidden");
});

document.getElementById("close-settings").addEventListener("click", () => {
  document.getElementById("modal-settings").classList.add("hidden");
});

document.getElementById("btn-save-settings").addEventListener("click", async () => {
  const mode = document.querySelector('input[name="schedule_mode"]:checked')?.value;
  const body = {
    schedule_mode:          mode,
    schedule_hours:         document.getElementById("schedule-hours").value,
    schedule_time:          document.getElementById("schedule-time").value,
    recent_count:           document.getElementById("recent-count").value,
    catalog_count:          document.getElementById("catalog-count").value,
    disk_threshold_pct:     document.getElementById("disk-threshold").value,
    rebase_missing_action:  document.querySelector('input[name="rebase_missing_action"]:checked')?.value || "download",
  };
  const status = document.getElementById("settings-status");
  try {
    await api("/api/settings", { method: "POST", body });
    status.className = "add-status ok";
    status.textContent = "✓ Saved";
    setTimeout(() => {
      status.textContent = "";
      document.getElementById("modal-settings").classList.add("hidden");
    }, 1200);
  } catch (e) {
    status.className = "add-status err";
    status.textContent = `✗ ${e.message}`;
  }
});

/* close modals on backdrop click */
document.querySelectorAll(".modal-backdrop").forEach(backdrop => {
  backdrop.addEventListener("click", (e) => {
    if (e.target === backdrop) backdrop.classList.add("hidden");
  });
});

/* ── polling helper ──────────────────────────────────────────────────── */
function sleep(ms) { return new Promise(res => setTimeout(res, ms)); }

async function pollUntilDone(taskId, card, channelId, maxWait = 300000) {
  const start = Date.now();
  while (Date.now() - start < maxWait) {
    await sleep(3000);
    try {
      const tasks = await api("/api/tasks/status");
      if (!tasks[taskId]) {
        const updated = await api("/api/channels");
        const ch = updated.find(c => c.channel_id === channelId);
        if (ch) updateCardStats(card, ch);
        setCardBusy(card, false);
        channels = updated;
        return;
      }
    } catch (_) { /* network blip */ }
  }
  setCardBusy(card, false);
}

/* ── update system ───────────────────────────────────────────────────── */
let _releases = [];

document.getElementById("btn-check-updates").addEventListener("click", async () => {
  const btn     = document.getElementById("btn-check-updates");
  const msg     = document.getElementById("update-status-msg");
  const area    = document.getElementById("update-release-area");
  const verBadge = document.getElementById("update-local-version");

  btn.disabled = true;
  btn.textContent = "Checking…";
  msg.className = "add-status";
  msg.textContent = "";
  area.classList.add("hidden");

  try {
    const data = await api("/api/updates/status");
    _releases = data.releases || [];

    verBadge.textContent = data.local_version ? `v${data.local_version}` : "unknown";

    // Populate release dropdown
    const select = document.getElementById("update-release-select");
    select.innerHTML = "";
    _releases.forEach(r => {
      const opt = document.createElement("option");
      opt.value       = r.tag;
      opt.textContent = `${r.tag}${r.is_latest ? " (latest)" : ""} — ${_fmtReleaseDate(r.published_at)}`;
      select.appendChild(opt);
    });

    if (_releases.length > 0) {
      area.style.display = "flex";
      area.classList.remove("hidden");
      _updateReleaseNotes();
    }

    msg.className = data.status === "up_to_date" ? "add-status ok" : "add-status";
    msg.textContent = data.message;

  } catch (e) {
    msg.className = "add-status err";
    msg.textContent = `✗ ${e.message}`;
  } finally {
    btn.disabled = false;
    btn.textContent = "Check for Updates";
  }
});

function _fmtReleaseDate(iso) {
  if (!iso) return "";
  return new Date(iso).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

function _updateReleaseNotes() {
  const select = document.getElementById("update-release-select");
  const notes  = document.getElementById("update-release-notes");
  const tag    = select.value;
  const release = _releases.find(r => r.tag === tag);
  notes.textContent = release?.notes || "No release notes available.";
}

document.getElementById("update-release-select")
  .addEventListener("change", _updateReleaseNotes);

document.getElementById("btn-apply-update").addEventListener("click", async () => {
  const tag = document.getElementById("update-release-select").value;
  const msg = document.getElementById("update-status-msg");
  const btn = document.getElementById("btn-apply-update");

  if (!tag) return;
  if (!confirm(`Apply ${tag}? The app will continue running on the old version until you restart the service.`)) return;

  btn.disabled = true;
  msg.className = "add-status";
  msg.textContent = `Downloading and applying ${tag}…`;

  try {
    await api("/api/updates/apply", { method: "POST", body: { tag } });

    // Poll for result
    let result = null;
    for (let i = 0; i < 60; i++) {
      await sleep(2000);
      const r = await api("/api/updates/result");
      if (!r.in_progress) { result = r.result; break; }
    }

    if (result?.ok) {
      msg.className = "add-status ok";
      msg.textContent = result.message;
    } else {
      msg.className = "add-status err";
      msg.textContent = `✗ ${result?.message || "Update failed."}`;
      btn.disabled = false;
    }
  } catch (e) {
    msg.className = "add-status err";
    msg.textContent = `✗ ${e.message}`;
    btn.disabled = false;
  }
});

/* ── disk warning banner ─────────────────────────────────────────────── */
let _diskWarningDismissed = false;

async function checkDiskSpace() {
  try {
    const status = await fetch("/api/disk/status").then(r => r.json());
    const banner  = document.getElementById("disk-warning-banner");
    const text    = document.getElementById("disk-warning-text");

    if (!status.ok) {
      text.textContent = `⚠ Warning: Low Disk Space — ${status.message}`;
      // Show again even if previously dismissed (spec: re-show on new warning event)
      if (!_diskWarningDismissed) {
        banner.classList.remove("hidden");
      } else {
        // New warning state — reset dismiss so it shows again
        _diskWarningDismissed = false;
        banner.classList.remove("hidden");
      }
    } else {
      // Space is fine — clear banner and reset dismiss flag
      banner.classList.add("hidden");
      _diskWarningDismissed = false;
    }
  } catch (_) { /* network blip — leave banner as-is */ }
}

document.getElementById("disk-warning-close").addEventListener("click", () => {
  document.getElementById("disk-warning-banner").classList.add("hidden");
  _diskWarningDismissed = true;
});

/* ── inline alert bar ────────────────────────────────────────────────── */
function showAlert(message) {
  const bar  = document.getElementById("alert-bar");
  const text = document.getElementById("alert-bar-text");
  text.textContent = message;
  bar.classList.remove("hidden");
}

document.getElementById("alert-bar-close").addEventListener("click", () => {
  document.getElementById("alert-bar").classList.add("hidden");
});

/* ── handle low_disk errors from download endpoints ──────────────────── */
function handleApiError(e, card) {
  if (e.message === "low_disk") {
    checkDiskSpace(); // trigger banner immediately
    showAlert("Download blocked: disk space is below your threshold. Free up space or adjust the limit in Settings.");
  } else {
    showAlert(`Error: ${e.message}`);
  }
  if (card) setCardBusy(card, false);
}

/* ── auto-refresh every 60s ──────────────────────────────────────────── */
setInterval(loadChannels, 60_000);
setInterval(checkDiskSpace, 60_000);

/* ── init ────────────────────────────────────────────────────────────── */
loadChannels();
checkDiskSpace();
