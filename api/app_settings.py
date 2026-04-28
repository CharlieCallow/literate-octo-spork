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
