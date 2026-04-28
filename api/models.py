"""Database schema. SQLModel = Pydantic + SQLAlchemy."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


def _now() -> datetime:
    return datetime.now(UTC)


class ReportStage(str, Enum):
    queued = "queued"
    brief = "brief"
    recruit = "recruit"  # M4: spin up temp specialists if the brief flagged any
    research = "research"
    charts = "charts"
    draft = "draft"
    redteam = "redteam"  # bear / devil's-advocate pass on the drafts
    edit = "edit"
    audit = "audit"      # ground every number in the edited prose against tool outputs
    render = "render"
    feedback = "feedback"
    housekeeping = "housekeeping"  # house view update, theme tags, calls, voice stats
    done = "done"
    failed = "failed"
    cancelled = "cancelled"


class ReportMode(str, Enum):
    fast = "fast"          # Haiku-only, no web search, tight iters -- testing
    standard = "standard"  # Opus EIC + Sonnet others, web search on -- default
    deep = "deep"          # Same models as standard, larger iter/token budget -- deep dive


class Report(SQLModel, table=True):
    __tablename__ = "reports"

    id: int | None = Field(default=None, primary_key=True)
    theme: str
    subtitle: str | None = None
    mode: ReportMode = Field(default=ReportMode.standard)
    budget_cap_usd: float | None = None  # overrides settings.cost_per_report_usd if set
    team_override: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    contributor_slugs: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    # Theme graph: tickers + themes mentioned in the published report.
    # Populated in the housekeeping stage; read by the Scout to weight
    # natural follow-ups in the daily digest.
    mentioned_tickers: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    mentioned_themes: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    stage: ReportStage = Field(default=ReportStage.queued, index=True)
    error: str | None = None
    pdf_path: str | None = None
    cost_usd: float = 0.0
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    state: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    # Public share link. Null = not shared. The token is a URL-safe random
    # string; the shared_at timestamp tracks when the link was minted (for UI
    # only -- it's not used for expiry).
    share_token: str | None = Field(default=None, index=True)
    shared_at: datetime | None = None


class Job(SQLModel, table=True):
    __tablename__ = "jobs"

    id: int | None = Field(default=None, primary_key=True)
    report_id: int = Field(foreign_key="reports.id", index=True)
    stage: ReportStage
    status: str = Field(default="pending", index=True)  # pending | running | done | failed
    attempts: int = 0
    cost_usd: float = 0.0
    last_error: str | None = None
    created_at: datetime = Field(default_factory=_now)
    run_after: datetime | None = Field(default=None, index=True)  # don't pick up before this time
    started_at: datetime | None = None
    finished_at: datetime | None = None


class AuditLog(SQLModel, table=True):
    __tablename__ = "audit_log"

    id: int | None = Field(default=None, primary_key=True)
    report_id: int | None = Field(default=None, foreign_key="reports.id", index=True)
    actor: str  # agent slug or "system"
    event: str  # e.g. "tool_call", "model_call", "stage_complete"
    cost_usd: float = 0.0
    details: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=_now)


class DataCache(SQLModel, table=True):
    __tablename__ = "data_cache"

    id: int | None = Field(default=None, primary_key=True)
    source: str = Field(index=True)
    query_hash: str = Field(index=True)
    fetched_at: datetime = Field(default_factory=_now)
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))


class ScoutRun(SQLModel, table=True):
    __tablename__ = "scout_runs"

    id: int | None = Field(default=None, primary_key=True)
    started_at: datetime = Field(default_factory=_now, index=True)
    finished_at: datetime | None = None
    n_themes: int = 0
    cost_usd: float = 0.0
    error: str | None = None


class Theme(SQLModel, table=True):
    __tablename__ = "themes"

    id: int | None = Field(default=None, primary_key=True)
    scout_run_id: int = Field(foreign_key="scout_runs.id", index=True)
    headline: str
    why_now: str = ""
    dig_into: str = ""
    source_urls: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    score: float = 0.0
    surfaced_at: datetime = Field(default_factory=_now, index=True)
    commissioned_report_id: int | None = Field(default=None, foreign_key="reports.id")


class RecommendationKind(str, Enum):
    promote = "promote"  # move team/temp/<slug>.md -> team/<slug>.md
    fire = "fire"        # move team/<slug>.md -> team/archive/<slug>.md


class RecommendationStatus(str, Enum):
    pending = "pending"
    approved = "approved"
    dismissed = "dismissed"


class Recommendation(SQLModel, table=True):
    __tablename__ = "recommendations"

    id: int | None = Field(default=None, primary_key=True)
    kind: RecommendationKind = Field(index=True)
    subject_slug: str = Field(index=True)
    subject_name: str
    subject_role: str
    reasoning: str
    status: RecommendationStatus = Field(default=RecommendationStatus.pending, index=True)
    created_at: datetime = Field(default_factory=_now)
    resolved_at: datetime | None = None


class PersonaStatus(str, Enum):
    standing = "standing"  # appears in /team roster, available to be assigned
    temp = "temp"          # ad-hoc specialist for one report (lives in team/temp/)
    archived = "archived"  # fired; rehirable from /team archive (lives in team/archive/)


class AppSetting(SQLModel, table=True):
    """Free-form key/value store for runtime-editable settings (model IDs,
    cost caps, etc.). Falls back to env-var values when a key is missing."""

    __tablename__ = "app_settings"

    key: str = Field(primary_key=True)
    value: str
    updated_at: datetime = Field(default_factory=_now)


class UploadedDocument(SQLModel, table=True):
    """User-supplied research notes / CSVs the agents can read as a tool result.
    `extracted_text` is the plaintext rendering used by the agent tool; the
    original file lives on disk under reports/<report_id>/uploads/."""

    __tablename__ = "uploaded_documents"

    id: int | None = Field(default=None, primary_key=True)
    report_id: int = Field(foreign_key="reports.id", index=True)
    filename: str
    mime: str
    size_bytes: int = 0
    summary: str = ""           # short head-of-doc snippet for the listing tool
    extracted_text: str = ""    # full extracted plaintext (chunked on read)
    created_at: datetime = Field(default_factory=_now)


class HouseView(SQLModel, table=True):
    """Single-row rolling firm stance. The brief stage loads it into the EIC's
    prompt; the housekeeping stage rewrites it after every report. id=1 always."""

    __tablename__ = "house_view"

    id: int = Field(default=1, primary_key=True)
    markdown: str = ""
    updated_at: datetime = Field(default_factory=_now)
    last_report_id: int | None = Field(default=None, foreign_key="reports.id")


class CallDirection(str, Enum):
    long = "long"
    short = "short"
    fade = "fade"
    avoid = "avoid"


class CallOutcome(str, Enum):
    hit = "hit"
    miss = "miss"
    partial = "partial"


class Call(SQLModel, table=True):
    """A structured forecast extracted from a published report. Weekly cron
    grades unresolved calls past their horizon against current prices."""

    __tablename__ = "calls"

    id: int | None = Field(default=None, primary_key=True)
    report_id: int = Field(foreign_key="reports.id", index=True)
    contributor_slug: str = Field(index=True)
    asset: str  # yfinance ticker, e.g. 'SPY', 'CCJ', 'BTC-USD'
    direction: CallDirection
    horizon_days: int = 90
    target_level: float | None = None
    conviction: int = 3  # 1-5 from the analyst's {cN} tag
    claim_text: str = ""  # short prose snippet from the report
    made_at: datetime = Field(default_factory=_now, index=True)
    price_at_call: float | None = None
    evaluated_at: datetime | None = None
    price_at_evaluation: float | None = None
    outcome: CallOutcome | None = None


class PersonaVoiceStat(SQLModel, table=True):
    """One row per (persona, report). Trailing windows of these power the
    drift-detection alert: voice flattening towards the firm mean is the
    failure mode the spec calls out."""

    __tablename__ = "persona_voice_stats"

    id: int | None = Field(default=None, primary_key=True)
    report_id: int = Field(foreign_key="reports.id", index=True)
    persona_slug: str = Field(index=True)
    n_words: int = 0
    mean_sentence_words: float = 0.0
    hedge_ratio: float = 0.0           # hedge words / sentences
    emdash_per_1k: float = 0.0          # em-dashes per 1000 words
    conviction_density: float = 0.0     # {c4}/{c5} tags per 100 sentences
    created_at: datetime = Field(default_factory=_now, index=True)


class Persona(SQLModel, table=True):
    """Source of truth for persona files. The filesystem is a write-through
    cache rehydrated from these rows at startup so Railway rebuilds don't
    lose promotions / firings / manual edits."""

    __tablename__ = "personas"

    slug: str = Field(primary_key=True)
    status: PersonaStatus = Field(default=PersonaStatus.standing, index=True)
    markdown: str
    name: str = ""
    role: str = ""
    is_orchestrator: bool = False  # editor-in-chief, scout, recruiter, data-and-charts
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
