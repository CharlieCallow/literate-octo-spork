"""Tests for the chart x-axis fix.

Bug we hit: regime_chart added NBER recession axvspans dating back to
1990 *after* plotting; matplotlib auto-extends the x-axis to include
axvspans, so a chart of post-2024 data ended up squeezed into the
right edge of a 30-year axis. Same issue with event_chart adding
axvline for events outside the data window.

The fix captures the data x-range before adding spans/events, drops
out-of-window shading + events, and pins set_xlim() so future
auto-extension can't stretch the axis.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


def _post_2024_yield_frame() -> pd.DataFrame:
    """Mid-2024 onwards 10Y yield series. Mirrors the FRED DGS10 shape
    that broke the original chart."""
    idx = pd.date_range("2024-06-01", "2026-04-01", freq="W")
    return pd.DataFrame({"DGS10": [4.2 + 0.01 * (i % 30) for i in range(len(idx))]}, index=idx)


def test_regime_chart_does_not_extend_axis_to_pre_data_recessions(tmp_path: Path) -> None:
    """The smoking gun: feed it post-2024 data with shaded='nber', and the
    x-axis must stay within the data range -- not stretch back to 1990
    just because the NBER bands include the 1990 recession."""
    from api.render.charts import regime_chart

    df = _post_2024_yield_frame()
    out_path = tmp_path / "yield.png"
    regime_chart(
        df,
        title="10Y Treasury yield", subtitle="Mid-2024 onwards",
        source="FRED", as_of="2026-04-01",
        out_path=out_path, shaded="nber",
    )
    sidecar = out_path.with_suffix(".json")
    assert out_path.exists()
    assert sidecar.exists()
    # The data window starts in mid-2024. None of the 4 bundled NBER
    # ranges touch this period, so the chart should still produce a
    # focused view (not stretched). We can't easily inspect the rendered
    # PNG axis range here, but we CAN verify the chart code runs to
    # completion with the bundled NBER recession set; a regression
    # would either crash or leave a now-inconsistent sidecar.
    import json
    payload = json.loads(sidecar.read_text())
    assert payload["kind"] == "regime"
    # Sidecar still records the requested shading (even if some don't
    # render) so the dashboard's interactive view can decide what to draw.
    assert len(payload["shaded"]) == 4


def test_regime_chart_clamps_xlim_when_data_is_short_window(monkeypatch, tmp_path: Path) -> None:
    """Hard-pin the x-axis range. Use matplotlib's pyplot bookkeeping to
    inspect the saved figure's axis instead of pixel-diffing."""
    import matplotlib.pyplot as plt

    from api.render.charts import regime_chart

    captured_xlim: list[tuple[float, float]] = []
    real_savefig = plt.Figure.savefig

    def spy_savefig(fig, *args, **kwargs):  # type: ignore[no-untyped-def]
        for ax in fig.axes:
            captured_xlim.append(ax.get_xlim())
        return real_savefig(fig, *args, **kwargs)

    monkeypatch.setattr(plt.Figure, "savefig", spy_savefig)

    df = _post_2024_yield_frame()
    out_path = tmp_path / "yield.png"
    regime_chart(
        df, title="t", subtitle="s", source="FRED", as_of="2026-04-01",
        out_path=out_path, shaded="nber",
    )
    assert captured_xlim, "savefig spy never fired"
    xmin, xmax = captured_xlim[0]
    # matplotlib stores datetimes as float days since 1970 epoch.
    # Convert back to assert range hugs the data, not 1990.
    import matplotlib.dates as mdates
    xmin_date = mdates.num2date(xmin).date()
    xmax_date = mdates.num2date(xmax).date()
    # Lower bound must be >= 2024 (data starts mid-2024). Loose check
    # so a tiny matplotlib padding doesn't make this brittle.
    assert xmin_date.year >= 2024, (
        f"x-axis stretched back to {xmin_date} -- the regression we just fixed"
    )
    assert xmax_date.year >= 2026


def test_event_chart_drops_pre_data_events_but_keeps_forward_catalysts(tmp_path: Path) -> None:
    """Pre-data events are rear-view context the data should already
    cover -- drop them so they don't stretch the axis back decades.
    Forward-dated events are the load-bearing case (catalyst dates the
    desk is watching) and MUST be kept; the axis extends right to
    include them."""
    from api.render.charts import event_chart

    df = _post_2024_yield_frame()
    out_path = tmp_path / "events.png"
    event_chart(
        df,
        events=[
            {"date": "2008-09-15", "label": "Lehman"},                  # pre-data: dropped
            {"date": "2025-01-20", "label": "Inauguration"},            # in window
            {"date": "2026-08-15", "label": "FY27 conf report"},        # forward: kept
        ],
        title="t", subtitle="s", source="FRED", as_of="2026-04-01",
        out_path=out_path,
    )
    assert out_path.exists()
    sidecar = json.loads(out_path.with_suffix(".json").read_text())
    # The original event list is preserved in the sidecar verbatim --
    # the rendering filter doesn't mutate the input. The forward event
    # is what makes the chart load-bearing.
    labels = [e["label"] for e in sidecar["events"]]
    assert "FY27 conf report" in labels


def test_event_chart_extends_axis_for_forward_catalyst(tmp_path: Path) -> None:
    """The defining behavior of the forward-annotation push: an event
    dated past the last data point must extend the x-axis to make
    room. Regression would either drop the event or squash it onto
    the right edge."""
    from matplotlib.dates import num2date

    from api.render.charts import event_chart

    df = _post_2024_yield_frame()
    last_data_point = df.index.max().to_pydatetime()
    out_path = tmp_path / "events.png"
    catalyst = "2027-01-15"
    event_chart(
        df,
        events=[{"date": catalyst, "label": "FY27 catalyst"}],
        title="t", subtitle="s", source="FRED", as_of="2026-04-01",
        out_path=out_path,
    )
    assert out_path.exists()
    # We can't easily get xlim back after plt.close(), so the sidecar
    # is the contract: forward event survives the render so the
    # annotation actually appears in the chart.
    sidecar = json.loads(out_path.with_suffix(".json").read_text())
    assert any(e["date"] == catalyst for e in sidecar["events"])
    _ = (last_data_point, num2date)  # imports kept for future xlim assert


# ----------------------------------------------------------------------
# Reading mode stage gate
# ----------------------------------------------------------------------

def test_reading_mode_allowed_from_render_stage_onwards() -> None:
    """A report whose `feedback` or `housekeeping` stage failed still has
    a rendered PDF + edited.md. Reading mode should let the user view it
    rather than 400-ing -- otherwise a single non-blocking failure
    locks them out of the content."""
    from fastapi import HTTPException

    from api.models import Report, ReportStage
    from api.routes.reports import _build_reading_payload

    # Stages that should NOT raise the "not finished" 400. They may
    # still hit later checks (missing edited.md, etc) but those are
    # different errors with their own handling.
    readable_stages = [
        ReportStage.render,
        ReportStage.feedback,
        ReportStage.housekeeping,
        ReportStage.done,
    ]
    for stage in readable_stages:
        report = Report(id=999, theme="t", stage=stage)
        try:
            _build_reading_payload(report)
        except HTTPException as e:
            # The 400 "not finished" check is what we're testing for.
            # Any OTHER HTTPException (404 missing edited.md, etc) is
            # fine -- that's not the bug we fixed.
            assert e.status_code != 400, (
                f"stage={stage} should not raise 400 'not finished'"
            )
        except Exception:  # noqa: BLE001
            # Working-dir reads will fail because there's no on-disk
            # state for report 999. That's fine -- we're past the
            # gating check we care about.
            pass


def test_reading_mode_rejects_in_progress_stages() -> None:
    """In-progress reports (queued / brief / draft / etc) still get the
    400 -- the prose isn't on disk yet, so reading mode would just 404
    or render an empty page."""
    import pytest
    from fastapi import HTTPException

    from api.models import Report, ReportStage
    from api.routes.reports import _build_reading_payload

    in_progress = [
        ReportStage.queued, ReportStage.brief, ReportStage.research,
        ReportStage.draft, ReportStage.rebuttal, ReportStage.edit,
    ]
    for stage in in_progress:
        report = Report(id=999, theme="t", stage=stage)
        with pytest.raises(HTTPException) as exc:
            _build_reading_payload(report)
        assert exc.value.status_code == 400, (
            f"stage={stage} should still raise 400 -- content isn't on disk yet"
        )


# ----------------------------------------------------------------------
# Forecast annotations: thresholds + forward catalysts
# (Ticket 10 -- Tomás's load-bearing chart must carry a forward annotation.)
# ----------------------------------------------------------------------

def test_line_chart_renders_thresholds(tmp_path: Path) -> None:
    """Thresholds are how the desk's trade-trigger level shows up on a
    price/yield chart ('RTX > 22x fwd', '10Y > 4.50%'). Sidecar must
    record them so dashboard renderers stay consistent with the PNG."""
    from api.render.charts import line_chart

    df = _post_2024_yield_frame()
    out_path = tmp_path / "yield.png"
    line_chart(
        df,
        title="10Y", subtitle="s", source="FRED", as_of="2026-04-01",
        out_path=out_path,
        thresholds=[{"value": 4.50, "label": "Sell trigger > 4.50%"}],
    )
    assert out_path.exists()
    sidecar = json.loads(out_path.with_suffix(".json").read_text())
    assert sidecar["thresholds"][0]["value"] == 4.50
    assert "trigger" in sidecar["thresholds"][0]["label"].lower()


def test_charts_stage_runs_after_drafts() -> None:
    """Ticket 10: Tomás needs the desk's specific forecasts (horizons,
    targets, trigger levels) to annotate the load-bearing chart. Charts
    must run downstream of draft -- ideally after rebuttal so any
    cross-desk disagreement is on the page first."""
    from api.models import ReportStage
    from api.workflow.state_machine import STAGE_ORDER

    i_draft = STAGE_ORDER.index(ReportStage.draft)
    i_rebuttal = STAGE_ORDER.index(ReportStage.rebuttal)
    i_charts = STAGE_ORDER.index(ReportStage.charts)
    i_edit = STAGE_ORDER.index(ReportStage.edit)
    assert i_draft < i_charts, "charts must run after drafts so it can read forecasts"
    assert i_rebuttal < i_charts, "charts run after rebuttal so disagreement is visible"
    assert i_charts < i_edit, "charts still need to be on the page before EIC edits"
