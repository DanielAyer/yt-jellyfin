# yt-jellyfin

A self-hosted web app that automatically syncs YouTube channels into your [Jellyfin](https://jellyfin.org/) media library using [yt-dlp](https://github.com/yt-dlp/yt-dlp).

Add a channel, set a sync schedule (or trigger it manually), and new uploads land directly in your Jellyfin folder structure — each channel shows up as its own "show."

![status](https://img.shields.io/badge/status-active-success)
![license](https://img.shields.io/badge/license-MIT-blue)

---

## Features

- **Per-channel sync** — grabs the most recent uploads plus a configurable slice of the back catalog on every run
- **Never re-downloads** — tracks every video by ID in SQLite, so re-syncing is always cheap
- **Back-catalog browser** — a video grid per channel lets you pick exactly which older videos to pull, with thumbnails and multi-select
- **Flexible scheduling** — manual, on boot, every N hours, or daily at a specific time, all changeable live from the UI
- **Channel & video thumbnails** — pulled automatically from YouTube on add/sync
- **Runs as a systemd service** — survives reboots, auto-restarts on failure

---

## Screenshot

*(add a screenshot of the dashboard here once you have one)*

---

## Requirements

- Linux (tested on Ubuntu 22.04+ and Debian)
- Python 3.11+
- `yt-dlp` — **do not install via `apt`** (see below)
- An existing [Jellyfin](https://jellyfin.org/) server with a library you control
- A folder where downloaded videos will live (ideally on the same drive/volume as your Jellyfin library)

### Installing yt-dlp

The version of `yt-dlp` in `apt` / `apt-get` is frequently months out of date and will fail with HTTP 400 errors from YouTube's API. Always install directly from the yt-dlp GitHub releases:

```bash
sudo curl -L https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp \
  -o /usr/local/bin/yt-dlp
sudo chmod a+rx /usr/local/bin/yt-dlp
yt-dlp --version
```

To update later:
```bash
sudo curl -L https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp \
  -o /usr/local/bin/yt-dlp
```

If you previously installed via `apt`, the version at `/usr/local/bin/yt-dlp` will take priority. You can verify which one is being used with `which yt-dlp`.

---

## Quickstart, in plain English

Pull this repo down to the machine that will run it (ideally the same machine running Jellyfin, or one with network access to its library folder). Copy `.env.example` to `.env`, then open it and fill in two values: where your videos should be saved (`LIBRARY_ROOT`) and where the app's small database file should live (`DB_PATH`). Install the Python dependencies, start the app, and point your browser at it. From there, everything else — adding channels, setting a schedule, picking which old videos to grab — happens in the web UI.

---

## Installation

```bash
git clone https://github.com/<your-username>/yt-jellyfin.git
sudo mv yt-jellyfin /opt/yt-jellyfin
cd /opt/yt-jellyfin

python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

### Configure

Copy the example environment file and fill in your paths:

```bash
cp .env.example .env
nano .env
```

At minimum you must set:

```
LIBRARY_ROOT=/path/to/your/library
DB_PATH=/path/to/your/library/.ytjf.db
```

`LIBRARY_ROOT` is the folder Jellyfin's library will point at. `DB_PATH` is where the app's SQLite database lives — keeping it inside `LIBRARY_ROOT` means it travels with the drive if you ever move it.

No database setup is required. SQLite needs no separate server or installation step — the app creates the `.db` file and all its tables automatically on first startup, including the parent folder if it doesn't exist yet. `DB_PATH` just needs to point to where you *want* that file to live.

The app validates this configuration on startup and will refuse to run with a clear error message if either variable is missing or if `LIBRARY_ROOT` doesn't exist (e.g. an unmounted drive).

### Run it once to confirm

```bash
venv/bin/python app.py
```

Visit `http://<server-ip>:5000` — if it loads, configuration is good. Stop it with `Ctrl+C` and move on to the systemd service.

### Install as a systemd service

```bash
sudo cp yt-jellyfin.service /etc/systemd/system/
sudo nano /etc/systemd/system/yt-jellyfin.service   # set LIBRARY_ROOT / DB_PATH here too
sudo systemctl daemon-reload
sudo systemctl enable --now yt-jellyfin
```

The service file sets config via `Environment=` lines, which take priority over `.env`. You only need one or the other — pick whichever is easier for your setup.

---

## Jellyfin setup

In Jellyfin: **Dashboard → Libraries → Add Media Library**

- Content type: **Mixed Content** (recommended — avoids Jellyfin guessing at seasons/episodes)
- Folder: your `LIBRARY_ROOT` path

Each channel becomes its own folder/show. Jellyfin picks up new files on its next scan; you can force one from **Dashboard → Libraries → Scan All Libraries**.

---

## Usage

1. Paste a channel URL (e.g. `https://www.youtube.com/@SomeChannel`) and click **Add Channel**
2. The app fetches the channel's metadata and thumbnail in the background
3. Click **Sync Now** to pull recent uploads, or **More Videos** to browse the full back catalog and hand-pick what to download
4. Set a schedule in **⚙ Settings** if you want this to run automatically

---

## Project structure

```
app.py              Flask app + API routes
config.py            Centralized configuration (env vars)
database.py          SQLite schema + connection handling
downloader.py         yt-dlp wrapper + sync logic
scheduler.py          APScheduler job management
templates/            HTML pages
static/                CSS / JS
yt-jellyfin.service    systemd unit file
.env.example          Configuration template
```

---

## API reference

| Method | Path                              | Description                              |
|--------|------------------------------------|-------------------------------------------|
| GET    | `/api/channels`                   | List all channels with stats             |
| POST   | `/api/channels`                   | Add a channel — `{ url }`                |
| DELETE | `/api/channels/<id>`               | Remove a channel (does not delete files) |
| POST   | `/api/channels/<id>/sync`         | Trigger manual sync for one channel      |
| POST   | `/api/channels/<id>/catalog`      | Download N back-catalog videos — `{ count }` |
| POST   | `/api/channels/<id>/download`     | Download specific videos — `{ video_ids }` |
| POST   | `/api/sync-all`                   | Sync all enabled channels                |
| GET    | `/api/channels/<id>/videos`       | List videos — `?status=pending\|downloaded` |
| GET    | `/api/settings`                   | Get schedule + sync-count settings       |
| POST   | `/api/settings`                   | Update settings (applied live)           |
| GET    | `/api/tasks/status`               | Currently running background tasks       |

---

## Known limitations / roadmap

- Tile and dashboard state updates are manual-refresh based, not real-time. A future pass could move to SSE or batched polling for live updates — see the `TODO` in `static/js/videos.js`.
- Single-user, local-network design — no auth layer. Don't expose this directly to the internet without putting a reverse proxy with auth in front of it.
- Filename sanitization is intentionally conservative; very unusual channel names may produce awkward folder names.

---

## Architecture

**Request flow:**

```
Browser
  → Flask routes (app.py)
    → business logic (downloader.py / scheduler.py)
      → database.py (SQLite)
      → filesystem (LIBRARY_ROOT)
```

**Layer breakdown:**

- **`config.py`** — Loads and validates environment variables (`LIBRARY_ROOT`, `DB_PATH`, `HOST`, `PORT`). This runs first, before any other module is imported, so a misconfigured environment fails immediately with a clear error message instead of a confusing stack trace deeper in the app.

- **`database.py`** — SQLite connection helper (`get_db()`) and schema definition (`init_db()`). Four tables:
  - `channels` — one row per tracked channel, including thumbnail URL and sync stats
  - `videos` — one row per video ever seen, keyed by YouTube's `video_id` so a video is never downloaded twice
  - `video_thumbnails` — per-video thumbnail URLs, kept separate from the main `videos` table
  - `settings` — key-value store for schedule configuration, editable live from the UI

- **`downloader.py`** — All `yt-dlp` interaction lives here. Three responsibilities: resolving a channel URL into its ID, name, and thumbnail; fetching a flat list of video metadata for a channel; and invoking `yt-dlp` to actually download a video into `LIBRARY_ROOT/<ChannelName>/`. Also contains the sync logic that decides *which* videos to grab on each run — the N most recent uploads plus N videos from the back catalog.

- **`scheduler.py`** — Wraps APScheduler. Reads the `settings` table to decide whether to run on a fixed interval, daily at a specific time, once on boot, or not at all (manual only). Settings are re-read live whenever changed in the UI — no service restart required.

- **`app.py`** — The Flask app itself. Each route is a thin layer: validate input, kick off work (either a fast synchronous DB read, or a background thread for anything involving `yt-dlp`), and return JSON. In-progress background tasks are tracked in an in-memory dict so the frontend can poll `/api/tasks/status` to know what's currently running.

- **`templates/` + `static/`** — Server-rendered HTML shells (Jinja2) with vanilla JavaScript driving all interactivity through `fetch` calls to the JSON API. No frontend framework or build step, to keep installation simple.

**Example data flow — triggering a sync:**

1. User clicks **Sync Now** → `POST /api/channels/<id>/sync`
2. The route spawns a background thread running `sync_channel()` in `downloader.py`
3. That function calls `yt-dlp` to fetch the channel's current video list, upserts any new video stubs into the `videos` table, then downloads whichever ones are selected for this run (recent uploads + back-catalog slice) via `yt-dlp` again
4. Each completed download updates that video's row to `status='downloaded'` and increments the channel's running totals
5. The frontend polls `/api/tasks/status` until the task clears, then re-fetches `/api/channels` to display the updated numbers

**File structure:**

```
yt-jellyfin/
├── app.py                 Flask app + all API routes
├── config.py               Centralized configuration (env vars, validation)
├── database.py             SQLite schema + connection handling
├── downloader.py            yt-dlp wrapper + sync/download logic
├── scheduler.py             APScheduler job management
├── requirements.txt         Python dependencies
├── .env.example             Configuration template (copy to .env)
├── yt-jellyfin.service       systemd unit file
├── templates/
│   ├── index.html            Main dashboard
│   └── videos.html           Per-channel video grid / back-catalog browser
└── static/
    ├── css/
    │   ├── style.css          Shared styles
    │   └── videos.css         Video grid page styles
    └── js/
        ├── app.js             Dashboard logic
        └── videos.js          Video grid / selection logic
```

---

## License

MIT — see [LICENSE](LICENSE).
