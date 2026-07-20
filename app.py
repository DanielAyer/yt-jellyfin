import logging
import threading

# Validate required config before importing anything that depends on it
from config import HOST, PORT, validate_config
validate_config()

from flask import Flask, jsonify, request, render_template
from database import init_db, get_db
from downloader import (
    resolve_channel_id_and_name,
    fetch_channel_metadata,
    sync_channel,
    download_back_catalog,
    sync_all_channels,
    download_videos_by_id,
    delete_video_file,
    delete_channel_folder,
    refresh_last_viewed,
)
from disk_space import check_library_space, check_space
from scheduler import start_scheduler, apply_schedule

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)

app = Flask(__name__)
init_db()

# ── background task runner ─────────────────────────────────────────────────────

_running_tasks: dict[str, bool] = {}
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
    t = threading.Thread(target=wrapper, daemon=True)
    t.start()


def _is_busy(task_id: str) -> bool:
    with _task_lock:
        return _running_tasks.get(task_id, False)


# ── pages ──────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/channel/<channel_id>/videos")
def videos_page(channel_id):
    with get_db() as conn:
        ch = conn.execute(
            "SELECT * FROM channels WHERE channel_id = ?", (channel_id,)
        ).fetchone()
    if not ch:
        return "Channel not found", 404
    return render_template("videos.html", channel=dict(ch))


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
                       WHERE v.channel_id = c.channel_id AND v.status = 'pending') as pending_count
               FROM channels c ORDER BY c.channel_name"""
        ).fetchall()
    channels = [dict(r) for r in rows]

    # backfill missing thumbnails in the background
    missing = [c for c in channels if not c.get("thumbnail_url")]
    if missing:
        def _backfill():
            for ch in missing:
                try:
                    _, _, thumb = resolve_channel_id_and_name(ch["channel_url"])
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
        channel_id, channel_name, thumbnail_url = resolve_channel_id_and_name(url)
    except Exception as e:
        return jsonify({"error": str(e)}), 400

    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM channels WHERE channel_id = ?", (channel_id,)
        ).fetchone()
        if existing:
            return jsonify({"error": "channel already added"}), 409
        conn.execute(
            """INSERT INTO channels (channel_id, channel_name, channel_url, thumbnail_url)
               VALUES (?, ?, ?, ?)""",
            (channel_id, channel_name, url, thumbnail_url),
        )

    def _initial_sync():
        videos = fetch_channel_metadata(url)
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
        "channel_id":    channel_id,
        "channel_name":  channel_name,
        "thumbnail_url": thumbnail_url,
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

    _bg(task_id, download_videos_by_id, video_ids, channel_id)
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
        "schedule_mode", "schedule_hours", "schedule_time",
        "recent_count", "catalog_count", "disk_threshold_pct",
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
    # Refresh last_viewed from filesystem on boot
    _bg("boot_last_viewed", refresh_last_viewed)
    start_scheduler()
    app.run(host=HOST, port=PORT, debug=False)
