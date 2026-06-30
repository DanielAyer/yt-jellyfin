# This project is for personal and non-commercial use ONLY!  No authorization is given to monetize or commercially distribute this project.

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

- Linux (tested on Ubuntu 22.04+)
- Python 3.11+
- [`yt-dlp`](https://github.com/yt-dlp/yt-dlp) installed and on `PATH`
- An existing [Jellyfin](https://jellyfin.org/) server with a library you control
- A folder where downloaded videos will live (ideally on the same drive/volume as your Jellyfin library)

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

## License

MIT — see [LICENSE](LICENSE).
