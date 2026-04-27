"""FastAPI app entrypoint."""

from __future__ import annotations

import logging
import subprocess
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.db import init_db
from api.routes import health, recruiter, reports, scout, team
from api.settings import settings

log = logging.getLogger("api")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    init_db()
    worker = None
    if settings.bundle_worker:
        log.info("BUNDLE_WORKER=true; spawning worker as subprocess")
        worker = subprocess.Popen(  # noqa: S603 - args are static / no shell
            [sys.executable, "-u", "-m", "api.workflow.worker"],
            stdout=sys.stdout,
            stderr=sys.stderr,
        )
    try:
        yield
    finally:
        if worker is not None:
            log.info("shutting down bundled worker")
            worker.terminate()
            try:
                worker.wait(timeout=10)
            except subprocess.TimeoutExpired:
                worker.kill()


app = FastAPI(title=settings.app_name, lifespan=lifespan)

# Comma-separated origin URLs allowed to call the API. Configure via env var
# CORS_ORIGINS. Local dev defaults cover :3000; add your Vercel URL on Railway.
_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(reports.router)
app.include_router(team.router)
app.include_router(scout.router)
app.include_router(recruiter.router)
