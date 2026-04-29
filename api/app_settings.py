"""Runtime-editable settings stored in the app_settings table.

Falls back to the env-var defaults from api.settings when a key is missing.
The agent code reads through here so a model swap takes effect on the next
report without a redeploy."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Final

from sqlmodel import Session

from api.db import engine
from api.models import AppSetting
from api.settings import settings as env_settings

# Keys we expose for runtime override.
KEY_MODEL_HAIKU: Final = "model_haiku"
KEY_MODEL_SONNET: Final = "model_sonnet"
KEY_MODEL_OPUS: Final = "model_opus"
KEY_COST_PER_REPORT: Final = "cost_per_report_usd"
KEY_COST_PER_DAY: Final = "cost_per_day_usd"
KEY_WORKER_HEARTBEAT: Final = "worker_heartbeat_iso"
# Last unhandled exception inside the worker poll loop -- captured so
# /workers can render the traceback without the user having to dig in
# Railway logs.
KEY_WORKER_LAST_ERROR: Final = "worker_last_error"
KEY_WORKER_LAST_ERROR_AT: Final = "worker_last_error_at_iso"

KNOWN_KEYS: tuple[str, ...] = (
    KEY_MODEL_HAIKU, KEY_MODEL_SONNET, KEY_MODEL_OPUS,
    KEY_COST_PER_REPORT, KEY_COST_PER_DAY,
)


def get(key: str, default: str = "") -> str:
    try:
        with Session(engine) as session:
            row = session.get(AppSetting, key)
            if row:
                return row.value
    except Exception:  # noqa: BLE001 - DB may not exist (test contexts)
        pass
    return default


def set(key: str, value: str) -> None:  # noqa: A001 - shadowing intentional, scoped to module
    with Session(engine) as session:
        row = session.get(AppSetting, key)
        if row is None:
            row = AppSetting(key=key, value=value)
        else:
            row.value = value
            row.updated_at = datetime.now(UTC)
        session.add(row)
        session.commit()


# ---- accessors that respect overrides ----

def model_haiku() -> str:
    return get(KEY_MODEL_HAIKU, env_settings.model_haiku)


def model_sonnet() -> str:
    return get(KEY_MODEL_SONNET, env_settings.model_sonnet)


def model_opus() -> str:
    return get(KEY_MODEL_OPUS, env_settings.model_opus)


def cost_per_report_usd() -> float:
    raw = get(KEY_COST_PER_REPORT, "")
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return env_settings.cost_per_report_usd


def cost_per_day_usd() -> float:
    raw = get(KEY_COST_PER_DAY, "")
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return env_settings.cost_per_day_usd


def all_settings() -> dict[str, str]:
    """Return every known key with its current effective value."""
    return {
        KEY_MODEL_HAIKU: model_haiku(),
        KEY_MODEL_SONNET: model_sonnet(),
        KEY_MODEL_OPUS: model_opus(),
        KEY_COST_PER_REPORT: str(cost_per_report_usd()),
        KEY_COST_PER_DAY: str(cost_per_day_usd()),
    }


# ---------------------------------------------------------------------------
# Worker heartbeat
# ---------------------------------------------------------------------------
# The worker writes this each poll cycle so the dashboard can distinguish
# "worker alive but no work to do" from "worker process is dead". The
# AuditLog `last_activity` only ticks when an agent emits an event, which
# doesn't happen when the worker is idle -- so we need a separate signal.

def record_worker_heartbeat() -> None:
    """Stamp the current UTC time as the worker's last-seen timestamp.

    Best-effort -- a DB blip shouldn't take the worker down. If the
    upsert fails we log it and move on; the next iteration tries again."""
    try:
        set(KEY_WORKER_HEARTBEAT, datetime.now(UTC).isoformat())
    except Exception:  # noqa: BLE001
        import logging
        logging.getLogger("worker").warning(
            "heartbeat write failed -- /workers will report worker as offline",
            exc_info=True,
        )


def worker_last_seen() -> datetime | None:
    """Return the worker's most recent heartbeat as a tz-aware datetime,
    or None if the worker has never written one."""
    raw = get(KEY_WORKER_HEARTBEAT, "")
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def record_worker_error(message: str) -> None:
    """Persist the last unhandled exception inside the worker poll loop.

    Used by the worker's outer try/except to capture a traceback so the
    /workers page can show it -- much faster triage than scrolling
    Railway logs. Best-effort: a failed write here just means the
    error doesn't show up on the dashboard."""
    try:
        # Cap the persisted message length so a deeply nested traceback
        # doesn't blow up the AppSetting row beyond what Postgres' default
        # text column will hold cheaply.
        capped = message[:6000]
        set(KEY_WORKER_LAST_ERROR, capped)
        set(KEY_WORKER_LAST_ERROR_AT, datetime.now(UTC).isoformat())
    except Exception:  # noqa: BLE001
        import logging
        logging.getLogger("worker").warning(
            "could not persist worker error to AppSetting",
            exc_info=True,
        )


def worker_last_error() -> tuple[str, datetime | None] | None:
    """Return (traceback, recorded_at) for the worker's last unhandled
    exception, or None if there's nothing recorded."""
    msg = get(KEY_WORKER_LAST_ERROR, "")
    if not msg:
        return None
    raw_at = get(KEY_WORKER_LAST_ERROR_AT, "")
    at: datetime | None = None
    if raw_at:
        try:
            at_dt = datetime.fromisoformat(raw_at)
            at = at_dt if at_dt.tzinfo else at_dt.replace(tzinfo=UTC)
        except ValueError:
            at = None
    return msg, at
