"""Runtime-editable settings: model IDs + cost caps without redeploys."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api import app_settings
from api.auth import require_auth

router = APIRouter(prefix="/settings", tags=["settings"])


class SettingsOut(BaseModel):
    model_haiku: str
    model_sonnet: str
    model_opus: str
    cost_per_report_usd: float
    cost_per_day_usd: float


class SettingsUpdate(BaseModel):
    model_haiku: str | None = None
    model_sonnet: str | None = None
    model_opus: str | None = None
    cost_per_report_usd: float | None = None
    cost_per_day_usd: float | None = None


def _current() -> SettingsOut:
    return SettingsOut(
        model_haiku=app_settings.model_haiku(),
        model_sonnet=app_settings.model_sonnet(),
        model_opus=app_settings.model_opus(),
        cost_per_report_usd=app_settings.cost_per_report_usd(),
        cost_per_day_usd=app_settings.cost_per_day_usd(),
    )


@router.get("", response_model=SettingsOut, dependencies=[Depends(require_auth)])
def get_settings() -> SettingsOut:
    return _current()


@router.put("", response_model=SettingsOut, dependencies=[Depends(require_auth)])
def update_settings(payload: SettingsUpdate) -> SettingsOut:
    if payload.model_haiku is not None:
        if not payload.model_haiku.strip():
            raise HTTPException(400, "model_haiku must not be empty")
        app_settings.set(app_settings.KEY_MODEL_HAIKU, payload.model_haiku.strip())
    if payload.model_sonnet is not None:
        if not payload.model_sonnet.strip():
            raise HTTPException(400, "model_sonnet must not be empty")
        app_settings.set(app_settings.KEY_MODEL_SONNET, payload.model_sonnet.strip())
    if payload.model_opus is not None:
        if not payload.model_opus.strip():
            raise HTTPException(400, "model_opus must not be empty")
        app_settings.set(app_settings.KEY_MODEL_OPUS, payload.model_opus.strip())
    if payload.cost_per_report_usd is not None:
        if payload.cost_per_report_usd <= 0:
            raise HTTPException(400, "cost_per_report_usd must be positive")
        app_settings.set(app_settings.KEY_COST_PER_REPORT, str(payload.cost_per_report_usd))
    if payload.cost_per_day_usd is not None:
        if payload.cost_per_day_usd <= 0:
            raise HTTPException(400, "cost_per_day_usd must be positive")
        app_settings.set(app_settings.KEY_COST_PER_DAY, str(payload.cost_per_day_usd))
    return _current()
