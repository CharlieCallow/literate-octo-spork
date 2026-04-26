"""Audit hook factory — closes over a report_id and writes events to audit_log."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlmodel import Session

from api.db import engine
from api.models import AuditLog


def audit_hook(report_id: int) -> Callable[[str, dict[str, Any]], None]:
    def _hook(event: str, details: dict[str, Any]) -> None:
        cost = float(details.get("cost_usd", 0.0))
        actor = str(details.get("agent", "system"))
        with Session(engine) as session:
            session.add(AuditLog(
                report_id=report_id,
                actor=actor,
                event=event,
                cost_usd=cost,
                details=details,
            ))
            session.commit()

    return _hook
