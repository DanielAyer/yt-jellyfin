"""
Centralized configuration for yt-jellyfin.

Reads from environment variables, with optional .env file support via
python-dotenv. Real environment variables (e.g. systemd Environment= lines)
always take priority over .env.

Required variables (LIBRARY_ROOT, DB_PATH) are validated at runtime by
setup.py rather than at import time, allowing the app to start in setup
mode when not yet configured.
"""
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

LIBRARY_ROOT = os.environ.get("LIBRARY_ROOT", "").strip()
DB_PATH      = os.environ.get("DB_PATH", "").strip()
HOST         = os.environ.get("HOST", "0.0.0.0").strip()
PORT         = int(os.environ.get("PORT", "5000"))

# Jellyfin integration (optional — set during setup wizard)
JELLYFIN_URL     = os.environ.get("JELLYFIN_URL", "").strip()
JELLYFIN_API_KEY = os.environ.get("JELLYFIN_API_KEY", "").strip()


def validate_config():
    """
    Legacy validation entry point — kept for compatibility.
    In setup-mode installs this is a no-op; validation is handled
    by setup.py and the /setup route in app.py.
    """
    pass
