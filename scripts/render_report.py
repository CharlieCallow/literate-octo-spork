"""Re-render a report PDF from its editable `draft.md`.

Usage:
    python -m scripts.render_report <report_dir> [--out <pdf_path>]

Reads `<report_dir>/draft.md`, expands `[chart: name.png]` references
against `<report_dir>/charts/`, and writes `<report_dir>/report.pdf`
(or `--out` if specified). No LLM calls. No network (assuming charts
already exist on disk). Target: under 30 seconds.

This is the "fix a typo, get a corrected PDF" path: edit draft.md in
your text editor, run this command, ship the resulting PDF."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from api.render.draft import render_from_dir


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("report_dir", type=Path, help="report working directory (contains draft.md)")
    p.add_argument("--out", type=Path, default=None, help="output PDF path (default: <dir>/report.pdf)")
    args = p.parse_args(argv)

    report_dir: Path = args.report_dir
    if not report_dir.is_dir():
        print(f"error: {report_dir} is not a directory", file=sys.stderr)
        return 2
    if not (report_dir / "draft.md").exists():
        print(f"error: {report_dir}/draft.md not found", file=sys.stderr)
        return 2

    t0 = time.monotonic()
    out = render_from_dir(report_dir, args.out)
    dt = time.monotonic() - t0
    print(f"rendered {out} in {dt:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
