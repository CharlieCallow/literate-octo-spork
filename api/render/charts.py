"""House-style chart helpers. Returns the path of a saved PNG."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
import pandas as pd

STYLE_PATH = Path(__file__).parent / "styles" / "forte.mplstyle"
CHART_CYCLE = ["#1F1B4D", "#5BC0BE", "#302070", "#5A8DA6", "#82DDD9", "#3DA8A6"]
NAVY = "#1F1B4D"
MUTED = "#6B6B7A"


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
    return out_path


def regime_chart(
    df: pd.DataFrame,
    *,
    title: str,
    subtitle: str,
    source: str,
    as_of: str,
    out_path: Path,
    shaded: Sequence[tuple[str, str]] = (),
) -> Path:
    """Line chart with shaded regimes (e.g. recessions). `shaded` is [(start_iso, end_iso), ...]."""
    _style()
    fig, ax = plt.subplots()
    for i, col in enumerate(df.columns):
        color = CHART_CYCLE[i % len(CHART_CYCLE)]
        ax.plot(df.index, df[col], color=color, label=col)
        _label_last(ax, df[col], color)
    for start, end in shaded:
        ax.axvspan(pd.to_datetime(start), pd.to_datetime(end), color="#E8E8EE", alpha=0.6, lw=0)
    ax.set_title(title, loc="left")
    ax.text(0.0, 1.04, subtitle, transform=ax.transAxes, fontsize=11, color=MUTED, ha="left", va="bottom")
    if len(df.columns) > 4:
        ax.legend(loc="best")
    _annotate_source(ax, source, as_of)
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path)
    plt.close(fig)
    return out_path
