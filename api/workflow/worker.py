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
from datetime import UTC, datetime, timedelta
from types import FrameType

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
            continue
        log.info("running stage=%s report=%s job=%s attempt=%d",
                 job.stage, job.report_id, job.id, job.attempts)
        execute(job)

    log.info("worker stopped cleanly")


if __name__ == "__main__":
    main()
