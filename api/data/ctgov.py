"""ClinicalTrials.gov adapter -- biotech pipeline data, no key."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import httpx

from api.data.cache import get_cached, put_cached

CT_TTL = timedelta(hours=12)
_BASE = "https://clinicaltrials.gov/api/v2"
_HEADERS = {"accept": "application/json", "user-agent": "ForteResearch/0.1"}


def search_studies(
    query: str | None = None,
    *,
    sponsor: str | None = None,
    intervention: str | None = None,
    status: str | None = None,
    phase: str | None = None,
    limit: int = 15,
) -> list[dict[str, Any]]:
    """Search clinical trials. Returns a tidy summary per study."""
    q = {"q": query, "sponsor": sponsor, "intervention": intervention,
         "status": status, "phase": phase, "limit": limit}
    cached = get_cached("ctgov", q, CT_TTL)
    if cached is not None:
        return cached["studies"]  # type: ignore[no-any-return]

    params: dict[str, Any] = {"pageSize": min(limit, 50), "format": "json"}
    if query:
        params["query.term"] = query
    if sponsor:
        params["query.lead"] = sponsor
    if intervention:
        params["query.intr"] = intervention
    if status:
        params["filter.overallStatus"] = status
    if phase:
        params["filter.phase"] = phase

    r = httpx.get(f"{_BASE}/studies", params=params, headers=_HEADERS, timeout=20.0)
    r.raise_for_status()
    rows = r.json().get("studies", []) or []
    studies = []
    for s in rows[:limit]:
        proto = s.get("protocolSection", {}) or {}
        ident = proto.get("identificationModule", {}) or {}
        status_mod = proto.get("statusModule", {}) or {}
        sponsor_mod = proto.get("sponsorCollaboratorsModule", {}) or {}
        design = proto.get("designModule", {}) or {}
        nct = ident.get("nctId")
        studies.append({
            "nct_id": nct,
            "title": ident.get("briefTitle"),
            "status": status_mod.get("overallStatus"),
            "phase": (design.get("phases") or [None])[0],
            "sponsor": sponsor_mod.get("leadSponsor", {}).get("name"),
            "start_date": (status_mod.get("startDateStruct", {}) or {}).get("date"),
            "completion_date": (status_mod.get("primaryCompletionDateStruct", {}) or {}).get("date"),
            "url": f"https://clinicaltrials.gov/study/{nct}" if nct else None,
        })
    put_cached("ctgov", q, {"studies": studies})
    return studies
