"""Database schema. SQLModel = Pydantic + SQLAlchemy."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ReportStage(str, Enum):
    queued = "queued"
    brief = "brief"
    research = "research"
    charts = "charts"
    draft = "draft"
    edit = "edit"
    render = "render"
    feedback = "feedback"
    done = "done"
    failed = "failed"


class Report(SQLModel, table=True):
    __tablename__ = "reports"

    id: int | None = Field(default=None, primary_key=True)
    theme: str
    subtitle: str | None = None
    stage: ReportStage = Field(default=ReportStage.queued, index=True)
    error: str | None = None
    pdf_path: str | None = None
    cost_usd: float = 0.0
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    state: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB, nullable=False, server_default="{}"))


class Job(SQLModel, table=True):
    __tablename__ = "jobs"

    id: int | None = Field(default=None, primary_key=True)
    report_id: int = Field(foreign_key="reports.id", index=True)
    stage: ReportStage
    status: str = Field(default="pending", index=True)  # pending | running | done | failed
    attempts: int = 0
    last_error: str | None = None
    created_at: datetime = Field(default_factory=_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None


class AuditLog(SQLModel, table=True):
    __tablename__ = "audit_log"

    id: int | None = Field(default=None, primary_key=True)
    report_id: int | None = Field(default=None, foreign_key="reports.id", index=True)
    actor: str  # agent slug or "system"
    event: str  # e.g. "tool_call", "model_call", "stage_complete"
    cost_usd: float = 0.0
    details: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB, nullable=False, server_default="{}"))
    created_at: datetime = Field(default_factory=_now)


class DataCache(SQLModel, table=True):
    __tablename__ = "data_cache"

    id: int | None = Field(default=None, primary_key=True)
    source: str = Field(index=True)
    query_hash: str = Field(index=True)
    fetched_at: datetime = Field(default_factory=_now)
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB, nullable=False, server_default="{}"))
