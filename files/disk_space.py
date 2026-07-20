"""
Disk space guard.

Checks free space on any given path's partition and compares against a
user-configured threshold (minimum percent free). A hard floor of 5% is
always enforced regardless of the user's setting.

Used before every download and archive operation.
"""
import os
import shutil
import logging
from database import get_db

log = logging.getLogger(__name__)

HARD_FLOOR_PCT = 5.0  # minimum free % enforced regardless of user setting


def get_disk_usage(path: str) -> dict:
    """
    Return disk usage stats for the partition containing `path`.
    Returns dict with keys: total, used, free, free_pct.
    Returns None if the path doesn't exist or stat fails.
    """
    try:
        usage = shutil.disk_usage(path)
        free_pct = (usage.free / usage.total) * 100
        return {
            "total":    usage.total,
            "used":     usage.used,
            "free":     usage.free,
            "free_pct": round(free_pct, 2),
        }
    except Exception as e:
        log.warning("Could not stat disk usage for %s: %s", path, e)
        return None


def get_threshold() -> float:
    """
    Return the user-configured minimum free disk percent from settings.
    Falls back to HARD_FLOOR_PCT if not set or invalid.
    Always enforces HARD_FLOOR_PCT as a minimum.
    """
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = 'disk_threshold_pct'"
            ).fetchone()
        if row:
            val = float(row["value"])
            return max(val, HARD_FLOOR_PCT)
    except Exception as e:
        log.warning("Could not read disk threshold setting: %s", e)
    return HARD_FLOOR_PCT


def check_space(path: str, label: str = "disk") -> dict:
    """
    Check whether `path`'s partition has sufficient free space.

    Returns a dict:
        ok        — True if space is sufficient, False if blocked
        free_pct  — current free percent (float)
        threshold — threshold that was checked against (float)
        label     — human-readable label for the store being checked
        message   — human-readable warning string if not ok, else None
    """
    threshold = get_threshold()
    usage = get_disk_usage(path)

    if usage is None:
        return {
            "ok":        False,
            "free_pct":  None,
            "threshold": threshold,
            "label":     label,
            "message":   f"Could not read disk usage for {label} ({path}). "
                         f"Check that the drive is mounted.",
        }

    free_pct = usage["free_pct"]
    ok = free_pct >= threshold

    return {
        "ok":        ok,
        "free_pct":  free_pct,
        "threshold": threshold,
        "label":     label,
        "message":   (
            f"Low disk space on {label}: {free_pct:.1f}% free "
            f"(threshold: {threshold:.0f}%). Free up space or lower "
            f"your threshold in Settings before downloading."
        ) if not ok else None,
    }


def check_library_space() -> dict:
    """Check free space on the LIBRARY_ROOT partition."""
    from config import LIBRARY_ROOT
    return check_space(LIBRARY_ROOT, label="library")
