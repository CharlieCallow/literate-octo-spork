"""openFDA adapter -- drug approvals, recalls, adverse events. Free, no key."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import httpx

from api.data.cache import get_cached, put_cached

FDA_TTL = timedelta(hours=12)
_BASE = "https://api.fda.gov"
_HEADERS = {"accept": "application/json", "user-agent": "ForteResearch/0.1"}


def drug_label_search(query: str, *, limit: int = 10) -> list[dict[str, Any]]:
    """Search drug labels (label = approved indication, dosage, warnings)."""
    q = {"q": query, "limit": limit}
    cached = get_cached("openfda_labels", q, FDA_TTL)
    if cached is not None:
        return cached["rows"]  # type: ignore[no-any-return]

    r = httpx.get(
        f"{_BASE}/drug/label.json",
        params={"search": query, "limit": min(limit, 50)},
        headers=_HEADERS, timeout=15.0,
    )
    if r.status_code == 404:
        out = []
    else:
        r.raise_for_status()
        results = r.json().get("results", []) or []
        out = [{
            "id": x.get("id"),
            "brand_name": (x.get("openfda", {}).get("brand_name") or [None])[0],
            "generic_name": (x.get("openfda", {}).get("generic_name") or [None])[0],
            "manufacturer": (x.get("openfda", {}).get("manufacturer_name") or [None])[0],
            "indications": (x.get("indications_and_usage") or [None])[0],
            "approval_dates": x.get("openfda", {}).get("nda_application_number"),
        } for x in results[:limit]]
    put_cached("openfda_labels", q, {"rows": out})
    return out


def drug_recalls(query: str | None = None, *, limit: int = 10) -> list[dict[str, Any]]:
    """Recent drug recalls. `query` is optional free-text filter."""
    q = {"q": query or "*", "limit": limit}
    cached = get_cached("openfda_recalls", q, FDA_TTL)
    if cached is not None:
        return cached["rows"]  # type: ignore[no-any-return]

    params: dict[str, Any] = {"limit": min(limit, 50), "sort": "report_date:desc"}
    if query:
        params["search"] = query
    r = httpx.get(f"{_BASE}/drug/enforcement.json", params=params, headers=_HEADERS, timeout=15.0)
    if r.status_code == 404:
        out = []
    else:
        r.raise_for_status()
        results = r.json().get("results", []) or []
        out = [{
            "recall_number": x.get("recall_number"),
            "product_description": x.get("product_description"),
            "reason": x.get("reason_for_recall"),
            "classification": x.get("classification"),
            "status": x.get("status"),
            "report_date": x.get("report_date"),
            "company": x.get("recalling_firm"),
        } for x in results[:limit]]
    put_cached("openfda_recalls", q, {"rows": out})
    return out
