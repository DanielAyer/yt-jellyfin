"""
Update system for yt-jellyfin.

Handles:
- Reading the local VERSION file
- Fetching available releases from GitHub
- Applying a specific release (tarball download + file extraction)
- Preserving user data (.env, SQLite DB) across updates

Does NOT restart the service — the user is instructed to do that manually
after a successful update. This avoids needing sudo or process self-kill logic.

All network calls are wrapped in try/except to handle offline scenarios cleanly.
"""
import os
import re
import shutil
import tarfile
import tempfile
import logging
import urllib.request
import urllib.error
import json
from datetime import datetime

log = logging.getLogger(__name__)

# ── constants ──────────────────────────────────────────────────────────────────

GITHUB_API_RELEASES = "https://api.github.com/repos/DanielAyer/yt-jellyfin/releases"
GITHUB_API_HEADERS  = {
    "Accept":     "application/vnd.github+json",
    "User-Agent": "yt-jellyfin-updater",
}

# Files and folders that belong to the user, never overwritten during update
PRESERVE = {
    ".env",
    ".ytjf.db",
    "venv",
    "__pycache__",
}

# Install directory — same as WorkingDirectory in the systemd service
INSTALL_DIR = os.path.dirname(os.path.abspath(__file__))


# ── version helpers ────────────────────────────────────────────────────────────

def get_local_version() -> str:
    """
    Read the local VERSION file.
    Returns the version string (e.g. '0.1.0') or 'unknown' if not found.
    """
    version_path = os.path.join(INSTALL_DIR, "VERSION")
    try:
        with open(version_path, "r") as f:
            return f.read().strip()
    except FileNotFoundError:
        log.warning("VERSION file not found at %s", version_path)
        return "unknown"


def _version_tuple(version_str: str) -> tuple:
    """Convert '0.2.1' to (0, 2, 1) for comparison. Returns (0,) for unknown."""
    try:
        return tuple(int(x) for x in re.findall(r"\d+", version_str))
    except Exception:
        return (0,)


def is_git_install() -> bool:
    """Return True if the install directory contains a .git folder."""
    return os.path.isdir(os.path.join(INSTALL_DIR, ".git"))


# ── GitHub API ─────────────────────────────────────────────────────────────────

def _github_get(url: str) -> dict | list | None:
    """
    Make a GET request to the GitHub API.
    Returns parsed JSON or None on any error.
    """
    try:
        req = urllib.request.Request(url, headers=GITHUB_API_HEADERS)
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        log.warning("GitHub API request failed: %s", e)
        return None
    except Exception as e:
        log.warning("Unexpected error fetching %s: %s", url, e)
        return None


def fetch_releases() -> list[dict]:
    """
    Fetch all releases from GitHub.
    Returns a list of release dicts:
        {
            tag:          "v0.2.0",
            version:      "0.2.0",
            name:         "Release title",
            published_at: "2026-07-20T...",
            notes:        "Release body / changelog excerpt",
            tarball_url:  "https://...",
            is_latest:    bool,
        }
    Returns empty list on error.
    """
    data = _github_get(GITHUB_API_RELEASES)
    if not data or not isinstance(data, list):
        return []

    releases = []
    for i, r in enumerate(data):
        tag = r.get("tag_name", "")
        # Strip leading 'v' for version comparison
        version = tag.lstrip("v")
        releases.append({
            "tag":          tag,
            "version":      version,
            "name":         r.get("name") or tag,
            "published_at": r.get("published_at", ""),
            "notes":        r.get("body", "").strip(),
            "tarball_url":  r.get("tarball_url", ""),
            "is_latest":    i == 0,  # GitHub returns newest first
        })

    return releases


def check_for_updates() -> dict:
    """
    Compare local version against available releases.
    Returns:
        {
            local_version:   str,
            is_git_install:  bool,
            status:          "up_to_date" | "update_available" | "ahead" | "unknown" | "offline",
            latest_version:  str | None,
            releases:        list[dict],
            message:         str,
        }
    """
    local = get_local_version()
    releases = fetch_releases()

    if not releases:
        return {
            "local_version":  local,
            "is_git_install": is_git_install(),
            "status":         "offline",
            "latest_version": None,
            "releases":       [],
            "message":        "Could not reach GitHub. Check your internet connection.",
        }

    latest = releases[0]
    local_t  = _version_tuple(local)
    latest_t = _version_tuple(latest["version"])

    if local == "unknown":
        status  = "unknown"
        message = "Could not determine local version. Consider reinstalling."
    elif local_t < latest_t:
        status  = "update_available"
        message = f"Update available: v{latest['version']}"
    elif local_t > latest_t:
        status  = "ahead"
        message = f"Local version ({local}) is ahead of latest release ({latest['version']})."
    else:
        status  = "up_to_date"
        message = f"You are running the latest release (v{local})."

    return {
        "local_version":  local,
        "is_git_install": is_git_install(),
        "status":         status,
        "latest_version": latest["version"],
        "releases":       releases,
        "message":        message,
    }


# ── apply update ───────────────────────────────────────────────────────────────

def apply_update(tag: str) -> dict:
    """
    Download and apply a specific release by tag.

    Steps:
      1. Block if any background tasks are running (caller should check first)
      2. Find the release in GitHub's release list
      3. Download the tarball to a temp directory
      4. Extract it
      5. Copy app files into INSTALL_DIR, skipping PRESERVE items
      6. Return success with restart instructions

    .env and the SQLite DB are never touched.
    Returns {ok, message, error} dict.
    """
    log.info("Applying update: %s", tag)

    # ── 1. Find the release ────────────────────────────────────────────────────
    releases = fetch_releases()
    if not releases:
        return {"ok": False, "error": "offline", "message": "Could not reach GitHub."}

    release = next((r for r in releases if r["tag"] == tag), None)
    if not release:
        return {"ok": False, "error": "not_found", "message": f"Release {tag} not found on GitHub."}

    tarball_url = release["tarball_url"]
    if not tarball_url:
        return {"ok": False, "error": "no_tarball", "message": f"No tarball available for {tag}."}

    # ── 2. Download to temp dir ────────────────────────────────────────────────
    try:
        with tempfile.TemporaryDirectory() as tmp:
            tarball_path = os.path.join(tmp, f"yt-jellyfin-{tag}.tar.gz")

            log.info("Downloading %s → %s", tarball_url, tarball_path)
            try:
                req = urllib.request.Request(tarball_url, headers=GITHUB_API_HEADERS)
                with urllib.request.urlopen(req, timeout=60) as resp, \
                     open(tarball_path, "wb") as f:
                    shutil.copyfileobj(resp, f)
            except urllib.error.URLError as e:
                return {"ok": False, "error": "download_failed", "message": f"Download failed: {e}"}

            # ── 3. Extract ─────────────────────────────────────────────────────
            extract_dir = os.path.join(tmp, "extracted")
            os.makedirs(extract_dir)

            try:
                with tarfile.open(tarball_path, "r:gz") as tar:
                    tar.extractall(extract_dir)
            except tarfile.TarError as e:
                return {"ok": False, "error": "extract_failed", "message": f"Extraction failed: {e}"}

            # GitHub tarballs contain a single top-level folder
            # e.g. DanielAyer-yt-jellyfin-abc1234/
            contents = os.listdir(extract_dir)
            if len(contents) != 1:
                return {"ok": False, "error": "unexpected_structure",
                        "message": "Unexpected tarball structure. Please reinstall manually."}

            source_dir = os.path.join(extract_dir, contents[0])

            # ── 4. Copy files, preserving user data ────────────────────────────
            _copy_update(source_dir, INSTALL_DIR)

        log.info("Update %s applied successfully", tag)
        return {
            "ok":      True,
            "message": (
                f"✓ Version {tag} applied successfully.\n\n"
                f"Please restart the service to activate the new version:\n\n"
                f"    sudo systemctl restart yt-jellyfin"
            ),
        }

    except Exception as e:
        log.exception("Unexpected error during update: %s", e)
        return {"ok": False, "error": "unexpected", "message": f"Unexpected error: {e}"}


def _copy_update(source_dir: str, dest_dir: str):
    """
    Recursively copy files from source_dir to dest_dir,
    skipping anything in PRESERVE.
    """
    for item in os.listdir(source_dir):
        if item in PRESERVE:
            log.info("Preserving user file: %s", item)
            continue

        src  = os.path.join(source_dir, item)
        dest = os.path.join(dest_dir, item)

        if os.path.isdir(src):
            # Recurse into subdirectories
            os.makedirs(dest, exist_ok=True)
            _copy_update(src, dest)
        else:
            try:
                shutil.copy2(src, dest)
                log.debug("Copied: %s", item)
            except OSError as e:
                log.warning("Could not copy %s: %s", item, e)
