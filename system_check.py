"""System health checks and boot log review for yt-jellyfin."""
import subprocess, shutil, sys, re, logging, os
from datetime import datetime, timezone

log = logging.getLogger(__name__)

LOG_COLORS = {
    "system": "#a0aab8", "error": "#e05252", "warning": "#e0a652",
    "channel": "#c792ea", "add_channel": "#89ddff", "settings": "#f78c6c",
}

def _run(cmd):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return r.returncode, (r.stdout + r.stderr).strip()
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return -1, str(e)

def check_python():
    v = sys.version_info
    version = f"{v.major}.{v.minor}.{v.micro}"
    ok = v.major >= 3 and v.minor >= 11
    return {"name": "Python", "version": version, "ok": ok,
            "message": None if ok else f"Python 3.11+ required, found {version}"}

def check_nodejs():
    code, out = _run(["node", "--version"])
    if code != 0:
        return {"name": "Node.js", "version": None, "ok": False,
                "message": "Node.js not found. Install Node.js 22+: curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - && sudo apt-get install -y nodejs"}
    version = out.lstrip("v")
    major = int(version.split(".")[0]) if version else 0
    ok = major >= 22
    return {"name": "Node.js", "version": version, "ok": ok,
            "message": None if ok else f"Node.js 22+ required, found v{version}. Upgrade via NodeSource."}

def check_ffmpeg():
    found = shutil.which("ffmpeg") is not None
    version = None
    if found:
        _, out = _run(["ffmpeg", "-version"])
        m = re.search(r"ffmpeg version (\S+)", out)
        version = m.group(1) if m else "unknown"
    return {"name": "ffmpeg", "version": version, "ok": found,
            "message": None if found else "ffmpeg not found. Install: sudo apt-get install -y ffmpeg"}

def check_ytdlp():
    code, out = _run(["yt-dlp", "--version"])
    if code != 0:
        return {"name": "yt-dlp", "version": None, "ok": False, "age_days": None,
                "message": "yt-dlp not found. Install from GitHub releases."}
    version = out.strip()
    age_days = None
    warnings = []
    is_nightly = "nightly" in version.lower() or "@" in version
    try:
        # Parse stable version date (YYYY.MM.DD) — skip for nightly builds
        if not is_nightly:
            ver_date = datetime.strptime(version.split("@")[0].strip(), "%Y.%m.%d").replace(tzinfo=timezone.utc)
            age_days = (datetime.now(timezone.utc) - ver_date).days
            if age_days > 30:
                warnings.append(
                    f"yt-dlp is {age_days} days old — update to avoid 403 errors. "
                    f"If stable still fails, try: sudo yt-dlp --update-to nightly"
                )
    except ValueError:
        pass
    js_configured = False
    for p in ["/etc/yt-dlp.conf", os.path.expanduser("~/.config/yt-dlp/config")]:
        try:
            if "js-runtimes" in open(p).read():
                js_configured = True; break
        except FileNotFoundError:
            pass
    if not js_configured:
        warnings.append("JS runtime not configured. Run: echo '--js-runtimes node' | sudo tee /etc/yt-dlp.conf")
    return {"name": "yt-dlp", "version": version, "ok": len(warnings) == 0,
            "age_days": age_days, "message": " ".join(warnings) or None}

def check_dependencies():
    checks = [check_python(), check_nodejs(), check_ffmpeg(), check_ytdlp()]
    try:
        from disk_space import check_library_space
        space = check_library_space()
        checks.append({"name": "Disk space",
                        "version": f"{space['free_pct']:.1f}% free" if space.get("free_pct") else None,
                        "ok": space["ok"], "message": space["message"] if not space["ok"] else None})
    except Exception as e:
        checks.append({"name": "Disk space", "version": None, "ok": False, "message": str(e)})
    return checks

def _unit_to_seconds(n, unit):
    return int(n * {"minutes": 60, "hours": 3600, "days": 86400,
                    "weeks": 604800, "months": 2592000, "years": 31536000}.get(unit, 3600))

def _parse_log_line(line):
    if not line.strip(): return None
    upper = line.upper()
    level = "ERROR" if (" ERROR " in upper or "[ERROR]" in upper) else \
            "WARNING" if (" WARNING " in upper or " WARN " in upper) else "INFO"
    lower = line.lower()
    if "downloader" in lower or "sync" in lower or "download" in lower: source = "channel"
    elif "add_channel" in lower or "remove_channel" in lower: source = "add_channel"
    elif "settings" in lower or "config" in lower: source = "settings"
    else: source = "system"
    color = LOG_COLORS["error"] if level == "ERROR" else \
            LOG_COLORS["warning"] if level == "WARNING" else LOG_COLORS.get(source, LOG_COLORS["system"])
    return {"timestamp": line[:15], "level": level, "source": source, "color": color, "message": line}

def review_boot_logs(n=6, unit="hours"):
    seconds = _unit_to_seconds(n, unit)
    entries, error_count, warning_count = [], 0, 0
    # Current boot
    try:
        r = subprocess.run(["journalctl", "-u", "yt-jellyfin", "-b", "0",
                            "--no-pager", "--output=short", "-n", "500"],
                           capture_output=True, text=True, timeout=15)
        if r.returncode == 0:
            for line in r.stdout.splitlines():
                e = _parse_log_line(line)
                if e:
                    entries.append(e)
                    if e["level"] == "ERROR": error_count += 1
                    elif e["level"] == "WARNING": warning_count += 1
    except Exception: pass
    # Previous boot within time window
    try:
        r = subprocess.run(["journalctl", "-u", "yt-jellyfin", "-b", "-1",
                            "--no-pager", "--output=short",
                            "--since", f"{int(seconds)}s ago", "-n", "500"],
                           capture_output=True, text=True, timeout=15)
        if r.returncode == 0:
            for line in r.stdout.splitlines():
                e = _parse_log_line(line)
                if e:
                    entries.append(e)
                    if e["level"] == "ERROR": error_count += 1
                    elif e["level"] == "WARNING": warning_count += 1
    except Exception: pass
    entries.sort(key=lambda e: e.get("timestamp", ""))
    return {"error_count": error_count, "warning_count": warning_count,
            "has_errors": error_count > 0, "entries": entries[-100:],
            "window_description": f"last {n} {unit} + current boot"}

def get_channel_logs(channel_name, limit=50):
    entries = []
    try:
        r = subprocess.run(["journalctl", "-u", "yt-jellyfin", "-b", "0",
                            "--no-pager", "--output=short", "-n", "1000"],
                           capture_output=True, text=True, timeout=15)
        if r.returncode == 0:
            for line in r.stdout.splitlines():
                if channel_name.lower() in line.lower():
                    e = _parse_log_line(line)
                    if e: entries.append(e)
    except Exception as e:
        log.warning("Could not fetch channel logs: %s", e)
    return entries[-limit:]
