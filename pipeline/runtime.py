"""
Shared runtime for CLI and API entrypoints.

The command line runner and the web backend both call this module so data-mode
semantics stay identical in both places.
"""
from __future__ import annotations

import logging
from pathlib import Path
import threading

from pipeline.cancellation import raise_if_cancelled
from pipeline.fetch_data import run as run_fetch
from pipeline.select_stock import run as run_select
from pipeline.schemas import CandidateRun

ROOT = Path(__file__).resolve().parent.parent

logger = logging.getLogger(__name__)

DATA_MODES = ("existing", "incremental", "refresh", "cache-only")


def run_pipeline(
    data_mode: str = "incremental",
    pick_date: str | None = None,
    strategy_id: str | None = None,
    start_from: int = 1,
    no_dashboard: bool = True,
    config_path: str | None = None,
    stop_event: threading.Event | None = None,
) -> CandidateRun | None:
    """
    Run the stock-selection pipeline.

    Args:
        data_mode: existing / incremental / refresh / cache-only.
        pick_date: Optional selection date in YYYY-MM-DD format.
        start_from: 1=fetch, 2=select, 3=dashboard.
        no_dashboard: Do not start the legacy Streamlit dashboard when true.
        config_path: Optional rules_preselect.yaml path for selection.
    """
    if data_mode not in DATA_MODES:
        raise ValueError(f"unknown data_mode: {data_mode}")

    effective_start = start_from
    if data_mode == "existing" and effective_start == 1:
        effective_start = 2

    if effective_start <= 1:
        raise_if_cancelled(stop_event)
        logger.info("Step 1/3 fetch data, mode=%s", data_mode)
        run_fetch(
            use_cache_only=(data_mode == "cache-only"),
            force_refresh=(data_mode == "refresh"),
            stop_event=stop_event,
        )

    result: CandidateRun | None = None
    if effective_start <= 2:
        raise_if_cancelled(stop_event)
        logger.info("Step 2/3 run strategy preselect, strategy_id=%s", strategy_id or "config default")
        result = run_select(
            config_path=config_path,
            pick_date=pick_date,
            strategy_id=strategy_id,
            stop_event=stop_event,
        )

    if effective_start <= 3 and not no_dashboard:
        logger.info("Step 3/3 web console is served by backend/app.py and web/")

    return result
