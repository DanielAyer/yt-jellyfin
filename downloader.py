import subprocess
import json
import os
import re
import shutil
import logging
from datetime import datetime
from database import get_db
from config import LIBRARY_ROOT

log = logging.getLogger(__name__)


# ── helpers ────────────────────────────────────────────────────────────────────

def _run_ytdlp(*args, capture=True):
    cmd = ["yt-dlp", "--no-color", *args]
    log.debug("yt-dlp %s", " ".join(args))
    r = subprocess.run(cmd, capture_output=capture, text=True)
    return r


def _channel_dir(channel_name: str) -> str:
    """Return (and create if needed) the folder for a channel."""
    safe = "".join(c if c.isalnum() or c in " _-" else "_" for c in channel_name).strip()
    path = os.path.join(LIBRARY_ROOT, safe)
    os.makedirs(path, exist_ok=True)
    return path


def _safe_title(title: str) -> str:
    """Sanitize a video title for use as a filename."""
    # Remove characters that are problematic on Linux/Windows/Mac filesystems
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title).strip()
    # Collapse multiple spaces/underscores
    safe = re.sub(r"[ _]{2,}", " ", safe).strip(" _")
    return safe or "untitled"


def _fmt_date(upload_date: str) -> str:
    """Convert YYYYMMDD to YYYY-MM-DD. Returns empty string if invalid."""
    if upload_date and len(upload_date) == 8:
        return f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}"
    return ""


def _output_path(out_dir: str, title: str, upload_date: str, video_id: str) -> str:
    """
    Determine output filepath using collision-aware naming:
      1. Title.mp4
      2. Title (YYYY-MM-DD).mp4       — if title already exists
      3. Title (YYYY-MM-DD) [id].mp4  — final fallback
    """
    safe = _safe_title(title)
    date  = _fmt_date(upload_date)

    candidates = [
        os.path.join(out_dir, f"{safe}.mp4"),
        os.path.join(out_dir, f"{safe} ({date}).mp4") if date else None,
        os.path.join(out_dir, f"{safe} ({date}) [{video_id}].mp4") if date else
        os.path.join(out_dir, f"{safe} [{video_id}].mp4"),
    ]

    for path in candidates:
        if path and not os.path.exists(path):
            return path

    # All candidates exist (extremely unlikely) — force unique with full ID
    return os.path.join(out_dir, f"{safe} [{video_id}].mp4")


def _known_ids(channel_id: str) -> set:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT video_id FROM videos WHERE channel_id = ?", (channel_id,)
        ).fetchall()
    return {r["video_id"] for r in rows}


# ── last_viewed ────────────────────────────────────────────────────────────────

def get_last_viewed(file_path: str) -> str | None:
    """
    Read the file's last-accessed time (atime) from the filesystem.
    Returns ISO timestamp string, or None if the file doesn't exist.

    NOTE: Requires the partition to NOT be mounted with 'noatime'.
    See /api/system/status for the noatime check and user alert.
    """
    try:
        atime = os.stat(file_path).st_atime
        return datetime.utcfromtimestamp(atime).isoformat()
    except (FileNotFoundError, OSError):
        return None


def refresh_last_viewed(channel_id: str | None = None):
    """
    Scan downloaded videos and update last_viewed from filesystem atime.
    Called on boot (all channels) or per-channel after a sync.
    """
    with get_db() as conn:
        query = """SELECT video_id, file_path FROM videos
                   WHERE status = 'downloaded' AND file_path IS NOT NULL"""
        params = []
        if channel_id:
            query += " AND channel_id = ?"
            params.append(channel_id)
        rows = conn.execute(query, params).fetchall()

    updated = 0
    for row in rows:
        lv = get_last_viewed(row["file_path"])
        if lv:
            with get_db() as conn:
                conn.execute(
                    "UPDATE videos SET last_viewed = ? WHERE video_id = ?",
                    (lv, row["video_id"]),
                )
            updated += 1
    log.info("Refreshed last_viewed for %d videos", updated)


# ── file deletion ──────────────────────────────────────────────────────────────

def delete_video_file(file_path: str) -> bool:
    """
    Delete a single video file and its associated .info.json sidecar if present.
    Returns True if the file was deleted, False if it didn't exist or failed.
    """
    deleted = False
    try:
        if file_path and os.path.isfile(file_path):
            os.remove(file_path)
            log.info("Deleted video file: %s", file_path)
            deleted = True
        # Remove .info.json sidecar if present
        info = os.path.splitext(file_path)[0] + ".info.json" if file_path else None
        if info and os.path.isfile(info):
            os.remove(info)
    except OSError as e:
        log.warning("Could not delete file %s: %s", file_path, e)
    return deleted


def delete_channel_folder(channel_name: str) -> bool:
    """
    Delete the entire channel folder and all its contents.
    Returns True on success.
    """
    safe = "".join(c if c.isalnum() or c in " _-" else "_" for c in channel_name).strip()
    path = os.path.join(LIBRARY_ROOT, safe)
    try:
        if os.path.isdir(path):
            shutil.rmtree(path)
            log.info("Deleted channel folder: %s", path)
            return True
    except OSError as e:
        log.warning("Could not delete channel folder %s: %s", path, e)
    return False


# ── metadata fetch ─────────────────────────────────────────────────────────────

def _videos_url(channel_url: str) -> str:
    """Ensure we fetch the /videos tab, not the channel root."""
    url = channel_url.rstrip("/")
    if not url.endswith("/videos"):
        url += "/videos"
    return url


def _pick_thumbnail(thumbnails: list) -> str | None:
    """Pick best thumbnail URL from a yt-dlp thumbnails list."""
    if not thumbnails:
        return None
    for t in thumbnails:
        if t.get("preference") == 1:
            return t.get("url")
    return thumbnails[-1].get("url")


def fetch_channel_metadata(channel_url: str) -> list[dict]:
    """Return list of {video_id, title, upload_date, duration, thumbnail_url} newest-first."""
    r = _run_ytdlp(
        "--flat-playlist",
        "--dump-single-json",
        "--playlist-reverse",
        _videos_url(channel_url),
    )
    if r.returncode != 0:
        log.error("yt-dlp metadata failed: %s", r.stderr[:400])
        return []

    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError:
        log.error("Bad JSON from yt-dlp")
        return []

    entries = data.get("entries", [])
    if entries and entries[0].get("_type") == "playlist":
        entries = entries[0].get("entries", [])

    videos = []
    for e in reversed(entries):
        thumbnails = e.get("thumbnails", [])
        thumb = None
        if thumbnails:
            for t in thumbnails:
                if t.get("width") == 336:
                    thumb = t.get("url")
                    break
            if not thumb:
                thumb = thumbnails[-1].get("url")
        videos.append({
            "video_id":      e.get("id", ""),
            "title":         e.get("title", ""),
            "upload_date":   e.get("upload_date", ""),
            "duration":      e.get("duration") or 0,
            "thumbnail_url": thumb,
        })
    return videos


def resolve_channel_id_and_name(channel_url: str) -> tuple[str, str, str | None]:
    """Return (channel_id, channel_name, thumbnail_url) for a channel URL."""
    r = _run_ytdlp("--flat-playlist", "--dump-single-json", "--playlist-items", "0", channel_url)
    if r.returncode != 0:
        raise RuntimeError(f"Could not resolve channel: {r.stderr[:200]}")
    data = json.loads(r.stdout)
    channel_id    = data.get("channel_id") or data.get("id", "")
    channel_name  = data.get("channel") or data.get("title", "Unknown")
    thumbnail_url = _pick_thumbnail(data.get("thumbnails", []))
    return channel_id, channel_name, thumbnail_url


# ── download ───────────────────────────────────────────────────────────────────

def download_video(video_id: str, channel_name: str, channel_id: str) -> bool:
    """
    Download a single video using yt-dlp.

    Filename convention (collision-aware):
      1. Title.mp4
      2. Title (YYYY-MM-DD).mp4
      3. Title (YYYY-MM-DD) [video_id].mp4

    Metadata (upload date, channel, description, etc.) is embedded
    in the MP4 file itself via --embed-metadata.
    """
    out_dir = _channel_dir(channel_name)
    url = f"https://www.youtube.com/watch?v={video_id}"

    # Fetch title and upload_date first so we can determine the output path
    meta_r = _run_ytdlp(
        "--dump-single-json",
        "--no-playlist",
        url,
    )
    title       = video_id   # fallback
    upload_date = ""
    if meta_r.returncode == 0:
        try:
            meta = json.loads(meta_r.stdout)
            title       = meta.get("title", video_id)
            upload_date = meta.get("upload_date", "")
        except json.JSONDecodeError:
            pass

    out_path = _output_path(out_dir, title, upload_date, video_id)

    r = _run_ytdlp(
        "--format",              "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "--merge-output-format", "mp4",
        "--output",              out_path,
        "--no-playlist",
        "--embed-metadata",      # embeds title, date, channel, description into MP4
        "--no-progress",
        url,
        capture=False,
    )

    if hasattr(r, "returncode") and r.returncode != 0:
        log.warning("Download failed for %s", video_id)
        _mark_video(channel_id, video_id, status="failed")
        return False

    # Confirm the file actually exists at the expected path
    if not os.path.isfile(out_path):
        # yt-dlp may have adjusted the extension — scan the directory
        out_path = _find_file_by_title(out_dir, title, video_id)

    _mark_video(channel_id, video_id, status="downloaded", file_path=out_path)
    return True


def _find_file_by_title(directory: str, title: str, video_id: str) -> str | None:
    """Scan directory for a file matching the title or video_id."""
    safe = _safe_title(title)
    for f in os.listdir(directory):
        name = os.path.splitext(f)[0]
        if name.startswith(safe) or video_id in f:
            return os.path.join(directory, f)
    return None


def download_videos_by_id(video_ids: list[str], channel_id: str) -> dict:
    """Download a specific list of video IDs for a channel."""
    with get_db() as conn:
        ch = conn.execute(
            "SELECT * FROM channels WHERE channel_id = ?", (channel_id,)
        ).fetchone()
    if not ch:
        return {"error": "channel not found"}

    results = {"downloaded": [], "failed": []}
    for vid in video_ids:
        ok = download_video(vid, ch["channel_name"], channel_id)
        (results["downloaded"] if ok else results["failed"]).append(vid)
    return results


def _mark_video(channel_id, video_id, status, file_path=None):
    with get_db() as conn:
        conn.execute(
            """UPDATE videos SET status = ?, downloaded_at = ?, file_path = ?
               WHERE video_id = ? AND channel_id = ?""",
            (status, datetime.utcnow().isoformat(), file_path, video_id, channel_id),
        )
        if status == "downloaded":
            conn.execute(
                "UPDATE channels SET total_downloaded = total_downloaded + 1 WHERE channel_id = ?",
                (channel_id,),
            )


# ── sync logic ─────────────────────────────────────────────────────────────────

def sync_channel(channel_id: str, recent_count: int = 5, catalog_count: int = 5) -> dict:
    with get_db() as conn:
        ch = conn.execute(
            "SELECT * FROM channels WHERE channel_id = ?", (channel_id,)
        ).fetchone()
    if not ch:
        return {"error": "channel not found"}

    log.info("Syncing channel %s", ch["channel_name"])
    all_videos = fetch_channel_metadata(ch["channel_url"])
    if not all_videos:
        return {"error": "could not fetch metadata"}

    known = _known_ids(channel_id)
    total_available = len(all_videos)

    with get_db() as conn:
        for v in all_videos:
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
            """UPDATE channels SET total_available = ?, last_checked = ? WHERE channel_id = ?""",
            (total_available, datetime.utcnow().isoformat(), channel_id),
        )

    recent_targets  = [v for v in all_videos if v["video_id"] not in known][:recent_count]
    catalog_targets = [v for v in reversed(all_videos) if v["video_id"] not in known][:catalog_count]
    to_download     = {v["video_id"]: v for v in recent_targets + catalog_targets}.values()

    results = {"downloaded": [], "failed": []}
    for v in to_download:
        ok = download_video(v["video_id"], ch["channel_name"], channel_id)
        (results["downloaded"] if ok else results["failed"]).append(v["video_id"])

    # Refresh last_viewed for this channel after sync
    refresh_last_viewed(channel_id)

    return results


def sync_all_channels(recent_count: int = 5, catalog_count: int = 5):
    with get_db() as conn:
        channels = conn.execute(
            "SELECT channel_id FROM channels WHERE enabled = 1"
        ).fetchall()
    for ch in channels:
        try:
            sync_channel(ch["channel_id"], recent_count, catalog_count)
        except Exception as e:
            log.exception("Error syncing %s: %s", ch["channel_id"], e)


def download_back_catalog(channel_id: str, count: int) -> dict:
    with get_db() as conn:
        ch = conn.execute(
            "SELECT * FROM channels WHERE channel_id = ?", (channel_id,)
        ).fetchone()
    if not ch:
        return {"error": "channel not found"}

    with get_db() as conn:
        rows = conn.execute(
            """SELECT video_id FROM videos
               WHERE channel_id = ? AND status = 'pending'
               ORDER BY upload_date ASC LIMIT ?""",
            (channel_id, count),
        ).fetchall()

    results = {"downloaded": [], "failed": []}
    for row in rows:
        ok = download_video(row["video_id"], ch["channel_name"], channel_id)
        (results["downloaded"] if ok else results["failed"]).append(row["video_id"])
    return results
