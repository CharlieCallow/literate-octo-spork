"""Centralised settings. Read from env / .env. Never hard-code secrets."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Forte Research"
    app_env: str = "dev"

    anthropic_api_key: str = ""

    model_haiku: str = "claude-haiku-4-5-20251001"
    model_sonnet: str = "claude-sonnet-4-6"
    model_opus: str = "claude-opus-4-7"

    cost_per_report_usd: float = 1.00
    cost_per_report_warn: float = 0.70
    cost_per_day_usd: float = 5.00
    cost_per_day_warn: float = 3.00

    dashboard_password_hash: str = ""
    session_secret: str = "change-me"

    database_url: str = f"sqlite:///{REPO_ROOT / 'forte.db'}"

    fred_api_key: str = ""

    reports_dir: Path = REPO_ROOT / "reports"

    @property
    def assets_dir(self) -> Path:
        return REPO_ROOT / "assets"

    @property
    def team_dir(self) -> Path:
        return REPO_ROOT / "team"


settings = Settings()
