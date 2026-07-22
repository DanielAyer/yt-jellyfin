/* setup.js — first-run configuration wizard */

let _mode = "jellyfin"; // "jellyfin" | "manual"
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

/* ── Jellyfin URL → live API key link ────────────────────────────────── */
document.getElementById("jellyfin-url").addEventListener("input", (e) => {
  const url  = e.target.value.trim().replace(/\/$/, "");
  const link = document.getElementById("jellyfin-api-link");
  link.href        = `${url}/web/index.html#!/keys.html`;
  link.textContent = "Dashboard → API Keys";
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
      opt.textContent = `${lib.name} — ${lib.path} (${lib.type})`;
      select.appendChild(opt);
    });

    document.getElementById("step-library-select").style.display = "flex";
    status.className = "add-status ok";
    status.textContent = `✓ Connected — ${_libraries.length} library folder(s) found.`;

  } catch (e) {
    status.className = "add-status err";
    status.textContent = `✗ ${e.message}`;
  } finally {
    btn.disabled = false;
    btn.textContent = "Connect to Jellyfin";
  }
});

/* ── manual DB path auto-fill ────────────────────────────────────────── */
document.getElementById("manual-library-root").addEventListener("input", (e) => {
  const root  = e.target.value.trim().replace(/\/$/, "");
  const label = document.getElementById("manual-db-default-label");
  label.textContent = root ? `${root}/.ytjf.db` : "LIBRARY_ROOT/.ytjf.db";
  if (document.getElementById("manual-db-auto").checked) {
    document.getElementById("manual-db-path").value = root ? `${root}/.ytjf.db` : "";
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

  let library_root = "";
  let db_path      = "";
  let jellyfin_url = "";
  let jellyfin_api_key = "";

  if (_mode === "jellyfin") {
    const select = document.getElementById("jellyfin-library-select");
    library_root     = select.value;
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
      body: {
        library_root,
        db_path,
        host:             document.getElementById("adv-host").value.trim() || "0.0.0.0",
        port:             document.getElementById("adv-port").value.trim() || "5000",
        jellyfin_url,
        jellyfin_api_key,
      },
    });

    result.className = "setup-result ok";
    result.textContent = data.message;
    result.classList.remove("hidden");

  } catch (e) {
    result.className = "setup-result err";
    result.textContent = `✗ ${e.message}`;
    result.classList.remove("hidden");
    btn.disabled = false;
    btn.textContent = "Save Configuration";
  }
});
