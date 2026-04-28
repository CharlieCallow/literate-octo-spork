"""House-style chart helpers. Each one writes a PNG and a sibling .json
sidecar with the underlying data so the dashboard can render the chart
interactively (Plotly) without re-fetching."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import date as _date
from pathlib import Path
from typing import Any

import matplotlib as mpl
import matplotlib.pyplot as plt
import pandas as pd

STYLE_PATH = Path(__file__).parent / "styles" / "forte.mplstyle"
CHART_CYCLE = ["#1F1B4D", "#5BC0BE", "#302070", "#5A8DA6", "#82DDD9", "#3DA8A6"]
NAVY = "#1F1B4D"
TEAL = "#5BC0BE"
RULE = "#E8E8EE"
MUTED = "#6B6B7A"

# A small set of post-1990 NBER recession ranges. Used when a regime chart is
# called with shaded='nber'. Update if the agent needs newer ones.
_NBER_RECESSIONS: list[tuple[str, str]] = [
    ("1990-07-01", "1991-03-01"),
    ("2001-03-01", "2001-11-01"),
    ("2007-12-01", "2009-06-01"),
    ("2020-02-01", "2020-04-01"),
]


def _style() -> None:
    mpl.style.use(str(STYLE_PATH))


def _annotate_source(ax: plt.Axes, source: str, as_of: str) -> None:
    ax.annotate(
        f"Source: {source}. As of {as_of}.",
        xy=(0, -0.18),
        xycoords="axes fraction",
        fontsize=9,
        color=MUTED,
        annotation_clip=False,
    )


def _label_last(ax: plt.Axes, series: pd.Series, color: str) -> None:
    if series.empty:
        return
    x = series.index[-1]
    y = float(series.iloc[-1])
    ax.annotate(
        f"  {y:,.2f}",
        xy=(x, y),
        xytext=(4, 0),
        textcoords="offset points",
        color=color,
        fontsize=9,
        va="center",
    )


def _df_to_records(df: pd.DataFrame) -> dict[str, Any]:
    """Serialise a DataFrame into a JSON-friendly shape for the sidecar."""
    if df.empty:
        return {"index": [], "series": {}}
    idx = df.index
    if hasattr(idx, "to_pydatetime"):
        index = [d.isoformat() if hasattr(d, "isoformat") else str(d) for d in idx]
    else:
        index = [str(x) for x in idx]
    series = {col: [None if pd.isna(v) else float(v) for v in df[col].tolist()] for col in df.columns}
    return {"index": index, "series": series}


def _save_sidecar(out_path: Path, kind: str, *, title: str, subtitle: str,
                  source: str, as_of: str, data: dict[str, Any],
                  extras: dict[str, Any] | None = None) -> None:
    """Write a .json sidecar next to the PNG for interactive previews."""
    payload: dict[str, Any] = {
        "kind": kind,
        "title": title,
        "subtitle": subtitle,
        "source": source,
        "as_of": as_of,
        "palette": {"cycle": CHART_CYCLE, "navy": NAVY, "teal": TEAL, "rule": RULE, "muted": MUTED},
        **data,
    }
    if extras:
        payload.update(extras)
    json_path = out_path.with_suffix(".json")
    json_path.write_text(json.dumps(payload, default=str), encoding="utf-8")


# ----------------------------------------------------------------------
# Line + bar (existing)
# ----------------------------------------------------------------------

def line_chart(
    df: pd.DataFrame,
    *,
    title: str,
    subtitle: str,
    source: str,
    as_of: str,
    out_path: Path,
    y_label: str | None = None,
) -> Path:
    """One column per series. Index used as x-axis."""
    _style()
    fig, ax = plt.subplots()
    for i, col in enumerate(df.columns):
        color = CHART_CYCLE[i % len(CHART_CYCLE)]
        ax.plot(df.index, df[col], color=color, label=col)
        _label_last(ax, df[col], color)

    ax.set_title(title, loc="left")
    ax.text(0.0, 1.04, subtitle, transform=ax.transAxes, fontsize=11, color=MUTED, ha="left", va="bottom")
    if y_label:
        ax.set_ylabel(y_label)
    if len(df.columns) > 4:
        ax.legend(loc="best")
    _annotate_source(ax, source, as_of)
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path)
    plt.close(fig)
    _save_sidecar(out_path, "line", title=title, subtitle=subtitle,
                  source=source, as_of=as_of, data=_df_to_records(df))
    return out_path


def bar_chart(
    series: pd.Series,
    *,
    title: str,
    subtitle: str,
    source: str,
    as_of: str,
    out_path: Path,
    horizontal: bool = False,
) -> Path:
    _style()
    fig, ax = plt.subplots()
    if horizontal:
        ax.barh(series.index.astype(str), series.values, color=NAVY)
    else:
        ax.bar(series.index.astype(str), series.values, color=NAVY)
    ax.set_title(title, loc="left")
    ax.text(0.0, 1.04, subtitle, transform=ax.transAxes, fontsize=11, color=MUTED, ha="left", va="bottom")
    _annotate_source(ax, source, as_of)
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path)
    plt.close(fig)
    _save_sidecar(out_path, "bar", title=title, subtitle=subtitle,
                  source=source, as_of=as_of,
                  data={"index": [str(x) for x in series.index],
                        "series": {series.name or "value": [float(v) for v in series.values]}},
                  extras={"horizontal": horizontal})
    return out_path


# ----------------------------------------------------------------------
# Regime: line + shaded periods (NBER recessions or custom)
# ----------------------------------------------------------------------

def regime_chart(
    df: pd.DataFrame,
    *,
    title: str,
    subtitle: str,
    source: str,
    as_of: str,
    out_path: Path,
    shaded: Sequence[tuple[str, str]] | str = (),
) -> Path:
    """Line chart with shaded regimes. Pass `shaded='nber'` for the bundled
    NBER recession ranges, or a sequence of (start_iso, end_iso) tuples."""
    if isinstance(shaded, str):
        shaded = _NBER_RECESSIONS if shaded.lower() in {"nber", "nber_recessions", "recessions"} else ()

    _style()
    fig, ax = plt.subplots()
    for i, col in enumerate(df.columns):
        color = CHART_CYCLE[i % len(CHART_CYCLE)]
        ax.plot(df.index, df[col], color=color, label=col)
        _label_last(ax, df[col], color)
    for start, end in shaded:
        ax.axvspan(pd.to_datetime(start), pd.to_datetime(end), color=RULE, alpha=0.7, lw=0)
    ax.set_title(title, loc="left")
    ax.text(0.0, 1.04, subtitle, transform=ax.transAxes, fontsize=11, color=MUTED, ha="left", va="bottom")
    if len(df.columns) > 4:
        ax.legend(loc="best")
    _annotate_source(ax, source, as_of)
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path)
    plt.close(fig)
    _save_sidecar(out_path, "regime", title=title, subtitle=subtitle,
                  source=source, as_of=as_of, data=_df_to_records(df),
                  extras={"shaded": [list(rng) for rng in shaded]})
    return out_path


# ----------------------------------------------------------------------
# Comparison: two series sharing an x-axis but on different y-axes.
# ----------------------------------------------------------------------

def comparison_chart(
    df: pd.DataFrame,
    *,
    title: str,
    subtitle: str,
    source: str,
    as_of: str,
    out_path: Path,
    dual_axis: bool = True,
) -> Path:
    """Compare two series with optionally distinct y-axes (useful when the
    series are on very different scales -- e.g. a yield vs an index level)."""
    if df.shape[1] < 2:
        return line_chart(df, title=title, subtitle=subtitle, source=source,
                          as_of=as_of, out_path=out_path)

    _style()
    fig, ax_left = plt.subplots()
    cols = list(df.columns)
    left_col = cols[0]
    ax_left.plot(df.index, df[left_col], color=CHART_CYCLE[0], label=left_col)
    ax_left.set_ylabel(left_col, color=CHART_CYCLE[0])
    ax_left.tick_params(axis="y", labelcolor=CHART_CYCLE[0])
    _label_last(ax_left, df[left_col], CHART_CYCLE[0])

    if dual_axis:
        ax_right = ax_left.twinx()
        right_col = cols[1]
        ax_right.plot(df.index, df[right_col], color=CHART_CYCLE[1], label=right_col)
        ax_right.set_ylabel(right_col, color=CHART_CYCLE[1])
        ax_right.tick_params(axis="y", labelcolor=CHART_CYCLE[1])
        ax_right.spines["top"].set_visible(False)
        _label_last(ax_right, df[right_col], CHART_CYCLE[1])
    else:
        for i, col in enumerate(cols[1:], start=1):
            ax_left.plot(df.index, df[col], color=CHART_CYCLE[i % len(CHART_CYCLE)], label=col)

    ax_left.set_title(title, loc="left")
    ax_left.text(0.0, 1.04, subtitle, transform=ax_left.transAxes, fontsize=11,
                 color=MUTED, ha="left", va="bottom")
    _annotate_source(ax_left, source, as_of)
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path)
    plt.close(fig)
    _save_sidecar(out_path, "comparison", title=title, subtitle=subtitle,
                  source=source, as_of=as_of, data=_df_to_records(df),
                  extras={"dual_axis": dual_axis})
    return out_path


# ----------------------------------------------------------------------
# Event: line + vertical event markers with labels.
# ----------------------------------------------------------------------

def event_chart(
    df: pd.DataFrame,
    events: Sequence[dict[str, str]],
    *,
    title: str,
    subtitle: str,
    source: str,
    as_of: str,
    out_path: Path,
) -> Path:
    """Line chart with vertical lines + labels at named events.
    Each event: {'date': 'YYYY-MM-DD', 'label': 'short label'}."""
    _style()
    fig, ax = plt.subplots()
    for i, col in enumerate(df.columns):
        color = CHART_CYCLE[i % len(CHART_CYCLE)]
        ax.plot(df.index, df[col], color=color, label=col)
        _label_last(ax, df[col], color)

    if len(df.columns):
        ymin, ymax = ax.get_ylim()
        for ev in events:
            try:
                x = pd.to_datetime(ev["date"])
            except (KeyError, ValueError):
                continue
            ax.axvline(x, color=TEAL, linewidth=1, linestyle="--", alpha=0.7)
            ax.annotate(
                ev.get("label", ""),
                xy=(x, ymax),
                xytext=(2, -10),
                textcoords="offset points",
                fontsize=9,
                color=NAVY,
                rotation=0,
                ha="left",
                va="top",
            )

    ax.set_title(title, loc="left")
    ax.text(0.0, 1.04, subtitle, transform=ax.transAxes, fontsize=11, color=MUTED, ha="left", va="bottom")
    if len(df.columns) > 4:
        ax.legend(loc="best")
    _annotate_source(ax, source, as_of)
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path)
    plt.close(fig)
    _save_sidecar(out_path, "event", title=title, subtitle=subtitle,
                  source=source, as_of=as_of, data=_df_to_records(df),
                  extras={"events": [dict(e) for e in events]})
    return out_path


def today_iso() -> str:
    return _date.today().isoformat()
