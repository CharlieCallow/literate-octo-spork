"""Polling worker. Run as a separate process: `python -m api.workflow.worker`.

Catches SIGINT and SIGTERM so it can finish the current job before exiting.
Also fires the daily Scout digest if SCOUT_AUTO_RUN is on and the configured
local time has passed today, and grades unresolved performance-ledger calls
on a weekly cadence."""

from __future__ import annotations

import contextlib
import logging
import signal
import time
import traceback
from datetime import UTC, datetime, timedelta
from types import FrameType

from api import app_settings
from api.db import init_db
from api.scout_runner import parse_hhmm, run_scout, should_run_today
from api.settings import settings
from api.workflow.runner import claim_one_job, execute, reclaim_stuck_jobs

POLL_INTERVAL_S = 3.0

_should_stop = False


def _request_shutdown(signum: int, _frame: FrameType | None) -> None:
    global _should_stop
    _should_stop = True
    logging.getLogger("worker").info("signal %d received -- finishing current job and exiting", signum)


def _maybe_run_scheduled_scout(log: logging.Logger) -> None:
    if not settings.scout_auto_run:
        return
    target = parse_hhmm(settings.scout_daily_time)
    if target is None:
        log.warning("invalid SCOUT_DAILY_TIME=%r; auto-run disabled", settings.scout_daily_time)
        return
    if datetime.now().time() < target:
        return
    if not should_run_today():
        return
    log.info("scheduled Scout digest kicking off (target=%s)", settings.scout_daily_time)
    try:
        run_scout()
    except Exception:  # noqa: BLE001
        log.exception("scheduled Scout digest failed")


# Performance-ledger grading runs at most once a week. The first run after
# any unresolved call ages past its horizon will pick it up; we don't need
# tighter cadence than that.
_WEEKLY = timedelta(days=7)


def _maybe_grade_calls(log: logging.Logger) -> None:
    try:
        from api import calls
    except Exception:  # noqa: BLE001
        return  # the calls module / tables may not exist on a stale deploy
    last = calls.last_evaluation_at()
    if last is not None:
        last_aware = last if last.tzinfo else last.replace(tzinfo=UTC)
        if datetime.now(UTC) - last_aware < _WEEKLY:
            return
    log.info("weekly call-evaluation kicking off")
    try:
        n = calls.evaluate_due()
        log.info("graded %d due calls", n)
    except Exception:  # noqa: BLE001
        log.exception("call evaluation failed")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s -- %(message)s")
    init_db()
    log = logging.getLogger("worker")
    log.info("worker started")
    log.info(
        "models loaded: haiku=%r  sonnet=%r  opus=%r",
        settings.model_haiku, settings.model_sonnet, settings.model_opus,
    )
    if not settings.anthropic_api_key:
        log.error("ANTHROPIC_API_KEY is empty -- agent calls will fail")
    if settings.scout_auto_run:
        log.info("scout auto-run enabled; daily target=%s", settings.scout_daily_time)

    signal.signal(signal.SIGINT, _request_shutdown)
    with contextlib.suppress(AttributeError, ValueError):
        signal.signal(signal.SIGTERM, _request_shutdown)  # not on Windows main thread

    while not _should_stop:
        try:
            _poll_iter(log)
        except Exception as e:  # noqa: BLE001
            # Anything escaping execute() / claim_one_job() / the watchdog
            # used to crash the worker process; the supervisor would
            # respawn it but the report would lose its place. Capture and
            # continue so a single bad iteration is visible on /workers
            # without taking the whole worker down.
            tb = traceback.format_exc()
            log.exception("unhandled exception in poll loop -- continuing")
            with contextlib.suppress(Exception):
                app_settings.record_worker_error(
                    f"{type(e).__name__}: {e}\n\n{tb}"
                )
            # Brief sleep so a pathological repeat exception doesn't
            # spin the CPU; respect the shutdown signal.
            for _ in range(20):  # ~2s
                if _should_stop:
                    break
                time.sleep(0.1)

    log.info("worker stopped cleanly")


def _poll_iter(log: logging.Logger) -> None:
    """One iteration of the worker's main loop. Lifted out of main() so
    the outer try/except can keep the process alive even when something
    deep in the runner / DB layer raises unexpectedly."""
    # Heartbeat first -- if the rest of the loop blows up, at least
    # the dashboard sees the worker is alive. Cheap upsert.
    app_settings.record_worker_heartbeat()

    # Watchdog runs every poll: jobs left in `running` past their
    # mode-specific deadline are routed through the failure path so
    # the same backoff-and-retry logic that handles real exceptions
    # picks them up. Cheap (one indexed query). Catches the case
    # where the previous worker died mid-job.
    try:
        n = reclaim_stuck_jobs()
        if n:
            log.warning("watchdog reclaimed %d stuck job(s)", n)
    except Exception:  # noqa: BLE001
        log.exception("watchdog reclaim_stuck_jobs failed (non-blocking)")

    job = claim_one_job()
    if job is None:
        _maybe_run_scheduled_scout(log)
        _maybe_grade_calls(log)
        for _ in range(int(POLL_INTERVAL_S * 10)):
            if _should_stop:
                break
            time.sleep(0.1)
        return
    log.info("running stage=%s report=%s job=%s attempt=%d",
             job.stage, job.report_id, job.id, job.attempts)
    execute(job)


if __name__ == "__main__":
    main()
