"""Trigger a Scout digest run from the CLI. Requires ANTHROPIC_API_KEY.

Run:  python -m scripts.scout_now
"""

from __future__ import annotations

import logging

from api.db import init_db
from api.scout_runner import run_scout


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s -- %(message)s")
    init_db()
    run = run_scout()
    print(f"ScoutRun {run.id} -- {run.n_themes} themes, ${run.cost_usd:.3f}")


if __name__ == "__main__":
    main()
