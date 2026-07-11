"""One-time migration of existing CSV/JSON caches into data/oversell.db.

Safe to run repeatedly: rows are keyed by code/date and candidates by strategy/date.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.data_fetcher import AStockDataFetcher  # noqa: E402
from storage.database import init_db, save_candidate_run, upsert_price_batch, upsert_stocks  # noqa: E402


def migrate_candidates() -> int:
    total = 0
    for path in (ROOT / "data" / "candidates").glob("candidates_*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if "candidates" not in payload or "pick_date" not in payload:
                continue
            save_candidate_run(payload)
            total += 1
        except Exception as exc:  # noqa: BLE001
            print(f"skip candidate {path.name}: {exc}")
    return total


def migrate_prices(batch_size: int = 100) -> tuple[int, int]:
    raw_dir = ROOT / "data" / "raw"
    pattern = re.compile(r"^(\d{6})_(qfq|hfq|bfq)\.csv$")
    paths = [path for path in raw_dir.glob("*.csv") if pattern.match(path.name)]
    grouped: dict[str, dict[str, pd.DataFrame]] = {}
    migrated = 0
    adjusts: set[str] = set()
    for index, path in enumerate(paths, 1):
        matched = pattern.match(path.name)
        assert matched is not None
        code, adjust = matched.groups()
        try:
            normalized = AStockDataFetcher._normalize_history_dataframe(pd.read_csv(path))
            if not normalized.empty:
                grouped.setdefault(adjust, {})[code] = normalized
                adjusts.add(adjust)
        except Exception as exc:  # noqa: BLE001
            print(f"skip price {path.name}: {exc}")
        if index % batch_size == 0 or index == len(paths):
            for mode, prices in grouped.items():
                upsert_price_batch(prices, mode)
                migrated += len(prices)
            grouped.clear()
            print(f"migrated {index}/{len(paths)} files, {migrated} stock caches committed")
    return migrated, len(adjusts)


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate oversell caches into SQLite")
    parser.add_argument("--skip-prices", action="store_true")
    parser.add_argument("--skip-candidates", action="store_true")
    parser.add_argument("--batch-size", type=int, default=100)
    args = parser.parse_args()

    init_db()
    stocklist = ROOT / "data" / "stocklist.csv"
    if stocklist.exists():
        upsert_stocks(pd.read_csv(stocklist, dtype={"代码": str, "ts_code": str}))
        print("migrated stock list")
    if not args.skip_candidates:
        print(f"migrated {migrate_candidates()} candidate runs")
    if not args.skip_prices:
        stocks, adjusts = migrate_prices(max(10, args.batch_size))
        print(f"migrated {stocks} price caches across {adjusts} adjust modes")


if __name__ == "__main__":
    main()
