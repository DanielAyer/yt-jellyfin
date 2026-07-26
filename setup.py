"""
First-run setup system for yt-jellyfin.

Handles:
- Detecting whether the app needs first-run configuration
- Querying the Jellyfin API to discover library folders
- Writing the .env file with user-provided configuration
- Validating and creating paths before saving

Two configuration paths are supported:
  A) Import from Jellyfin — queries Jellyfin's local API for library folders
  B) Manual             — user types paths directly

The Jellyfin API key can be found at:
  <jellyfin-url>/web/index.html#/dashboard/keys

Jellyfin URL resolution order:
  1. JELLYFIN_URL set in .env or environment
  2. window.location.hostname:8096 (JS fallback, works for cohabitating installs)
  3. User prompted to set JELLYFIN_URL manually if connection fails
"""
import os
import logging
import urllib.request
import urllib.error
import json

log = logging.getLogger(__name__)

INSTALL_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH    = os.path.join(INSTALL_DIR, ".env")


# ── setup state ────────────────────────────────────────────────────────────────

def is_setup_complete() -> bool:
    """
    Returns True if the minimum required config is present and valid.
    Checks the environment (which includes any loaded .env values).
    """
    library_root = os.environ.get("LIBRARY_ROOT", "").strip()
    db_path      = os.environ.get("DB_PATH", "").strip()
    return bool(library_root and db_path and os.path.isdir(library_root))


def get_config_defaults() -> dict:
    """
    Return any pre-configured values from the environment that the
    setup wizard can use as defaults. All values are optional.
    """
    return {
        "jellyfin_url":     os.environ.get("JELLYFIN_URL", "").strip(),
        "jellyfin_api_key": os.environ.get("JELLYFIN_API_KEY", "").strip(),
        "library_root":     os.environ.get("LIBRARY_ROOT", "").strip(),
        "db_path":          os.environ.get("DB_PATH", "").strip(),
    }


# ── Jellyfin API ───────────────────────────────────────────────────────────────

def query_jellyfin_libraries(jellyfin_url: str, api_key: str) -> list[dict]:
    """
    Query the Jellyfin API for configured media library folders.

    For each library location, checks for an existing 'youtube' subfolder.

    Returns a list of dicts:
        {
            name:           str,
            path:           str,
            type:           str,
            youtube_exists: bool,
            youtube_path:   str | None,
        }

    Raises ValueError with a user-friendly message on any error.

    Jellyfin API key location:
        <jellyfin_url>/web/index.html#/dashboard/keys
    """
    url = jellyfin_url.rstrip("/") + "/Library/VirtualFolders"
    headers = {
        "X-Emby-Token": api_key,
        "Accept":        "application/json",
    }

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise ValueError("Invalid API key — check your Jellyfin API key and try again.")
        raise ValueError(f"Jellyfin returned HTTP {e.code}. Is the URL correct?")
    except urllib.error.URLError as e:
        raise ValueError(
            f"Could not reach Jellyfin at {jellyfin_url}. "
            f"Check the URL and that Jellyfin is running. "
            f"If Jellyfin is on a different machine, set JELLYFIN_URL in your .env file."
        )
    except Exception as e:
        raise ValueError(f"Unexpected error querying Jellyfin: {e}")

    libraries = []
    for folder in data:
        name      = folder.get("Name", "")
        lib_type  = folder.get("CollectionType", "unknown")
        locations = folder.get("Locations", [])
        for path in locations:
            # Check for existing youtube subfolder (case-insensitive)
            youtube_path = None
            try:
                for entry in os.listdir(path):
                    if entry.lower() == "youtube" and os.path.isdir(os.path.join(path, entry)):
                        youtube_path = os.path.join(path, entry)
                        break
            except OSError:
                pass

            libraries.append({
                "name":           name,
                "path":           path,
                "type":           lib_type,
                "youtube_exists": youtube_path is not None,
                "youtube_path":   youtube_path,
            })

    return libraries


# ── .env writer ────────────────────────────────────────────────────────────────

def write_env(library_root: str, db_path: str,
              jellyfin_url: str = "",
              jellyfin_api_key: str = "") -> dict:
    """
    Validate, create if needed, and write configuration to .env.
    Returns { ok, message, error }.
    """
    library_root = library_root.strip()
    db_path      = db_path.strip()

    if not library_root:
        return {"ok": False, "error": "LIBRARY_ROOT is required."}
    if not db_path:
        return {"ok": False, "error": "DB_PATH is required."}

    # Create LIBRARY_ROOT if it doesn't exist
    if not os.path.isdir(library_root):
        try:
            os.makedirs(library_root, exist_ok=True)
            log.info("Created LIBRARY_ROOT: %s", library_root)
        except OSError as e:
            return {
                "ok":    False,
                "error": f"Could not create LIBRARY_ROOT {library_root}: {e}\n"
                         f"Check that the parent directory exists and is writable.",
            }

    # Ensure DB parent directory exists
    db_dir = os.path.dirname(db_path)
    if db_dir and not os.path.isdir(db_dir):
        try:
            os.makedirs(db_dir, exist_ok=True)
        except OSError as e:
            return {"ok": False, "error": f"Could not create DB directory {db_dir}: {e}"}

    # Write .env
    lines = [
        "# yt-jellyfin configuration",
        "# Generated by the setup wizard. Edit manually if needed.",
        "",
        f"LIBRARY_ROOT={library_root}",
        f"DB_PATH={db_path}",
    ]
    if jellyfin_url:
        lines += ["", f"# Jellyfin integration", f"JELLYFIN_URL={jellyfin_url}"]
    if jellyfin_api_key:
        lines += [f"JELLYFIN_API_KEY={jellyfin_api_key}"]

    try:
        with open(ENV_PATH, "w") as f:
            f.write("\n".join(lines) + "\n")
        log.info("Configuration written to %s", ENV_PATH)
    except OSError as e:
        return {"ok": False, "error": f"Could not write .env file: {e}"}

    # Apply to current process so config is immediately available
    os.environ["LIBRARY_ROOT"] = library_root
    os.environ["DB_PATH"]      = db_path
    if jellyfin_url:
        os.environ["JELLYFIN_URL"] = jellyfin_url
    if jellyfin_api_key:
        os.environ["JELLYFIN_API_KEY"] = jellyfin_api_key

    return {
        "ok":      True,
        "message": (
            "Configuration saved successfully.\n\n"
            "Please restart the service to activate:\n\n"
            "    sudo systemctl restart yt-jellyfin"
        ),
    }
