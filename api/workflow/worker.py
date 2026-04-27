"""Polling worker. Run as a separate process: `python -m api.workflow.worker`.

Catches SIGINT and SIGTERM so it can finish the current job before exiting."""

from __future__ import annotations

import logging
import signal
import time
from types import FrameType

from api.db import init_db
from api.settings import settings
from api.workflow.runner import claim_one_job, execute

POLL_INTERVAL_S = 3.0

_should_stop = False


def _request_shutdown(signum: int, _frame: FrameType | None) -> None:
    global _should_stop
    _should_stop = True
    logging.getLogger("worker").info("signal %d received -- finishing current job and exiting", signum)


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

    signal.signal(signal.SIGINT, _request_shutdown)
    try:
        signal.signal(signal.SIGTERM, _request_shutdown)
    except (AttributeError, ValueError):
        pass  # SIGTERM not supported on Windows main thread

    while not _should_stop:
        job = claim_one_job()
        if job is None:
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
