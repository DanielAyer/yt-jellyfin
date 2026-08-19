import subprocess
import json
import os
import re
import shutil
import logging
import threading
from datetime import datetime
from database import get_db
from config import LIBRARY_ROOT

log = logging.getLogger(__name__)

# ── subprocess tracking for force stop ────────────────────────────────────────
# Maps channel_id → active yt-dlp Popen object
# Protected by _proc_lock for thread safety

_active_procs: dict[str, subprocess.Popen] = {}
_proc_lock = threading.Lock()


def _register_proc(channel_id: str, proc: subprocess.Popen):
    with _proc_lock:
        _active_procs[channel_id] = proc


def _unregister_proc(channel_id: str):
    with _proc_lock:
        _active_procs.pop(channel_id, None)


def force_stop_channel(channel_id: str) -> dict:
    """
    Kill the active yt-dlp download for a channel and clean up temp files.

    Cleanup scope:
      - Kill the yt-dlp subprocess
      - Remove any .part files in the channel folder (incomplete downloads)
      - Remove unmerged .mp4 + .m4a pairs where no final merged file exists
      - Leave all fully completed .mp4 files intact

    Returns { ok, message, cleaned_files }
    """
    with get_db() as conn:
        ch = conn.execute(
            "SELECT * FROM channels WHERE channel_id = ?", (channel_id,)
        ).fetchone()
    if not ch:
        return {"ok": False, "message": "Channel not found"}

    # Kill subprocess
    with _proc_lock:
        proc = _active_procs.pop(channel_id, None)
    if proc:
        try:
            proc.kill()
            proc.wait(timeout=5)
            log.info("Killed yt-dlp process for channel %s", channel_id)
        except Exception as e:
            log.warning("Error killing process for %s: %s", channel_id, e)

    # Clean up temp files
    out_dir = _channel_dir(ch["channel_name"])
    cleaned = _cleanup_temp_files(out_dir)

    return {
        "ok":           True,
        "message":      f"Download stopped. Cleaned up {len(cleaned)} temp file(s).",
        "cleaned_files": cleaned,
    }


def _cleanup_temp_files(directory: str) -> list[str]:
    """
    Remove incomplete download artifacts from a channel folder:
      - *.part files (yt-dlp incomplete downloads)
      - *.mp4 + *.m4a pairs with no corresponding merged .mp4
        (left over from interrupted ffmpeg merge)
    """
    cleaned = []
    try:
        files = os.listdir(directory)
    except OSError:
        return cleaned

    # Remove .part files
    for f in files:
        if f.endswith(".part"):
            path = os.path.join(directory, f)
            try:
                os.remove(path)
                cleaned.append(f)
                log.info("Removed temp file: %s", f)
            except OSError as e:
                log.warning("Could not remove %s: %s", f, e)

    # Remove unmerged .m4a files (audio streams not yet merged into .mp4)
    # These appear when ffmpeg merge was interrupted
    for f in files:
        if f.endswith(".m4a"):
            # Check if a corresponding merged .mp4 exists
            stem    = f[:-4]
            mp4     = os.path.join(directory, stem + ".mp4")
            m4a     = os.path.join(directory, f)
            if not os.path.isfile(mp4) and os.path.isfile(m4a):
                try:
                    os.remove(m4a)
                    cleaned.append(f)
                    log.info("Removed unmerged audio: %s", f)
                except OSError as e:
                    log.warning("Could not remove %s: %s", f, e)

    return cleaned



def _run_ytdlp(*args, capture=True):
    """Run yt-dlp and wait for completion. For metadata fetches."""
    cmd = ["yt-dlp", "--no-color", *args]
    log.debug("yt-dlp %s", " ".join(args))
    r = subprocess.run(cmd, capture_output=capture, text=True)
    return r


def _run_ytdlp_tracked(channel_id: str, *args, title_cb=None) -> int:
    """
    Run yt-dlp as a tracked Popen process so it can be killed via force_stop.
    Reads stdout line by line to extract download progress and current video title.
    Returns the exit code, or -1 if killed.
    """
    cmd = ["yt-dlp", "--no-color", *args]
    log.debug("yt-dlp (tracked) %s", " ".join(str(a) for a in args))
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, bufsize=1)
    _register_proc(channel_id, proc)
    current_title = ""
    try:
        for line in proc.stdout:
            line = line.rstrip()
            # Parse current video title: "[youtube] <id>: Downloading webpage"
            # or "[info] <id>: Downloading 1 format(s)"
            # or "[download] Destination: Title [id].ext"
            if "[download] Destination:" in line:
                # Extract filename as title proxy
                dest = line.split("Destination:", 1)[-1].strip()
                current_title = dest[:60] + ("…" if len(dest) > 60 else "")
            # Parse progress: "[download]  45.2% of 234.50MiB at 2.30MiB/s ETA 01:23"
            m = re.search(r"\[download\]\s+([\d.]+)%.*?ETA\s+(\S+)", line)
            if m and title_cb:
                pct = float(m.group(1))
                eta = m.group(2)
                title_cb(channel_id, current_title, pct, eta)
        proc.wait()
        return proc.returncode
    except Exception:
        return -1
    finally:
        _unregister_proc(channel_id)


def _channel_dir(channel_name: str) -> str:
    """Return (and create if needed) the folder for a channel."""
    safe = "".join(c if c.isalnum() or c in " _-" else "_" for c in channel_name).strip()
    path = os.path.join(LIBRARY_ROOT, safe)
    os.makedirs(path, exist_ok=True)
    return path


def _safe_title(title: str) -> str:
    """
    Sanitize a video title for use as a filename stem.
    Removes filesystem-unsafe characters and collapses whitespace.
    """
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title).strip()
    safe = re.sub(r"[ _]{2,}", " ", safe).strip(" _")
    return safe or "untitled"


def _fmt_date_iso(upload_date: str) -> str:
    """
    Convert yt-dlp YYYYMMDD string to ISO compact format YYYYMMDD
    suitable for use in filenames: [20260720].
    Returns empty string if invalid.
    """
    if upload_date and len(upload_date) == 8 and upload_date.isdigit():
        return upload_date  # already YYYYMMDD
    return ""


def _output_path(out_dir: str, title: str, upload_date: str) -> str:
    """
    Determine output filepath using collision-aware naming convention:

      1. Title.mp4
         — first attempt, no suffix

      2. Title_[YYYYMMDD].mp4  (both the existing and new file get renamed)
         — triggered when Title.mp4 already exists

      3. Title_[YYYYMMDD]_1.mp4, Title_[YYYYMMDD]_2.mp4, ...
         — triggered when Title_[YYYYMMDD].mp4 also exists;
           all files sharing that stem get a counter suffix

    Returns the resolved path for the new file. Any existing files that
    need renaming are handled by _resolve_collision(), which also updates
    the DB file_path records.
    """
    safe = _safe_title(title)
    date = _fmt_date_iso(upload_date)

    base_path = os.path.join(out_dir, f"{safe}.mp4")

    if not os.path.exists(base_path):
        # No collision — use clean title
        return base_path

    # Collision on base title — upgrade to dated names
    if not date:
        # No date available — fall back to counter on base title
        return _counter_path(out_dir, safe)

    dated_stem = f"{safe}_[{date}]"
    dated_path = os.path.join(out_dir, f"{dated_stem}.mp4")

    if not os.path.exists(dated_path):
        # Rename the existing base file to dated, return dated path for new file
        _rename_file_in_db(base_path, dated_path)
        os.rename(base_path, dated_path)
        return dated_path

    # Collision on dated name too — upgrade all matching files to countered names
    return _counter_path(out_dir, dated_stem, also_rename=dated_path)


def _rename_file_in_db(old_path: str, new_path: str):
    """Update file_path in the videos table when a file is renamed."""
    try:
        with get_db() as conn:
            conn.execute(
                "UPDATE videos SET file_path = ? WHERE file_path = ?",
                (new_path, old_path),
            )
    except Exception as e:
        log.warning("Could not update DB file_path for rename %s → %s: %s", old_path, new_path, e)


def _counter_path(out_dir: str, stem: str, also_rename: str | None = None) -> str:
    """
    Find the next available counter suffix for a given stem.
    If also_rename is provided, rename that existing file to stem_1.mp4
    and update the DB, then return stem_2.mp4 for the new file.
    """
    # Find all existing files that start with this stem
    existing = sorted([
        f for f in os.listdir(out_dir)
        if f.startswith(stem) and f.endswith(".mp4")
    ])

    if also_rename and os.path.exists(also_rename):
        # Rename the existing dated file to _1
        new_name = os.path.join(out_dir, f"{stem}_1.mp4")
        _rename_file_in_db(also_rename, new_name)
        os.rename(also_rename, new_name)
        return os.path.join(out_dir, f"{stem}_2.mp4")

    # Just find the next available counter
    counter = len(existing) + 1
    while os.path.exists(os.path.join(out_dir, f"{stem}_{counter}.mp4")):
        counter += 1
    return os.path.join(out_dir, f"{stem}_{counter}.mp4")


def _known_ids(channel_id: str) -> set:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT video_id FROM videos WHERE channel_id = ?", (channel_id,)
        ).fetchall()
    return {r["video_id"] for r in rows}


# ── last_viewed ────────────────────────────────────────────────────────────────

def get_last_viewed(file_path: str) -> str | None:
    """
    Read the file's last-accessed time (atime/relatime) from the filesystem.
    Returns ISO timestamp string, or None if the file doesn't exist.
    relatime granularity (24h) is sufficient for rebase/sync decisions.
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


# ── rebase ─────────────────────────────────────────────────────────────────────

def rebase_channel(channel_id: str, missing_action: str = "download") -> dict:
    """
    Reconcile the DB with the filesystem for a channel.

    Phase 1 — filesystem → DB (disk is ground truth):
      Scan the channel folder. For each .mp4 found:
        - Match to a DB record by sanitized title stem
        - If matched: ensure status='downloaded', file_path is current
        - If no match: create a new 'downloaded' record

    Phase 2 — DB → filesystem:
      Find all DB records with no matching file on disk.
      Apply missing_action:
        'download' — reset to 'pending' (resync will grab them)
        'remove'   — delete the DB record entirely

    Returns a summary dict.
    """
    with get_db() as conn:
        ch = conn.execute(
            "SELECT * FROM channels WHERE channel_id = ?", (channel_id,)
        ).fetchone()
    if not ch:
        return {"error": "channel not found"}

    out_dir = _channel_dir(ch["channel_name"])

    # ── Phase 1: scan filesystem ───────────────────────────────────────────────

    # Build a map of sanitized_stem → full_path for all .mp4 files on disk
    disk_files: dict[str, str] = {}
    try:
        for fname in os.listdir(out_dir):
            if fname.endswith(".mp4"):
                stem = os.path.splitext(fname)[0]
                disk_files[stem] = os.path.join(out_dir, fname)
    except FileNotFoundError:
        log.warning("Channel folder not found during rebase: %s", out_dir)

    # Build a single query result: all DB records for this channel
    with get_db() as conn:
        db_rows = conn.execute(
            "SELECT video_id, title, upload_date, file_path, status FROM videos WHERE channel_id = ?",
            (channel_id,)
        ).fetchall()

    # Build lookup: sanitized_title_stem → db_row
    # A DB record may match a plain title stem OR a dated/countered variant
    db_by_stem: dict[str, dict] = {}
    for row in db_rows:
        safe = _safe_title(row["title"])
        db_by_stem[safe] = dict(row)

    confirmed = []
    new_records = []
    matched_video_ids = set()

    for stem, full_path in disk_files.items():
        # Strip date/counter suffixes to get the base title stem for matching
        # e.g. "My Video_[20260720]_1" → "My Video"
        base_stem = re.sub(r"_\[\d{8}\](_\d+)?$", "", stem).strip()
        base_stem = re.sub(r"_\d+$", "", base_stem).strip()

        if base_stem in db_by_stem:
            row = db_by_stem[base_stem]
            matched_video_ids.add(row["video_id"])
            # Update file_path and status if needed
            if row["file_path"] != full_path or row["status"] != "downloaded":
                with get_db() as conn:
                    conn.execute(
                        """UPDATE videos SET status = 'downloaded', file_path = ?,
                           downloaded_at = COALESCE(downloaded_at, ?)
                           WHERE video_id = ?""",
                        (full_path, datetime.utcnow().isoformat(), row["video_id"]),
                    )
            confirmed.append(row["video_id"])
        else:
            # File on disk with no DB record — create one
            # We can't recover the video_id from the filename alone, so we use
            # the stem as a placeholder title and mark it as downloaded.
            # A subsequent sync will reconcile full metadata.
            placeholder_id = f"rebase_{stem[:40]}"
            with get_db() as conn:
                conn.execute(
                    """INSERT OR IGNORE INTO videos
                       (video_id, channel_id, title, status, file_path, downloaded_at)
                       VALUES (?, ?, ?, 'downloaded', ?, ?)""",
                    (placeholder_id, channel_id, stem, full_path, datetime.utcnow().isoformat()),
                )
            new_records.append(stem)

    # ── Phase 2: handle DB records with no matching file ───────────────────────

    missing = [dict(r) for r in db_rows if r["video_id"] not in matched_video_ids]
    missing_handled = []

    for row in missing:
        if missing_action == "remove":
            with get_db() as conn:
                conn.execute("DELETE FROM video_thumbnails WHERE video_id = ?", (row["video_id"],))
                conn.execute("DELETE FROM videos WHERE video_id = ?", (row["video_id"],))
        else:
            # Default: reset to pending so resync will redownload
            with get_db() as conn:
                conn.execute(
                    "UPDATE videos SET status = 'pending', file_path = NULL WHERE video_id = ?",
                    (row["video_id"],)
                )
        missing_handled.append(row["video_id"])

    # Recalculate channel totals
    with get_db() as conn:
        downloaded = conn.execute(
            "SELECT COUNT(*) FROM videos WHERE channel_id = ? AND status = 'downloaded'",
            (channel_id,)
        ).fetchone()[0]
        conn.execute(
            "UPDATE channels SET total_downloaded = ? WHERE channel_id = ?",
            (downloaded, channel_id)
        )

    log.info(
        "Rebase %s: %d confirmed, %d new records, %d missing (%s)",
        ch["channel_name"], len(confirmed), len(new_records),
        len(missing_handled), missing_action
    )

    return {
        "confirmed":       len(confirmed),
        "new_records":     len(new_records),
        "missing_count":   len(missing_handled),
        "missing_action":  missing_action,
        "message": (
            f"Rebase complete — {len(confirmed)} files confirmed, "
            f"{len(new_records)} new records created, "
            f"{len(missing_handled)} missing files "
            f"{'queued for download' if missing_action == 'download' else 'removed from database'}."
        )
    }


# ── file deletion ──────────────────────────────────────────────────────────────

def delete_video_file(file_path: str) -> bool:
    """Delete a single video file. Returns True if deleted."""
    deleted = False
    try:
        if file_path and os.path.isfile(file_path):
            os.remove(file_path)
            log.info("Deleted video file: %s", file_path)
            deleted = True
    except OSError as e:
        log.warning("Could not delete file %s: %s", file_path, e)
    return deleted


def delete_channel_folder(channel_name: str) -> bool:
    """Delete the entire channel folder and all its contents."""
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
    url = channel_url.rstrip("/")
    if not url.endswith("/videos"):
        url += "/videos"
    return url


def _pick_thumbnail(thumbnails: list) -> str | None:
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


def resolve_channel_id_and_name(channel_url: str) -> tuple[str, str, str | None, str]:
    """
    Return (channel_id, channel_name, thumbnail_url, resolved_channel_url).

    If a video URL is passed instead of a channel URL, extracts the channel
    from the video metadata and resolves that instead.
    Returns a 4-tuple so callers know the canonical channel URL to store.
    """
    r = _run_ytdlp("--flat-playlist", "--dump-single-json", "--playlist-items", "0", channel_url)
    if r.returncode != 0:
        raise RuntimeError(f"Could not resolve URL: {r.stderr[:200]}")

    data = json.loads(r.stdout)
    entry_type = data.get("_type", "")

    # If this is a single video, extract its channel and re-resolve
    if entry_type == "video" or (not entry_type and data.get("webpage_url_basename") == "watch"):
        log.info("Video URL detected — extracting channel from video metadata")
        channel_url_from_video = data.get("channel_url") or data.get("uploader_url", "")
        if not channel_url_from_video:
            raise RuntimeError(
                "Video URL detected but could not extract channel URL. "
                "Please enter the channel URL directly."
            )
        # Re-resolve using the channel URL extracted from the video
        r2 = _run_ytdlp("--flat-playlist", "--dump-single-json", "--playlist-items", "0", channel_url_from_video)
        if r2.returncode != 0:
            raise RuntimeError(f"Could not resolve channel from video: {r2.stderr[:200]}")
        data = json.loads(r2.stdout)
        channel_url = channel_url_from_video

    channel_id    = data.get("channel_id") or data.get("id", "")
    channel_name  = data.get("channel") or data.get("title", "Unknown")
    thumbnail_url = _pick_thumbnail(data.get("thumbnails", []))
    return channel_id, channel_name, thumbnail_url, channel_url


# ── download ───────────────────────────────────────────────────────────────────

def estimate_download_size(video_ids: list[str], channel_id: str) -> dict:
    """
    Estimate total download size from stored duration data.
    Uses ~2.5 MB/minute as a baseline for 1080p mp4.
    Returns { estimated_mb, estimated_gb, note }
    """
    MB_PER_MINUTE = 2.5
    with get_db() as conn:
        rows = conn.execute(
            f"""SELECT duration FROM videos
                WHERE channel_id = ? AND video_id IN ({','.join('?' * len(video_ids))})
                AND duration IS NOT NULL""",
            [channel_id] + list(video_ids),
        ).fetchall()

    total_seconds = sum(r["duration"] for r in rows if r["duration"])
    total_minutes = total_seconds / 60
    estimated_mb  = total_minutes * MB_PER_MINUTE
    estimated_gb  = estimated_mb / 1024

    return {
        "estimated_mb": round(estimated_mb, 1),
        "estimated_gb": round(estimated_gb, 2),
        "video_count":  len(video_ids),
        "note":         "Estimate based on ~2.5 MB/min at 1080p. Actual size may vary.",
    }


def download_video(video_id: str, channel_name: str, channel_id: str, title_cb=None) -> bool:
    """
    Download a single video using yt-dlp.

    Filename convention (collision-aware, ISO dates):
      1. Title.mp4
      2. Title_[YYYYMMDD].mp4        — existing file renamed to match
      3. Title_[YYYYMMDD]_N.mp4      — counter applied to all colliding files

    Metadata embedded in MP4 via --embed-metadata.
    Uses tracked Popen so the process can be killed via force_stop_channel().
    """
    out_dir = _channel_dir(channel_name)
    url = f"https://www.youtube.com/watch?v={video_id}"

    # Pre-fetch metadata to determine filename before downloading
    meta_r = _run_ytdlp("--dump-single-json", "--no-playlist", url)
    title       = video_id
    upload_date = ""
    if meta_r.returncode == 0:
        try:
            meta        = json.loads(meta_r.stdout)
            title       = meta.get("title", video_id)
            upload_date = meta.get("upload_date", "")
        except json.JSONDecodeError:
            pass

    out_path = _output_path(out_dir, title, upload_date)

    exit_code = _run_ytdlp_tracked(
        channel_id,
        "--format",              "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "--merge-output-format", "mp4",
        "--output",              out_path,
        "--no-playlist",
        "--embed-metadata",
        "--js-runtimes",         "node",
        url,
        title_cb=title_cb,
    )

    if exit_code != 0:
        if exit_code == -1:
            log.info("Download stopped (force stop) for %s", video_id)
        else:
            log.warning("Download failed for %s (yt-dlp exit code %s)", video_id, exit_code)
        _mark_video(channel_id, video_id, status="failed")
        return False

    # Verify the file actually exists
    if not os.path.isfile(out_path):
        out_path = _find_file_by_title(out_dir, title)

    if not out_path:
        log.error(
            "Download reported success but no file found for %s (%s). "
            "Check yt-dlp version and that the video is publicly available.",
            video_id, title,
        )
        _mark_video(channel_id, video_id, status="failed")
        return False

    _mark_video(channel_id, video_id, status="downloaded", file_path=out_path)
    return True


def _find_file_by_title(directory: str, title: str) -> str | None:
    """Scan directory for a file whose name starts with the sanitized title."""
    safe = _safe_title(title)
    for f in os.listdir(directory):
        if f.startswith(safe) and f.endswith(".mp4"):
            return os.path.join(directory, f)
    return None


def download_next_n(channel_id: str, n: int, progress_cb=None, title_cb=None) -> dict:
    """Download the next N oldest undownloaded videos (back catalog order)."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT video_id FROM videos
               WHERE channel_id = ? AND status = 'pending'
               ORDER BY upload_date ASC LIMIT ?""",
            (channel_id, n),
        ).fetchall()
    return download_videos_by_id([r["video_id"] for r in rows], channel_id, progress_cb, title_cb)


def download_latest_m(channel_id: str, m: int, progress_cb=None, title_cb=None) -> dict:
    """Download the M most recent undownloaded videos."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT video_id FROM videos
               WHERE channel_id = ? AND status = 'pending'
               ORDER BY upload_date DESC LIMIT ?""",
            (channel_id, m),
        ).fetchall()
    return download_videos_by_id([r["video_id"] for r in rows], channel_id, progress_cb, title_cb)


def download_all_pending(channel_id: str, progress_cb=None, title_cb=None) -> dict:
    """Download all undownloaded videos for a channel."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT video_id FROM videos
               WHERE channel_id = ? AND status = 'pending'
               ORDER BY upload_date DESC""",
            (channel_id,),
        ).fetchall()
    return download_videos_by_id([r["video_id"] for r in rows], channel_id, progress_cb, title_cb)


def download_videos_by_id(video_ids: list[str], channel_id: str, progress_cb=None, title_cb=None) -> dict:
    with get_db() as conn:
        ch = conn.execute(
            "SELECT * FROM channels WHERE channel_id = ?", (channel_id,)
        ).fetchone()
    if not ch:
        return {"error": "channel not found"}

    total = len(video_ids)
    results = {"downloaded": [], "failed": []}
    for i, vid in enumerate(video_ids):
        if progress_cb:
            progress_cb(channel_id, i, total, "Downloading")
        ok = download_video(vid, ch["channel_name"], channel_id, title_cb=title_cb)
        (results["downloaded"] if ok else results["failed"]).append(vid)

    if progress_cb:
        progress_cb(channel_id, total, total, "Complete")

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
            "UPDATE channels SET total_available = ?, last_checked = ? WHERE channel_id = ?",
            (total_available, datetime.utcnow().isoformat(), channel_id),
        )

    recent_targets  = [v for v in all_videos if v["video_id"] not in known][:recent_count]
    catalog_targets = [v for v in reversed(all_videos) if v["video_id"] not in known][:catalog_count]
    to_download     = {v["video_id"]: v for v in recent_targets + catalog_targets}.values()

    results = {"downloaded": [], "failed": []}
    for v in to_download:
        ok = download_video(v["video_id"], ch["channel_name"], channel_id)
        (results["downloaded"] if ok else results["failed"]).append(v["video_id"])

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
