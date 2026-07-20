# Changelog

All notable changes to this project will be documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versions follow [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

---

## [0.1.0] - 2026-06-24

Initial beta release.

### Added

**Core sync engine**
- Add YouTube channels by URL (`@handle` or `/channel/UC…` format)
- Per-channel sync: grabs N most recent uploads + N oldest unwatched back-catalog videos per run
- Never re-downloads: every video tracked by YouTube `video_id` in SQLite — re-syncing is always safe and cheap
- Back-catalog download with user-specified count
- Manual sync per channel, or sync all channels at once

**Scheduling**
- Four schedule modes: manual only, once on boot, every N hours, daily at a specific time
- Schedule changes apply live — no service restart required
- Powered by APScheduler, persisted in SQLite settings table

**Web UI — dashboard**
- Channel cards with hero thumbnail (auto-fetched from YouTube on add)
- Per-channel stats: total available, downloaded, pending
- Sync Now, Back Catalog, and More Videos actions per card
- Thumbnail backfill: channels added before thumbnail support auto-update on next dashboard load
- Global Sync All button
- Settings modal for schedule and per-sync count configuration

**Web UI — video grid (More Videos)**
- Full per-channel video grid with YouTube thumbnail backdrops
- Filter bar: All / Not Downloaded / Downloaded
- Click-to-select tiles with press animation and green Download badge
- Select All / Deselect All respecting active filter
- Download Selected queues chosen videos as a background task
- Manual refresh button

**Jellyfin integration**
- Videos downloaded directly into `LIBRARY_ROOT/<ChannelName>/` folder structure
- Each channel appears as a show in Jellyfin (Mixed Content library type recommended)
- No Jellyfin API integration required — file drop is sufficient

**Infrastructure**
- Flask backend with JSON API
- SQLite database: channels, videos, video_thumbnails, settings tables
- Background task runner with in-memory status tracking
- Centralized config via `config.py` — reads from environment variables or `.env` file
- Startup config validation: refuses to run with missing or invalid `LIBRARY_ROOT` / `DB_PATH`, with clear error messages
- systemd service file for boot persistence and auto-restart
- `yt-dlp` used for all YouTube interaction (metadata fetch and download)
- MIT licensed

### Known limitations

- Tile and dashboard state updates require manual refresh — real-time push (SSE) is a planned future improvement (see `TODO` in `static/js/videos.js`)
- Single-user, local-network design — no authentication layer
- No disk space awareness — downloads proceed regardless of available space (planned for v0.2.0)
- No content archiving — old unwatched content accumulates indefinitely (planned for v0.2.0)
- No in-app update mechanism (planned for v0.2.0)

---

*Changelog maintained by [Daniel Ayer](https://github.com/DanielAyer)*
