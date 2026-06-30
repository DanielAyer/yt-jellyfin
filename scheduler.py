import logging
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from database import get_db
from downloader import sync_all_channels

log = logging.getLogger(__name__)
scheduler = BackgroundScheduler(daemon=True)
JOB_ID = "sync_all"


def _get_settings() -> dict:
    with get_db() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    return {r["key"]: r["value"] for r in rows}


def _do_sync():
    s = _get_settings()
    try:
        rc = int(s.get("recent_count", 5))
        cc = int(s.get("catalog_count", 5))
    except ValueError:
        rc, cc = 5, 5
    log.info("Scheduled sync starting (recent=%d, catalog=%d)", rc, cc)
    sync_all_channels(rc, cc)
    log.info("Scheduled sync complete")


def apply_schedule():
    """Read current settings and (re)configure the APScheduler job."""
    s = _get_settings()
    mode = s.get("schedule_mode", "manual")

    if scheduler.get_job(JOB_ID):
        scheduler.remove_job(JOB_ID)

    if mode == "manual":
        log.info("Scheduler: manual mode — no recurring job")
        return

    if mode == "boot":
        # fire once on startup (already called from app startup if desired)
        log.info("Scheduler: boot-only mode")
        return

    if mode == "interval":
        hours = max(1, int(s.get("schedule_hours", 6)))
        scheduler.add_job(_do_sync, IntervalTrigger(hours=hours), id=JOB_ID, replace_existing=True)
        log.info("Scheduler: every %d hours", hours)
        return

    if mode == "daily":
        time_str = s.get("schedule_time", "03:00")
        hour, minute = (int(x) for x in time_str.split(":"))
        scheduler.add_job(_do_sync, CronTrigger(hour=hour, minute=minute), id=JOB_ID, replace_existing=True)
        log.info("Scheduler: daily at %02d:%02d", hour, minute)
        return


def start_scheduler(run_on_boot: bool = False):
    scheduler.start()
    apply_schedule()
    s = _get_settings()
    if run_on_boot or s.get("schedule_mode") == "boot":
        log.info("Running boot sync")
        _do_sync()
