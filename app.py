import logging
import threading

from config import HOST, PORT
from setup import is_setup_complete

from flask import Flask, jsonify, request, render_template, redirect, url_for

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)

app = Flask(__name__)

# ── setup mode ─────────────────────────────────────────────────────────────────
# If LIBRARY_ROOT / DB_PATH are not configured, the app runs in setup mode.
# Only the /setup routes are active; all other routes redirect to /setup.

def _is_setup_complete() -> bool:
    """Re-check setup state on each call so changes take effect without restart."""
    from setup import is_setup_complete
    return is_setup_complete()

# Always import everything — modules are only *used* when setup is complete.
# This avoids conditional import complexity while keeping setup mode working.
try:
    from database import init_db, get_db, get_channel_settings, save_channel_settings
    from downloader import (
        resolve_channel_id_and_name,
        fetch_channel_metadata,
        sync_channel,
        download_back_catalog,
        sync_all_channels,
        download_videos_by_id,
        download_next_n,
        download_latest_m,
        download_all_pending,
        estimate_download_size,
        force_stop_channel,
        delete_video_file,
        delete_channel_folder,
        refresh_last_viewed,
        rebase_channel,
    )
    from disk_space import check_library_space, check_space
    from scheduler import start_scheduler, apply_schedule
    from updater import check_for_updates, apply_update
    if _is_setup_complete():
        init_db()
    else:
        log.warning("App starting in SETUP MODE — LIBRARY_ROOT or DB_PATH not configured.")
        log.warning("Open http://<server-ip>:%d/setup to configure.", PORT)
except Exception as e:
    log.warning("Some modules could not be loaded (setup mode): %s", e)

# ── background task runner ─────────────────────────────────────────────────────

_running_tasks: dict[str, bool] = {}
_task_progress: dict[str, dict] = {}  # channel_id → {current, total, label}
_task_lock = threading.Lock()


def _bg(task_id: str, fn, *args, **kwargs):
    def wrapper():
        with _task_lock:
            _running_tasks[task_id] = True
        try:
            fn(*args, **kwargs)
        finally:
            with _task_lock:
                _running_tasks[task_id] = False
            # Clear progress when task completes
            channel_id = task_id.split("_", 1)[-1] if "_" in task_id else task_id
            with _task_lock:
                _task_progress.pop(channel_id, None)
    t = threading.Thread(target=wrapper, daemon=True)
    t.start()


def _is_busy(task_id: str) -> bool:
    with _task_lock:
        return _running_tasks.get(task_id, False)


def update_progress(channel_id: str, current: int, total: int, label: str = "Downloading"):
    """Called by downloader functions to report per-video progress."""
    with _task_lock:
        _task_progress[channel_id] = {
            "active":  True,
            "current": current,
            "total":   total,
            "label":   label,
            "pct":     round((current / total) * 100) if total else 0,
        }


# ── setup routes ───────────────────────────────────────────────────────────────

@app.route("/setup")
def setup_page():
    if _is_setup_complete():
        return redirect(url_for("index"))
    return render_template("setup.html")


@app.route("/api/setup/config-defaults")
def setup_config_defaults():
    """
    Return any pre-configured values from the environment that the
    setup wizard can use as defaults (e.g. JELLYFIN_URL from .env).
    """
    from setup import get_config_defaults
    return jsonify(get_config_defaults())


@app.route("/api/setup/jellyfin-libraries", methods=["POST"])
def setup_jellyfin_libraries():
    """Query Jellyfin API for library folders using provided URL and API key."""
    from setup import query_jellyfin_libraries
    data        = request.json or {}
    jellyfin_url = (data.get("jellyfin_url") or "").strip()
    api_key      = (data.get("api_key") or "").strip()

    if not jellyfin_url or not api_key:
        return jsonify({"error": "jellyfin_url and api_key are required"}), 400

    try:
        libraries = query_jellyfin_libraries(jellyfin_url, api_key)
        return jsonify({"ok": True, "libraries": libraries})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        log.exception("Unexpected error querying Jellyfin: %s", e)
        return jsonify({"ok": False, "error": "Unexpected error — check server logs."}), 500


@app.route("/api/setup/save", methods=["POST"])
def setup_save():
    """Write configuration to .env and signal the user to restart."""
    from setup import write_env
    data = request.json or {}

    result = write_env(
        library_root     = data.get("library_root", ""),
        db_path          = data.get("db_path", ""),
        jellyfin_url     = data.get("jellyfin_url", ""),
        jellyfin_api_key = data.get("jellyfin_api_key", ""),
    )
    status_code = 200 if result["ok"] else 400
    return jsonify(result), status_code


# ── pages ───────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    if not _is_setup_complete():
        return redirect(url_for("setup_page"))
    return render_template("index.html")


@app.route("/channel/<channel_id>/videos")
def videos_page(channel_id):
    if not _is_setup_complete():
        return redirect(url_for("setup_page"))
    with get_db() as conn:
        ch = conn.execute(
            "SELECT * FROM channels WHERE channel_id = ?", (channel_id,)
        ).fetchone()
    if not ch:
        return "Channel not found", 404
    return render_template("videos.html", channel=dict(ch))


@app.route("/logs")
def logs_page():
    if not _is_setup_complete():
        return redirect(url_for("setup_page"))
    return render_template("logs.html")


# ── log API ────────────────────────────────────────────────────────────────────

@app.route("/api/logs")
def get_logs():
    """
    Return the last N lines from the systemd journal for this service.
    Requires the service user to be in the systemd-journal group.
    """
    lines = int(request.args.get("lines", 200))
    lines = min(lines, 1000)
    try:
        import subprocess
        r = subprocess.run(
            ["journalctl", "-u", "yt-jellyfin", f"-n{lines}", "--no-pager", "--output=short"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode != 0:
            return jsonify({
                "lines": [],
                "error": (
                    "Could not read journal. Make sure the service user is in the "
                    "systemd-journal group: sudo usermod -aG systemd-journal <user>"
                ),
            }), 500
        log_lines = [l for l in r.stdout.splitlines() if l.strip()]
        return jsonify({"lines": log_lines, "count": len(log_lines)})
    except FileNotFoundError:
        return jsonify({"lines": [], "error": "journalctl not found — is this a systemd system?"}), 500
    except Exception as e:
        log.exception("Error reading journal: %s", e)
        return jsonify({"lines": [], "error": str(e)}), 500


@app.route("/api/logs/stream")
def stream_logs():
    """
    SSE endpoint that tails the systemd journal live.
    Connect with: EventSource('/api/logs/stream')
    Each new journal line is pushed as a plain 'data:' SSE event.
    """
    import subprocess

    def generate():
        try:
            proc = subprocess.Popen(
                ["journalctl", "-u", "yt-jellyfin", "-f", "--no-pager", "--output=short"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            )
            for line in proc.stdout:
                line = line.rstrip()
                if line:
                    yield f"data: {line}\n\n"
        except Exception as e:
            yield f"data: [stream error: {e}]\n\n"

    from flask import Response
    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── update API ─────────────────────────────────────────────────────────────────

@app.route("/api/updates/status")
def update_status():
    """
    Check for available updates by querying GitHub releases.
    Returns local version, release list, and update status.
    Network call — may be slow. Frontend should call this on demand only.
    """
    try:
        result = check_for_updates()
        return jsonify(result)
    except Exception as e:
        log.exception("Error checking for updates: %s", e)
        return jsonify({
            "status":  "offline",
            "message": f"Update check failed: {e}",
        }), 500


@app.route("/api/updates/apply", methods=["POST"])
def apply_update_route():
    """
    Apply a specific release by tag.
    Body: { "tag": "v0.2.0" }
    Blocks if any background tasks are running.
    """
    data = request.json or {}
    tag  = (data.get("tag") or "").strip()
    if not tag:
        return jsonify({"error": "tag required"}), 400

    # Block update if downloads or syncs are in progress
    with _task_lock:
        active = [k for k, v in _running_tasks.items() if v]
    if active:
        return jsonify({
            "error":   "tasks_running",
            "message": f"Cannot update while tasks are running: {', '.join(active)}. "
                       f"Please wait for them to finish.",
        }), 409

    task_id = "apply_update"
    if _is_busy(task_id):
        return jsonify({"error": "already running"}), 409

    # Run in background so the HTTP response can return immediately
    # Result is stored and retrievable via /api/updates/result
    _update_result.clear()
    _bg(task_id, _run_update, tag)
    return jsonify({"ok": True, "message": f"Applying {tag}…"})


_update_result: dict = {}


def _run_update(tag: str):
    """Background wrapper that stores the update result."""
    result = apply_update(tag)
    _update_result.update(result)
    log.info("Update result: %s", result)


@app.route("/api/updates/result")
def update_result():
    """Poll this after starting an update to get the final result."""
    busy = _is_busy("apply_update")
    return jsonify({
        "in_progress": busy,
        "result":      _update_result if not busy else None,
    })


# ── disk space API ─────────────────────────────────────────────────────────────

@app.route("/api/disk/status")
def disk_status():
    result = check_library_space()
    return jsonify(result)


# ── channels API ───────────────────────────────────────────────────────────────

@app.route("/api/channels", methods=["GET"])
def list_channels():
    with get_db() as conn:
        rows = conn.execute(
            """SELECT c.*,
                      (SELECT COUNT(*) FROM videos v
                       WHERE v.channel_id = c.channel_id AND v.status = 'downloaded'
                         AND (v.archived IS NULL OR v.archived = 0)) as downloaded_count,
                      (SELECT COUNT(*) FROM videos v
                       WHERE v.channel_id = c.channel_id AND v.status = 'pending') as pending_count,
                      (SELECT COUNT(*) FROM videos v
                       WHERE v.channel_id = c.channel_id AND v.status = 'failed') as failed_count
               FROM channels c ORDER BY c.channel_name"""
        ).fetchall()
    channels = [dict(r) for r in rows]

    # backfill missing thumbnails in the background
    missing = [c for c in channels if not c.get("thumbnail_url")]
    if missing:
        def _backfill():
            for ch in missing:
                try:
                    _, _, thumb, _ = resolve_channel_id_and_name(ch["channel_url"])
                    if thumb:
                        with get_db() as conn:
                            conn.execute(
                                "UPDATE channels SET thumbnail_url = ? WHERE channel_id = ?",
                                (thumb, ch["channel_id"]),
                            )
                        log.info("Backfilled thumbnail for %s", ch["channel_name"])
                except Exception as e:
                    log.warning("Could not fetch thumbnail for %s: %s", ch["channel_name"], e)
        _bg("backfill_thumbnails", _backfill)

    return jsonify(channels)


@app.route("/api/channels", methods=["POST"])
def add_channel():
    data = request.json or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "url required"}), 400

    try:
        channel_id, channel_name, thumbnail_url, resolved_url = resolve_channel_id_and_name(url)
    except Exception as e:
        return jsonify({"error": str(e)}), 400

    # Use the resolved channel URL (may differ from input if a video URL was passed)
    channel_url = resolved_url
    video_url_detected = resolved_url != url

    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM channels WHERE channel_id = ?", (channel_id,)
        ).fetchone()
        if existing:
            return jsonify({"error": "channel already added"}), 409
        conn.execute(
            """INSERT INTO channels (channel_id, channel_name, channel_url, thumbnail_url)
               VALUES (?, ?, ?, ?)""",
            (channel_id, channel_name, channel_url, thumbnail_url),
        )

    def _initial_sync():
        videos = fetch_channel_metadata(channel_url)
        if videos:
            with get_db() as conn:
                for v in videos:
                    conn.execute(
                        """INSERT OR IGNORE INTO videos
                           (video_id, channel_id, title, upload_date, duration, status)
                           VALUES (?, ?, ?, ?, ?, 'pending')""",
                        (v["video_id"], channel_id, v["title"], v["upload_date"], v["duration"]),
                    )
                    if v.get("thumbnail_url"):
                        conn.execute(
                            """INSERT OR REPLACE INTO video_thumbnails (video_id, thumbnail_url)
                               VALUES (?, ?)""",
                            (v["video_id"], v["thumbnail_url"]),
                        )
                conn.execute(
                    "UPDATE channels SET total_available = ? WHERE channel_id = ?",
                    (len(videos), channel_id),
                )

    _bg(f"init_{channel_id}", _initial_sync)
    return jsonify({
        "channel_id":          channel_id,
        "channel_name":        channel_name,
        "thumbnail_url":       thumbnail_url,
        "video_url_detected":  video_url_detected,
        "message":             (
            f"Video URL detected — adding channel '{channel_name}' instead."
            if video_url_detected else None
        ),
    }), 201


@app.route("/api/channels/<channel_id>", methods=["DELETE"])
def remove_channel(channel_id):
    """
    Remove a channel from the app and delete all its files from disk.
    This is intentional — the app is self-contained. Removing a channel
    removes everything associated with it.
    """
    with get_db() as conn:
        ch = conn.execute(
            "SELECT * FROM channels WHERE channel_id = ?", (channel_id,)
        ).fetchone()
    if not ch:
        return jsonify({"error": "channel not found"}), 404

    # Delete files from disk first
    delete_channel_folder(ch["channel_name"])

    # Then remove DB records
    with get_db() as conn:
        conn.execute(
            "DELETE FROM video_thumbnails WHERE video_id IN "
            "(SELECT video_id FROM videos WHERE channel_id = ?)", (channel_id,)
        )
        conn.execute("DELETE FROM videos WHERE channel_id = ?", (channel_id,))
        conn.execute("DELETE FROM channels WHERE channel_id = ?", (channel_id,))

    return jsonify({"ok": True})


# ── video delete API ───────────────────────────────────────────────────────────

@app.route("/api/videos/<video_id>", methods=["DELETE"])
def remove_video(video_id):
    """
    Delete a single video file from disk and remove its DB record.
    """
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM videos WHERE video_id = ?", (video_id,)
        ).fetchone()
    if not row:
        return jsonify({"error": "video not found"}), 404

    # Delete file from disk
    if row["file_path"]:
        delete_video_file(row["file_path"])

    # Remove from DB
    with get_db() as conn:
        conn.execute("DELETE FROM video_thumbnails WHERE video_id = ?", (video_id,))
        conn.execute("DELETE FROM videos WHERE video_id = ?", (video_id,))
        conn.execute(
            "UPDATE channels SET total_downloaded = MAX(0, total_downloaded - 1) "
            "WHERE channel_id = ?", (row["channel_id"],)
        )

    return jsonify({"ok": True})


@app.route("/api/channels/<channel_id>/rebase", methods=["POST"])
def rebase(channel_id):
    """
    Reconcile the DB with the filesystem for a channel.
    Phase 1: disk → DB (create/update records to match files on disk).
    Phase 2: DB → disk (handle DB records with no matching file per
             the user's 'rebase_missing_action' setting).
    """
    task_id = f"rebase_{channel_id}"
    if _is_busy(task_id):
        return jsonify({"error": "already running"}), 409

    with get_db() as conn:
        s = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings").fetchall()}
    missing_action = s.get("rebase_missing_action", "download")

    _bg(task_id, rebase_channel, channel_id, missing_action)
    return jsonify({"ok": True, "message": "rebase started"})


@app.route("/api/channels/<channel_id>/stop", methods=["POST"])
def stop_download(channel_id):
    """Force stop the active download for a channel and clean up temp files."""
    try:
        result = force_stop_channel(channel_id)
        return jsonify(result)
    except Exception as e:
        log.exception("Error stopping download for %s: %s", channel_id, e)
        return jsonify({"ok": False, "message": str(e)}), 500


@app.route("/api/channels/<channel_id>/settings", methods=["GET"])
def get_channel_settings_route(channel_id):
    """Get effective settings for a channel (per-channel overrides + global fallbacks)."""
    try:
        settings = get_channel_settings(channel_id)
        # Also return raw per-channel values so UI knows what's overridden
        with get_db() as conn:
            row = conn.execute(
                "SELECT * FROM channel_settings WHERE channel_id = ?", (channel_id,)
            ).fetchone()
        settings["has_override"] = row is not None
        settings["n_catalog_override"] = row["n_catalog"] if row else None
        settings["m_recent_override"]  = row["m_recent"]  if row else None
        return jsonify(settings)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/channels/<channel_id>/settings", methods=["POST"])
def save_channel_settings_route(channel_id):
    """Save per-channel settings. Pass null to reset to global default."""
    data = request.json or {}
    try:
        n = int(data["n_catalog"]) if data.get("n_catalog") is not None else None
        m = int(data["m_recent"])  if data.get("m_recent")  is not None else None
        save_channel_settings(channel_id, n, m)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/channels/<channel_id>/estimate", methods=["POST"])
def size_estimate(channel_id):
    """Estimate download size for a list of video IDs."""
    data = request.json or {}
    video_ids = data.get("video_ids", [])
    if not video_ids:
        return jsonify({"error": "no video_ids provided"}), 400
    try:
        result = estimate_download_size(video_ids, channel_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/channels/<channel_id>/download/next-n", methods=["POST"])
def download_next_n_route(channel_id):
    """Download the next N oldest undownloaded videos."""
    space = check_library_space()
    if not space["ok"]:
        return jsonify({"error": "low_disk", "message": space["message"]}), 507

    task_id = f"download_{channel_id}"
    if _is_busy(task_id):
        return jsonify({"error": "already running"}), 409

    settings = get_channel_settings(channel_id)
    n = int(request.json.get("n", settings["n_catalog"])) if request.json else settings["n_catalog"]
    _bg(task_id, download_next_n, channel_id, n, update_progress)
    return jsonify({"ok": True, "message": f"Downloading next {n} videos"})


@app.route("/api/channels/<channel_id>/download/latest-m", methods=["POST"])
def download_latest_m_route(channel_id):
    """Download the M most recent undownloaded videos."""
    space = check_library_space()
    if not space["ok"]:
        return jsonify({"error": "low_disk", "message": space["message"]}), 507

    task_id = f"download_{channel_id}"
    if _is_busy(task_id):
        return jsonify({"error": "already running"}), 409

    settings = get_channel_settings(channel_id)
    m = int(request.json.get("m", settings["m_recent"])) if request.json else settings["m_recent"]
    _bg(task_id, download_latest_m, channel_id, m, update_progress)
    return jsonify({"ok": True, "message": f"Downloading latest {m} videos"})


@app.route("/api/channels/<channel_id>/download/all", methods=["POST"])
def download_all_route(channel_id):
    """Download all undownloaded videos for a channel."""
    space = check_library_space()
    if not space["ok"]:
        return jsonify({"error": "low_disk", "message": space["message"]}), 507

    task_id = f"download_{channel_id}"
    if _is_busy(task_id):
        return jsonify({"error": "already running"}), 409

    _bg(task_id, download_all_pending, channel_id, update_progress)
    return jsonify({"ok": True, "message": "Downloading all pending videos"})


# ── sync / download API ────────────────────────────────────────────────────────

@app.route("/api/channels/<channel_id>/sync", methods=["POST"])
def manual_sync(channel_id):
    space = check_library_space()
    if not space["ok"]:
        return jsonify({"error": "low_disk", "message": space["message"]}), 507

    task_id = f"sync_{channel_id}"
    if _is_busy(task_id):
        return jsonify({"error": "already running"}), 409

    with get_db() as conn:
        s = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings").fetchall()}
    rc = int(s.get("recent_count", 5))
    cc = int(s.get("catalog_count", 5))

    _bg(task_id, sync_channel, channel_id, rc, cc)
    return jsonify({"ok": True, "message": "sync started"})


@app.route("/api/channels/<channel_id>/catalog", methods=["POST"])
def back_catalog(channel_id):
    space = check_library_space()
    if not space["ok"]:
        return jsonify({"error": "low_disk", "message": space["message"]}), 507

    data = request.json or {}
    try:
        count = max(1, int(data.get("count", 10)))
    except (TypeError, ValueError):
        return jsonify({"error": "invalid count"}), 400

    task_id = f"catalog_{channel_id}"
    if _is_busy(task_id):
        return jsonify({"error": "already running"}), 409

    _bg(task_id, download_back_catalog, channel_id, count)
    return jsonify({"ok": True, "message": f"downloading {count} back-catalog videos"})


@app.route("/api/channels/<channel_id>/download", methods=["POST"])
def download_selected(channel_id):
    space = check_library_space()
    if not space["ok"]:
        return jsonify({"error": "low_disk", "message": space["message"]}), 507

    data = request.json or {}
    video_ids = data.get("video_ids", [])
    if not video_ids:
        return jsonify({"error": "no video_ids provided"}), 400

    task_id = f"selected_{channel_id}"
    if _is_busy(task_id):
        return jsonify({"error": "already running"}), 409

    _bg(task_id, download_videos_by_id, video_ids, channel_id, update_progress)
    return jsonify({"ok": True, "message": f"downloading {len(video_ids)} videos"})


@app.route("/api/sync-all", methods=["POST"])
def sync_all():
    space = check_library_space()
    if not space["ok"]:
        return jsonify({"error": "low_disk", "message": space["message"]}), 507

    task_id = "sync_all_manual"
    if _is_busy(task_id):
        return jsonify({"error": "already running"}), 409

    with get_db() as conn:
        s = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings").fetchall()}
    rc = int(s.get("recent_count", 5))
    cc = int(s.get("catalog_count", 5))

    _bg(task_id, sync_all_channels, rc, cc)
    return jsonify({"ok": True, "message": "full sync started"})


@app.route("/api/channels/<channel_id>/progress")
def channel_progress(channel_id):
    """Return current download progress for a channel."""
    with _task_lock:
        progress = _task_progress.get(channel_id)
    if progress:
        return jsonify(progress)
    return jsonify({"active": False, "current": 0, "total": 0, "pct": 0, "label": ""})


@app.route("/api/tasks/status")
def task_status():
    with _task_lock:
        return jsonify(dict(_running_tasks))


# ── settings API ───────────────────────────────────────────────────────────────

@app.route("/api/settings", methods=["GET"])
def get_settings():
    with get_db() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    return jsonify({r["key"]: r["value"] for r in rows})


@app.route("/api/settings", methods=["POST"])
def update_settings():
    data = request.json or {}
    allowed = {
        "n_catalog", "m_recent",
        "disk_threshold_pct",
        "rebase_missing_action",
        # TODO: scheduled sync — add back as advanced option in future
    }
    with get_db() as conn:
        for key, value in data.items():
            if key in allowed:
                if key == "disk_threshold_pct":
                    try:
                        value = str(max(5.0, float(value)))
                    except (TypeError, ValueError):
                        value = "10"
                conn.execute(
                    "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                    (key, str(value)),
                )
    apply_schedule()
    return jsonify({"ok": True})


# ── videos API ─────────────────────────────────────────────────────────────────

@app.route("/api/channels/<channel_id>/videos")
def list_videos(channel_id):
    status_filter = request.args.get("status")
    limit  = min(int(request.args.get("limit", 500)), 1000)
    offset = int(request.args.get("offset", 0))

    query = """
        SELECT v.*, COALESCE(vt.thumbnail_url, '') as thumbnail_url
        FROM videos v
        LEFT JOIN video_thumbnails vt ON v.video_id = vt.video_id
        WHERE v.channel_id = ?
    """
    params: list = [channel_id]
    if status_filter:
        query += " AND v.status = ?"
        params.append(status_filter)
    query += " ORDER BY v.upload_date DESC LIMIT ? OFFSET ?"
    params += [limit, offset]

    with get_db() as conn:
        rows = conn.execute(query, params).fetchall()
    return jsonify([dict(r) for r in rows])


if __name__ == "__main__":
    if _is_setup_complete():
        _bg("boot_last_viewed", refresh_last_viewed)
        start_scheduler()
    app.run(host=HOST, port=PORT, debug=False)
