/**
 * log_strip.js — shared contextual log strip for all pages.
 * Include after the page-specific JS. Expects a #log-strip element in the page.
 */
(function() {
  const strip   = document.getElementById("log-strip");
  if (!strip) return;

  const header  = strip.querySelector(".log-strip-header");
  const body    = strip.querySelector(".log-strip-body");
  const badge   = strip.querySelector(".log-strip-badge");
  const toggle  = strip.querySelector(".log-strip-toggle");

  let _collapsed = true;
  strip.classList.add("collapsed");

  header.addEventListener("click", () => {
    _collapsed = !_collapsed;
    strip.classList.toggle("collapsed", _collapsed);
    strip.classList.toggle("expanded", !_collapsed);
    toggle.textContent = _collapsed ? "▼" : "▲";
  });

  async function loadLogs() {
    const channelId = strip.dataset.channelId || "";
    const url = channelId
      ? `/api/logs/context?channel_id=${channelId}&limit=50`
      : `/api/logs/context?limit=50`;

    try {
      const data = await fetch(url).then(r => r.json());
      const entries = data.entries || [];
      body.innerHTML = "";
      let errorCount = 0;

      entries.slice().reverse().forEach(e => {
        const el = document.createElement("div");
        el.className = "log-entry";
        el.style.color = e.color || "#a0aab8";
        el.textContent = e.message;
        el.title = e.message;
        body.appendChild(el);
        if (e.level === "ERROR") errorCount++;
      });

      // Auto-scroll to bottom
      body.scrollTop = body.scrollHeight;

      // Update error badge
      if (errorCount > 0) {
        badge.textContent = errorCount + " error" + (errorCount > 1 ? "s" : "");
        badge.classList.remove("hidden");
      } else {
        badge.classList.add("hidden");
      }
    } catch (_) {}
  }

  // Load on init, refresh every 30s
  loadLogs();
  setInterval(loadLogs, 30_000);
})();
