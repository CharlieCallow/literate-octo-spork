"""Auto-respawning supervisor for the bundled worker subprocess.

Replaces the one-shot subprocess.Popen at API startup. If the worker
crashes the supervisor notices on its next poll and spawns a fresh
process. Without this the worker dies silently, the API keeps serving,
and the user has to redeploy to recover -- exactly what we hit on the
nuclear-power-deals report.

Crash stats are exposed for /workers/status so a crash loop is visible
on the dashboard rather than buried in Railway logs.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Self

log = logging.getLogger("worker-supervisor")

POLL_INTERVAL_S = 5.0
# Capped exponential backoff between respawn attempts so a worker that
# crashes immediately on boot doesn't fill the logs and rate-limit any
# downstream services it talks to.
RESPAWN_BACKOFF_BASE_S = 2.0
RESPAWN_BACKOFF_CEILING_S = 30.0


@dataclass
class SupervisorStats:
    """Crash bookkeeping read by /workers/status."""
    crash_count: int = 0
    last_exit_code: int | None = None
    last_crash_at: datetime | None = None
    last_spawn_at: datetime | None = None
    consecutive_failures: int = 0  # resets when the worker stays alive a while
    pid: int | None = None


@dataclass
class WorkerSupervisor:
    """Owns one worker subprocess and respawns it on exit.

    Hold a single instance globally (see `current()` / `set_current()`)
    so the workers route can read the stats."""

    stats: SupervisorStats = field(default_factory=SupervisorStats)
    process: subprocess.Popen[bytes] | None = None
    _stop: bool = False
    _task: asyncio.Task[None] | None = None

    def _spawn(self) -> None:
        log.info("spawning worker subprocess")
        self.process = subprocess.Popen(  # noqa: S603 - args are static / no shell
            [sys.executable, "-u", "-m", "api.workflow.worker"],
            stdout=sys.stdout,
            stderr=sys.stderr,
        )
        self.stats.pid = self.process.pid
        self.stats.last_spawn_at = datetime.now(UTC)

    async def _supervise(self) -> None:
        # Initial spawn. If anything in the boot path raised before we
        # got here, we'd have nothing -- so handle that path defensively.
        if self.process is None or self.process.poll() is not None:
            self._spawn()
        # Track when the current run started -- if the worker stays alive
        # past a "stable" threshold, reset the consecutive-failure counter
        # so a single crash later doesn't carry forward forever.
        run_started = datetime.now(UTC)
        stable_after_s = 60.0

        while not self._stop:
            await asyncio.sleep(POLL_INTERVAL_S)
            if self.process is None:
                self._spawn()
                run_started = datetime.now(UTC)
                continue

            rc = self.process.poll()
            if rc is None:
                # Still alive. Reset consecutive-failure if we've been up
                # long enough -- crash-loop detection should care about
                # *recent* repeated failures, not historical ones.
                if (
                    self.stats.consecutive_failures > 0
                    and (datetime.now(UTC) - run_started).total_seconds() > stable_after_s
                ):
                    self.stats.consecutive_failures = 0
                continue

            # Worker exited.
            self.stats.last_exit_code = rc
            self.stats.last_crash_at = datetime.now(UTC)
            self.stats.crash_count += 1
            self.stats.consecutive_failures += 1
            log.warning(
                "worker exited with code %d (crash #%d, consecutive %d) -- respawning",
                rc, self.stats.crash_count, self.stats.consecutive_failures,
            )
            backoff = min(
                RESPAWN_BACKOFF_CEILING_S,
                RESPAWN_BACKOFF_BASE_S * (2 ** max(0, self.stats.consecutive_failures - 1)),
            )
            await asyncio.sleep(backoff)
            self._spawn()
            run_started = datetime.now(UTC)

    def start(self) -> Self:
        """Kick off the supervise loop on the current event loop."""
        self._task = asyncio.create_task(self._supervise())
        return self

    async def stop(self) -> None:
        """Cancel the supervise loop and tear down the worker cleanly."""
        self._stop = True
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
        if self.process is not None and self.process.poll() is None:
            log.info("shutting down worker subprocess")
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()


# ---------------------------------------------------------------------------
# Module-level singleton so /workers/status can read crash stats without
# threading the supervisor through DI.
# ---------------------------------------------------------------------------

_current: WorkerSupervisor | None = None


def current() -> WorkerSupervisor | None:
    return _current


def set_current(s: WorkerSupervisor | None) -> None:
    global _current
    _current = s
