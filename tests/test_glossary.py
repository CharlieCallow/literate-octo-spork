"""Regression tests for the glossary builder's ticker-resolution gate.

The bug this guards against: the glossary used to run a free-form name
lookup, so a draft mentioning TLN / LITE / COHR could come back glossed
as "Talon Metals (lithium miner) / Lam Research / Coherent Inc" -- one
of those is right, the other two are different tickers entirely. The fix:

- the equity layer's ticker -> issuer-info map is persisted on Report.tickers
- the glossary builder reads from it and is forbidden from emitting a
  glossary entry for any equity ticker that isn't in the map
- a deterministic post-parse filter enforces the rule in case the model
  ignores the prompt
- the audit stage flags any position-table ticker missing from the map
"""

from __future__ import annotations

import pytest
from sqlmodel import Session, SQLModel, create_engine

from api.models import Call, CallDirection, Report


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    import api.calls
    import api.db
    import api.ticker_resolutions
    monkeypatch.setattr(api.db, "engine", engine)
    monkeypatch.setattr(api.calls, "engine", engine)
    monkeypatch.setattr(api.ticker_resolutions, "engine", engine)
    return engine


def _seed_report_with_positions(engine) -> int:  # type: ignore[no-untyped-def]
    """Pair-trade report whose position table is TLN / LITE / COHR. The
    *real* names are Talen Energy / Lumentum / Coherent; the model's
    free-lookup hallucination is Talon Metals / Lam Research / Cohen &
    Steers (or similar). This fixture is the regression's canonical
    setup."""
    with Session(engine) as session:
        r = Report(theme="Power-grid + photonics pair trade")
        session.add(r)
        session.commit()
        report_id = r.id  # type: ignore[assignment]
        for ticker in ("TLN", "LITE", "COHR"):
            session.add(Call(
                report_id=report_id,
                contributor_slug="equity-analyst",
                asset=ticker,
                direction=CallDirection.long,
                horizon_days=90,
                conviction=4,
                claim_text=f"long {ticker}",
            ))
        session.commit()
    return report_id  # type: ignore[return-value]


_RESOLVED = {
    "TLN": {"company_name": "Talen Energy", "exchange": "NASDAQ", "sector": "Utilities"},
    "LITE": {"company_name": "Lumentum Holdings", "exchange": "NASDAQ", "sector": "Technology"},
    "COHR": {"company_name": "Coherent Corp", "exchange": "NYSE", "sector": "Technology"},
}


def test_resolve_for_report_persists_to_report_tickers(db, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Round-trip: seed a report whose calls reference TLN / LITE / COHR,
    monkeypatch the yfinance lookup, and assert Report.tickers ends up with
    the resolved issuer info -- not the hallucinated names."""
    from api import ticker_resolutions as tr

    monkeypatch.setattr(tr, "_lookup", lambda t: _RESOLVED.get(t, {}))
    report_id = _seed_report_with_positions(db)

    out = tr.resolve_for_report(report_id)

    assert out["TLN"]["company_name"] == "Talen Energy"
    assert out["LITE"]["company_name"] == "Lumentum Holdings"
    assert out["COHR"]["company_name"] == "Coherent Corp"
    with Session(db) as session:
        r = session.get(Report, report_id)
        assert r is not None
        assert r.tickers["TLN"]["company_name"] == "Talen Energy"
        assert r.tickers["LITE"]["company_name"] == "Lumentum Holdings"
        assert r.tickers["COHR"]["company_name"] == "Coherent Corp"


def test_filter_glossary_rewrites_hallucinated_issuer_names() -> None:
    """The model emits a glossary that confidently miss-names TLN / LITE /
    COHR. The deterministic filter rewrites each entry's issuer to the
    authoritative resolution -- Talen Energy, not Talon Metals; Lumentum,
    not Lam Research; Coherent, not Cohen & Steers."""
    from api.ticker_resolutions import filter_glossary_md

    hallucinated = "\n".join([
        "**TLN** — Talon Metals, Canadian lithium miner.",
        "**LITE** — Lam Research, semiconductor wafer-fab equipment maker.",
        "**COHR** — Cohen & Steers, REIT-focused asset manager.",
        "**HBM** — high-bandwidth memory, stacked DRAM used in AI accelerators.",
    ])

    out = filter_glossary_md(hallucinated, _RESOLVED)

    assert "Talon Metals" not in out
    assert "Lam Research" not in out
    assert "Cohen & Steers" not in out
    assert "Talen Energy" in out
    assert "Lumentum Holdings" in out
    assert "Coherent Corp" in out
    # Non-ticker entries pass through.
    assert "high-bandwidth memory" in out


def test_filter_glossary_drops_unresolved_position_tickers() -> None:
    """If the model invents a glossary entry for a ticker that nobody pulled
    price history for, drop it -- the analyst layer is the only authority on
    what a ticker means in this report."""
    from api.ticker_resolutions import filter_glossary_md

    glossary = "\n".join([
        "**TLN** — Talon Metals, Canadian lithium miner.",
        "**ZZZZ** — ZZZZ Corp, made-up issuer the model hallucinated.",
        "**HBM** — high-bandwidth memory, stacked DRAM.",
    ])

    out = filter_glossary_md(glossary, {"TLN": _RESOLVED["TLN"]})

    # TLN gets rewritten to Talen Energy.
    assert "Talen Energy" in out
    # ZZZZ is ticker-shaped + has issuer signal in its definition + not in
    # the resolved dict -> dropped.
    assert "ZZZZ" not in out
    # HBM passes through.
    assert "high-bandwidth memory" in out


def test_audit_position_tickers_flags_missing_resolutions(db, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """If yfinance fails for one of the position-table tickers, the audit
    stage must surface it. A silent failure is exactly how a confidently-
    wrong glossary entry shipped pre-fix."""
    from api import ticker_resolutions as tr

    partial = {"TLN": _RESOLVED["TLN"], "LITE": _RESOLVED["LITE"]}  # COHR missing
    monkeypatch.setattr(tr, "_lookup", lambda t: partial.get(t, {}))
    report_id = _seed_report_with_positions(db)
    tr.resolve_for_report(report_id)

    missing = tr.audit_position_tickers(report_id)

    assert missing == ["COHR"]


def test_glossary_prompt_includes_resolution_blob() -> None:
    """The glossary builder formats the full info dict (name + exchange +
    sector) into its prompt so the model has the authoritative metadata,
    not just a name. Sanity check that the new dict shape is wired through
    without breaking the prompt."""
    from api.agents.glossary import Glossary

    # Avoid invoking the live LLM. We just want the prompt the builder
    # would have sent.
    captured: dict[str, str] = {}

    class _StubAgent(Glossary):
        def __init__(self) -> None:  # type: ignore[no-untyped-def]
            pass  # bypass real persona / cost tracker setup

        def run(self, prompt, **kwargs):  # type: ignore[no-untyped-def, override]
            captured["prompt"] = prompt
            from api.agents.base import AgentResult
            return AgentResult(text="", cost_usd=0.0)

    _StubAgent().build(
        edited_prose="The pair trade is long TLN, long LITE, short COHR.",
        ticker_resolutions=_RESOLVED,
    )

    prompt = captured["prompt"]
    assert "Talen Energy" in prompt
    assert "Lumentum Holdings" in prompt
    assert "Coherent Corp" in prompt
    assert "FORBIDDEN" in prompt  # the new allow-list rule
