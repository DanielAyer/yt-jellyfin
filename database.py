import sqlite3
import os
from config import DB_PATH


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _add_column_if_missing(conn, table: str, column: str, col_type: str):
    """SQLite doesn't support ALTER TABLE ADD COLUMN IF NOT EXISTS — this does."""
    existing = [
        row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    ]
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS channels (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id       TEXT    NOT NULL UNIQUE,
                channel_name     TEXT    NOT NULL,
                channel_url      TEXT    NOT NULL,
                added_at         TEXT    NOT NULL DEFAULT (datetime('now')),
                last_checked     TEXT,
                total_available  INTEGER DEFAULT 0,
                total_downloaded INTEGER DEFAULT 0,
                enabled          INTEGER NOT NULL DEFAULT 1,
                thumbnail_url    TEXT
            );

            CREATE TABLE IF NOT EXISTS video_thumbnails (
                video_id      TEXT PRIMARY KEY,
                thumbnail_url TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS videos (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                video_id      TEXT    NOT NULL UNIQUE,
                channel_id    TEXT    NOT NULL REFERENCES channels(channel_id),
                title         TEXT,
                upload_date   TEXT,
                duration      INTEGER,
                downloaded_at TEXT,
                file_path     TEXT,
                is_recent     INTEGER NOT NULL DEFAULT 0,
                status        TEXT    NOT NULL DEFAULT 'pending'
            );

            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            -- Per-channel settings — inherits from global settings if not set
            CREATE TABLE IF NOT EXISTS channel_settings (
                channel_id  TEXT PRIMARY KEY REFERENCES channels(channel_id),
                n_catalog   INTEGER,   -- Download next N from back catalog (NULL = use global)
                m_recent    INTEGER    -- Download latest M recent uploads (NULL = use global)
            );

            INSERT OR IGNORE INTO settings (key, value) VALUES
                ('n_catalog',           '5'),
                ('m_recent',            '5'),
                ('disk_threshold_pct',  '10'),
                ('rebase_missing_action', 'download'),
                ('log_review_n',        '6'),
                ('log_review_unit',     'hours');

            -- TODO: scheduled sync removed from UI — add back as advanced option in future
            -- ('schedule_mode',  'manual'),
            -- ('schedule_hours', '6'),
            -- ('schedule_time',  '03:00'),
        """)

        # Safe column migrations
        _add_column_if_missing(conn, "videos", "last_viewed",  "TEXT")
        _add_column_if_missing(conn, "videos", "archived",     "INTEGER NOT NULL DEFAULT 0")
        _add_column_if_missing(conn, "videos", "archive_path", "TEXT")


def get_channel_settings(channel_id: str) -> dict:
    """
    Return effective settings for a channel.
    Per-channel values take priority over global settings.
    Falls back to global, then hardcoded defaults.
    """
    with get_db() as conn:
        # Global settings
        global_rows = conn.execute("SELECT key, value FROM settings").fetchall()
        global_s = {r["key"]: r["value"] for r in global_rows}

        # Per-channel overrides
        ch_row = conn.execute(
            "SELECT * FROM channel_settings WHERE channel_id = ?", (channel_id,)
        ).fetchone()

    n_catalog = (
        int(ch_row["n_catalog"]) if ch_row and ch_row["n_catalog"] is not None
        else int(global_s.get("n_catalog", 5))
    )
    m_recent = (
        int(ch_row["m_recent"]) if ch_row and ch_row["m_recent"] is not None
        else int(global_s.get("m_recent", 5))
    )

    return {
        "n_catalog":            n_catalog,
        "m_recent":             m_recent,
        "disk_threshold_pct":   float(global_s.get("disk_threshold_pct", 10)),
        "rebase_missing_action": global_s.get("rebase_missing_action", "download"),
    }


def save_channel_settings(channel_id: str, n_catalog: int | None, m_recent: int | None):
    """
    Save per-channel settings. Pass None to reset to global default.
    """
    with get_db() as conn:
        conn.execute(
            """INSERT INTO channel_settings (channel_id, n_catalog, m_recent)
               VALUES (?, ?, ?)
               ON CONFLICT(channel_id) DO UPDATE SET
                 n_catalog = excluded.n_catalog,
                 m_recent  = excluded.m_recent""",
            (channel_id, n_catalog, m_recent),
        )
