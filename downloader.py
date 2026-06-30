import subprocess
import json
import os
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
    safe = "".join(c if c.isalnum() or c in " _-" else "_" for c in channel_name).strip()
    path = os.path.join(LIBRARY_ROOT, safe)
    os.makedirs(path, exist_ok=True)
    return path


def _known_ids(channel_id: str) -> set:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT video_id FROM videos WHERE channel_id = ?", (channel_id,)
        ).fetchall()
    return {r["video_id"] for r in rows}


# ── metadata fetch ─────────────────────────────────────────────────────────────

def _videos_url(channel_url: str) -> str:
    """Ensure we fetch the /videos tab, not the channel root (which returns sub-playlists)."""
    url = channel_url.rstrip("/")
    if not url.endswith("/videos"):
        url += "/videos"
    return url


def _pick_thumbnail(thumbnails: list, prefer_size: int = 0) -> str | None:
    """Pick best thumbnail URL from a yt-dlp thumbnails list."""
    if not thumbnails:
        return None
    # prefer avatar (preference=1) for channels, otherwise highest resolution
    for t in thumbnails:
        if t.get("preference") == 1:
            return t.get("url")
    # fall back to last entry (usually highest res)
    return thumbnails[-1].get("url")


def fetch_channel_metadata(channel_url: str) -> list[dict]:
    """Return list of {video_id, title, upload_date, duration, thumbnail_url} newest-first."""
    r = _run_ytdlp(
        "--flat-playlist",
        "--dump-single-json",
        "--playlist-reverse",   # oldest first so we can reverse for newest-first
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
    # Guard against nested playlist structure
    if entries and entries[0].get("_type") == "playlist":
        entries = entries[0].get("entries", [])

    videos = []
    for e in reversed(entries):          # newest-first
        thumbnails = e.get("thumbnails", [])
        # for flat-playlist entries, pick hqdefault-sized thumbnail
        thumb = None
        if thumbnails:
            # prefer 336x188 (hqdefault) or just take the last one
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
    channel_id   = data.get("channel_id") or data.get("id", "")
    channel_name = data.get("channel") or data.get("title", "Unknown")
    thumbnail_url = _pick_thumbnail(data.get("thumbnails", []))
    return channel_id, channel_name, thumbnail_url


# ── download ───────────────────────────────────────────────────────────────────

def download_video(video_id: str, channel_name: str, channel_id: str) -> bool:
    out_dir = _channel_dir(channel_name)
    url = f"https://www.youtube.com/watch?v={video_id}"

    r = _run_ytdlp(
        "--format", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "--merge-output-format", "mp4",
        "--output", os.path.join(out_dir, "%(upload_date)s - %(title)s [%(id)s].%(ext)s"),
        "--no-playlist",
        "--write-info-json",
        "--no-progress",
        url,
        capture=False,
    )

    if r != 0 and hasattr(r, "returncode") and r.returncode != 0:
        log.warning("Download failed for %s", video_id)
        _mark_video(channel_id, video_id, status="failed")
        return False

    file_path = _find_file_for_id(out_dir, video_id)
    _mark_video(channel_id, video_id, status="downloaded", file_path=file_path)
    return True


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


def _find_file_for_id(directory: str, video_id: str) -> str | None:
    for f in os.listdir(directory):
        if video_id in f and f.endswith(".mp4"):
            return os.path.join(directory, f)
    return None


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


# ── sync logic ────────────────────────────────────────────────────────────────

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

    # upsert all video stubs + thumbnails
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

    # 5 most recent not yet downloaded
    recent_targets = [v for v in all_videos if v["video_id"] not in known][:recent_count]

    # next 5 oldest from back-catalog (oldest first, skip already downloaded)
    catalog_targets = [v for v in reversed(all_videos) if v["video_id"] not in known][:catalog_count]

    to_download = {v["video_id"]: v for v in recent_targets + catalog_targets}.values()

    results = {"downloaded": [], "failed": []}
    for v in to_download:
        ok = download_video(v["video_id"], ch["channel_name"], channel_id)
        (results["downloaded"] if ok else results["failed"]).append(v["video_id"])

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
