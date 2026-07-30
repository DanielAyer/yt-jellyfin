/*
 * setup.js — first-run configuration wizard
 *
 * Jellyfin URL resolution order:
 *   1. JELLYFIN_URL from server config (/api/setup/config-defaults)
 *   2. window.location.hostname:8096 (works for cohabitating installs)
 *   3. User prompted to set JELLYFIN_URL manually if connection fails
 */

let _mode     = "jellyfin";
let _libraries = [];

/* ── API helper ──────────────────────────────────────────────────────── */
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

/* ── init — load config defaults and pre-populate fields ─────────────── */
async function init() {
  try {
    const defaults = await api("/api/setup/config-defaults");

    // Jellyfin URL: config var → hostname fallback → blank
    const jellyfinUrl = defaults.jellyfin_url
      || `http://${window.location.hostname}:8096`;

    const urlInput = document.getElementById("jellyfin-url");
    urlInput.value = jellyfinUrl;
    _updateApiKeyLink(jellyfinUrl);

    if (defaults.jellyfin_api_key) {
      document.getElementById("jellyfin-api-key").value = defaults.jellyfin_api_key;
    }

    // Manual fields
    if (defaults.library_root) {
      document.getElementById("manual-library-root").value = defaults.library_root;
      _updateDbLabel(defaults.library_root);
    }
    if (defaults.db_path) {
      document.getElementById("manual-db-auto").checked = false;
      const dbInput = document.getElementById("manual-db-path");
      dbInput.value = defaults.db_path;
      dbInput.classList.remove("hidden");
    }
  } catch (_) {
    // Non-fatal — just leave fields blank
  }
}

/* ── Jellyfin URL → live API key link ────────────────────────────────── */
function _updateApiKeyLink(url) {
  const clean = url.trim().replace(/\/$/, "");
  const link  = document.getElementById("jellyfin-api-link");
  link.href   = `${clean}/web/index.html#/dashboard/keys`;
}

document.getElementById("jellyfin-url").addEventListener("input", (e) => {
  _updateApiKeyLink(e.target.value);
});

/* ── mode toggle ─────────────────────────────────────────────────────── */
document.querySelectorAll(".mode-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    _mode = btn.dataset.mode;
    document.querySelectorAll(".mode-btn").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById("path-jellyfin").classList.toggle("hidden", _mode !== "jellyfin");
    document.getElementById("path-manual").classList.toggle("hidden",   _mode !== "manual");
  });
});

/* ── connect to Jellyfin ─────────────────────────────────────────────── */
document.getElementById("btn-fetch-libraries").addEventListener("click", async () => {
  const btn    = document.getElementById("btn-fetch-libraries");
  const status = document.getElementById("jellyfin-connect-status");
  const url    = document.getElementById("jellyfin-url").value.trim();
  const key    = document.getElementById("jellyfin-api-key").value.trim();

  if (!url || !key) {
    status.className = "add-status err";
    status.textContent = "Please enter both a Jellyfin URL and API key.";
    return;
  }

  btn.disabled = true;
  btn.textContent = "Connecting…";
  status.className = "add-status";
  status.textContent = "";

  try {
    const data = await api("/api/setup/jellyfin-libraries", {
      method: "POST",
      body: { jellyfin_url: url, api_key: key },
    });

    _libraries = data.libraries || [];

    if (_libraries.length === 0) {
      status.className = "add-status err";
      status.textContent = "Connected, but no library folders found. Add a library in Jellyfin first.";
      return;
    }

    // Populate library dropdown
    const select = document.getElementById("jellyfin-library-select");
    select.innerHTML = "";
    _libraries.forEach(lib => {
      const opt = document.createElement("option");
      opt.value       = lib.path;
      opt.textContent = `${lib.name} — ${lib.path}`;
      select.appendChild(opt);
    });

    document.getElementById("step-library-select").classList.remove("hidden");
    _updateYoutubeFolderOptions();

    status.className = "add-status ok";
    status.textContent = `✓ Connected — ${_libraries.length} library folder(s) found.`;

  } catch (e) {
    status.className = "add-status err";
    // If connection failed, hint about JELLYFIN_URL config var
    const hint = e.message.includes("Could not reach")
      ? `\nIf Jellyfin is on a different machine, update the URL above or set JELLYFIN_URL in your .env file.`
      : "";
    status.textContent = `✗ ${e.message}${hint}`;
  } finally {
    btn.disabled = false;
    btn.textContent = "Connect to Jellyfin";
  }
});

/* ── YouTube folder options ──────────────────────────────────────────── */
document.getElementById("jellyfin-library-select")
  .addEventListener("change", _updateYoutubeFolderOptions);

function _updateYoutubeFolderOptions() {
  const select  = document.getElementById("jellyfin-library-select");
  const lib     = _libraries.find(l => l.path === select.value);
  if (!lib) return;

  const existingLabel = document.querySelector("#yt-existing-label code");
  const existingRadio = document.getElementById("yt-use-existing");
  const createRadio   = document.getElementById("yt-create-new");

  if (lib.youtube_exists) {
    // Existing youtube folder found — default to using it
    existingLabel.textContent = lib.youtube_path;
    existingRadio.disabled    = false;
    existingRadio.checked     = true;
  } else {
    // No youtube folder — default to creating one
    existingLabel.textContent = "none found";
    existingRadio.disabled    = true;
    createRadio.checked       = true;
  }
}

function _resolveLibraryRoot() {
  const select = document.getElementById("jellyfin-library-select");
  const lib    = _libraries.find(l => l.path === select.value);
  if (!lib) return "";

  const choice = document.querySelector('input[name="yt_folder"]:checked')?.value;

  if (choice === "existing") return lib.youtube_path;
  if (choice === "create")   return lib.path.replace(/\/$/, "") + "/youtube";
  if (choice === "custom")   return lib.path;
  return lib.path;
}

/* ── manual DB path auto-fill ────────────────────────────────────────── */
function _updateDbLabel(root) {
  const clean = root.trim().replace(/\/$/, "");
  document.getElementById("manual-db-default-label").textContent =
    clean ? `${clean}/.ytjf.db` : "LIBRARY_ROOT/.ytjf.db";
}

document.getElementById("manual-library-root").addEventListener("input", (e) => {
  _updateDbLabel(e.target.value);
  if (document.getElementById("manual-db-auto").checked) {
    const clean = e.target.value.trim().replace(/\/$/, "");
    document.getElementById("manual-db-path").value = clean ? `${clean}/.ytjf.db` : "";
  }
});

document.getElementById("manual-db-auto").addEventListener("change", (e) => {
  const dbInput = document.getElementById("manual-db-path");
  if (e.target.checked) {
    const root = document.getElementById("manual-library-root").value.trim().replace(/\/$/, "");
    dbInput.value = root ? `${root}/.ytjf.db` : "";
    dbInput.classList.add("hidden");
  } else {
    dbInput.classList.remove("hidden");
  }
});

/* ── save configuration ──────────────────────────────────────────────── */
document.getElementById("btn-save-setup").addEventListener("click", async () => {
  const btn    = document.getElementById("btn-save-setup");
  const result = document.getElementById("setup-result");

  let library_root     = "";
  let db_path          = "";
  let jellyfin_url     = "";
  let jellyfin_api_key = "";

  if (_mode === "jellyfin") {
    library_root     = _resolveLibraryRoot();
    jellyfin_url     = document.getElementById("jellyfin-url").value.trim();
    jellyfin_api_key = document.getElementById("jellyfin-api-key").value.trim();

    if (!library_root) {
      result.className = "setup-result err";
      result.textContent = "Please connect to Jellyfin and select a library folder.";
      result.classList.remove("hidden");
      return;
    }
  } else {
    library_root = document.getElementById("manual-library-root").value.trim();
    const autoDb = document.getElementById("manual-db-auto").checked;
    db_path = autoDb
      ? `${library_root.replace(/\/$/, "")}/.ytjf.db`
      : document.getElementById("manual-db-path").value.trim();

    if (!library_root) {
      result.className = "setup-result err";
      result.textContent = "Please enter a library root folder.";
      result.classList.remove("hidden");
      return;
    }
  }

  // Auto-set DB path for Jellyfin mode
  if (!db_path && library_root) {
    db_path = `${library_root.replace(/\/$/, "")}/.ytjf.db`;
  }

  btn.disabled = true;
  btn.textContent = "Saving…";
  result.classList.add("hidden");

  try {
    const data = await api("/api/setup/save", {
      method: "POST",
      body: { library_root, db_path, jellyfin_url, jellyfin_api_key },
    });

    result.className = "setup-result ok";
    result.textContent = "✓ Configuration saved successfully.";
    result.classList.remove("hidden");
    document.getElementById("restart-block").classList.remove("hidden");
    document.getElementById("restart-block").style.display = "flex";

  } catch (e) {
    result.className = "setup-result err";
    result.textContent = `✗ ${e.message}`;
    result.classList.remove("hidden");
    btn.disabled = false;
    btn.textContent = "Save Configuration";
  }
});

/* ── copy restart command ─────────────────────────────────────────────── */
document.getElementById("btn-copy-restart")?.addEventListener("click", () => {
  const cmd = document.getElementById("restart-cmd").textContent;
  navigator.clipboard.writeText(cmd).then(() => {
    const btn = document.getElementById("btn-copy-restart");
    btn.textContent = "Copied!";
    setTimeout(() => { btn.textContent = "Copy"; }, 2000);
  }).catch(() => {
    // Fallback for browsers without clipboard API
    const el = document.createElement("textarea");
    el.value = cmd;
    document.body.appendChild(el);
    el.select();
    document.execCommand("copy");
    document.body.removeChild(el);
  });
});

/* ── init ────────────────────────────────────────────────────────────── */
init();
