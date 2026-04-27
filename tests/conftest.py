"""Shared pytest fixtures + env defaults so individual tests don't have to
worry about ANTHROPIC_API_KEY etc. being set."""

from __future__ import annotations

import os

# Stub out env vars that pydantic-settings checks at import time.
os.environ.setdefault("ANTHROPIC_API_KEY", "stub-key-for-tests")
os.environ.setdefault("DASHBOARD_PASSWORD_HASH", "stub-hash")
os.environ.setdefault("SESSION_SECRET", "stub-secret")
os.environ.setdefault("FRED_API_KEY", "stub-fred-key")
