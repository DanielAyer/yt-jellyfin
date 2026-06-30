"""
Centralized configuration, loaded from environment variables.

Required variables (no defaults — must be set via .env or the environment):
    LIBRARY_ROOT  — root folder where downloaded videos are stored
    DB_PATH       — path to the SQLite database file

Optional variables (sensible defaults provided):
    HOST          — interface Flask binds to (default 0.0.0.0)
    PORT          — port Flask binds to (default 5000)

Set these either by copying .env.example to .env and editing it, or by
setting real environment variables (e.g. Environment= lines in the
systemd service file). Real env vars always take priority over .env.
"""
import os
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

LIBRARY_ROOT = os.environ.get("LIBRARY_ROOT", "")
DB_PATH      = os.environ.get("DB_PATH", "")
HOST         = os.environ.get("HOST", "0.0.0.0")
PORT         = int(os.environ.get("PORT", "5000"))


def validate_config():
    """Fail fast with a clear error if required config is missing."""
    missing = []
    if not LIBRARY_ROOT:
        missing.append("LIBRARY_ROOT")
    if not DB_PATH:
        missing.append("DB_PATH")

    if missing:
        print(
            "\n[CONFIG ERROR] Missing required environment variable(s): "
            + ", ".join(missing) +
            "\n\nCopy .env.example to .env and fill in the values, or set "
            "them as real environment variables (e.g. in the systemd "
            "service file's Environment= lines).\n",
            file=sys.stderr,
        )
        sys.exit(1)

    if not os.path.isdir(LIBRARY_ROOT):
        print(
            f"\n[CONFIG ERROR] LIBRARY_ROOT does not exist or is not a "
            f"directory: {LIBRARY_ROOT}\n\nCreate it, or check that your "
            f"external drive is mounted, before starting the app.\n",
            file=sys.stderr,
        )
        sys.exit(1)
