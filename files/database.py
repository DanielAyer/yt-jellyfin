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

            INSERT OR IGNORE INTO settings (key, value) VALUES
                ('schedule_mode',      'manual'),
                ('schedule_hours',     '6'),
                ('schedule_time',      '03:00'),
                ('recent_count',       '5'),
                ('catalog_count',      '5'),
                ('disk_threshold_pct', '10');
        """)

        # Safe column migrations — handles upgrades from earlier schema versions
        _add_column_if_missing(conn, "videos", "last_viewed",  "TEXT")
        _add_column_if_missing(conn, "videos", "archived",     "INTEGER NOT NULL DEFAULT 0")
        _add_column_if_missing(conn, "videos", "archive_path", "TEXT")
