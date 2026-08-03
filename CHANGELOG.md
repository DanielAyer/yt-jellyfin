# Changelog

All notable changes to this project will be documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versions follow [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

---

## [1.0] - 2026-07-31

### Added

**First-run setup wizard**
- Browser-based setup wizard on first launch — no manual file editing required
- Import configuration automatically from Jellyfin via API (detects all configured libraries)
- Manual configuration path for installs not cohabitating with Jellyfin
- Jellyfin URL pre-populated intelligently; fully user-editable
- "I don't see my library" option with direct link to Jellyfin library management
- Writes `.env` on save; copy-to-clipboard restart command displayed on success
- Setup mode routing — all routes redirect to `/setup` until configured

**User-controlled download modes**
- Three bulk download options per channel: Download All, Download Next N (back catalog), Download Latest M (recent)
- Individual tile selection retained for fine-grained control
- Estimated download size shown before confirming bulk downloads (duration-based, clearly labelled as approximate)
- Force stop — kills active yt-dlp process and cleans up `.part` and unmerged temp files; completed downloads unaffected

**Per-channel settings**
- Per-channel N and M overrides via ⚙ gear icon on each channel card
- Inherits global defaults from Settings; reset to global with one click
- Stored in new `channel_settings` table in SQLite

**Disk space guard**
- Pre-download space check on all download operations
- User-configurable minimum free disk threshold (hard floor: 5%)
- Orange warning banner on all pages when space is below threshold — dismissible but re-appears on next detection
- Inline alert when a download is blocked by low disk space

**Rebase**
- Reconciles the database with the filesystem as ground truth
- Phase 1: scans channel folder, creates DB records for files with no entry
- Phase 2: handles DB entries with no matching file per user's configured action (download missing / remove entry)
- Configurable missing-file action in Settings

**Log viewer**
- Dedicated `/logs` page with last 200 lines of systemd journal on load
- Level filter: All / Info / Warning / Error
- Follow live toggle — SSE stream from journalctl
- Download log as `.txt` file
- Accessible from header on all pages

**Update system**
- Check for updates via GitHub releases API (no auth required)
- Version picker dropdown — select any published release, not just latest
- Release notes displayed per selected version
- Apply update downloads tarball, extracts, copies app files, preserves `.env` and SQLite DB
- Blocks update if downloads are in progress
- Displays restart command with copy-to-clipboard on completion

**Guided installer**
- `install.sh` — idempotent install script safe to re-run
- Checks python3 ≥ 3.11, python3-venv, ffmpeg, git, curl
- Always installs yt-dlp from GitHub releases, removes outdated apt version if present
- Auto-detects Jellyfin user from running processes, patches service file automatically
- Adds service user to `systemd-journal` group for log viewer access
- Checks outbound port 443 and inbound app port; offers to configure ufw if active
- Prompts for install directory (default `/opt/yt-jellyfin`)

**Documentation**
- Firewall configuration section (pfSense/router-level and host-level ufw)
- Permissions section (service user, directory ownership, journal group)
- Development environment reference
- yt-dlp install warning — apt version is always outdated, curl install documented
- GitHub issue templates: bug report and feature request

### Changed

- Download behavior is now fully user-controlled — no automatic downloads on channel add or sync
- Scheduling removed from UI (available via APScheduler internally; planned as advanced option in future)
- Filename convention updated: `Title.mp4` → `Title_[YYYYMMDD].mp4` → `Title_[YYYYMMDD]_N.mp4` (collision-aware, ISO dates)
- Metadata embedded in MP4 files via `--embed-metadata`
- Channel delete now removes files from disk (previously DB-only)
- Video delete removes file from disk
- systemd service file no longer contains hardcoded `LIBRARY_ROOT`/`DB_PATH` — configuration comes from `.env` via setup wizard
- Channel card stat label "pending" renamed to "not downloaded" for clarity
- Failed download count added to channel card stats (shown in red when > 0)

### Fixed

- Download verification: yt-dlp exit code 0 no longer falsely marks a video as downloaded if no file was produced — now correctly marks as failed with a clear log message
- Setup wizard no longer resets YouTube folder radio selection on library dropdown change
- Double `youtube/youtube` path no longer generated when selecting an existing youtube subfolder

### Known limitations

- Tile and dashboard state updates require manual refresh — real-time push (SSE) planned for future release (see `TODO` in `static/js/videos.js`)
- Single-user, local-network design — no authentication layer
- Race condition possible if sync and rebase run simultaneously on the same channel — namespaced task IDs planned for next release

---

## [beta-1] - 2026-06-24

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

- Tile and dashboard state updates require manual refresh — real-time push (SSE) is a planned future improvement
- Single-user, local-network design — no authentication layer
- No disk space awareness — downloads proceed regardless of available space
- No content archiving — old unwatched content accumulates indefinitely
- No in-app update mechanism

---

*Changelog maintained by [Daniel Ayer](https://github.com/DanielAyer)*

