"""
Background task scheduler using APScheduler.

Runs scheduled scrape jobs at configured intervals.
"""
from __future__ import annotations

import json
import logging
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from . import database as db
from .engine import run_scrape

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def _execute_scheduled(schedule_id: str, symbol: str, timeframes: list[str], range_key: str):
    """Callback executed by APScheduler."""
    logger.info("Scheduled scrape running: %s (%s)", symbol, schedule_id)
    job_id = db.create_job(symbol, timeframes, range_key)
    try:
        run_scrape(symbol, timeframes, range_key, job_id=job_id)
        db.touch_schedule(schedule_id)
    except Exception as exc:
        logger.error("Scheduled scrape failed: %s", exc)


def start_scheduler() -> BackgroundScheduler:
    global _scheduler
    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.start()

    # re-register persisted schedules
    for task in db.list_schedules():
        _add_apscheduler_job(task)

    logger.info("Scheduler started  active_tasks=%d", len(_scheduler.get_jobs()))
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def add_scheduled_task(symbol: str, timeframes: list[str], range_key: str, interval_minutes: int) -> str:
    sid = db.create_schedule(symbol, timeframes, range_key, interval_minutes)

    if _scheduler:
        task = db.list_schedules()
        for t in task:
            if t.id == sid:
                _add_apscheduler_job(t)
                break

    logger.info("Scheduled task created: %s every %dm", symbol, interval_minutes)
    return sid


def remove_scheduled_task(schedule_id: str) -> bool:
    db.delete_schedule(schedule_id)
    if _scheduler:
        try:
            _scheduler.remove_job(schedule_id)
        except Exception:
            pass
    return True


def _add_apscheduler_job(task) -> None:
    if not _scheduler:
        return
    tfs = json.loads(task.timeframes) if isinstance(task.timeframes, str) else task.timeframes
    _scheduler.add_job(
        _execute_scheduled,
        trigger=IntervalTrigger(minutes=task.interval_minutes),
        id=task.id,
        args=[task.id, task.symbol, tfs, task.range],
        replace_existing=True,
        max_instances=1,
    )