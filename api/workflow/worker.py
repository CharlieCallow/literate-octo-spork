"""Polling worker. Run as a separate process: `python -m api.workflow.worker`."""

from __future__ import annotations

import logging
import time

from api.db import init_db
from api.workflow.runner import claim_one_job, execute

POLL_INTERVAL_S = 3.0


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")
    init_db()
    log = logging.getLogger("worker")
    log.info("worker started")
    while True:
        job = claim_one_job()
        if job is None:
            time.sleep(POLL_INTERVAL_S)
            continue
        log.info("running stage=%s report=%s job=%s", job.stage, job.report_id, job.id)
        execute(job)


if __name__ == "__main__":
    main()
