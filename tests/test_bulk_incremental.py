from __future__ import annotations

import tempfile
import unittest
import logging
from pathlib import Path

import pandas as pd

import storage.database as database
from data.data_fetcher import AStockDataFetcher


class _FakeTushare:
    def trade_cal(self, **_: object) -> pd.DataFrame:
        return pd.DataFrame([{"cal_date": "20260710", "is_open": 1}])

    def daily(self, **_: object) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "trade_date": "20260710",
                    "open": 5.5,
                    "high": 6.2,
                    "low": 5.4,
                    "close": 6.0,
                    "change": 0.5,
                    "pct_chg": 9.09,
                    "vol": 1000.0,
                    "amount": 6000.0,
                }
            ]
        )

    def adj_factor(self, trade_date: str, **_: object) -> pd.DataFrame:
        factor = 1.0 if trade_date == "20260709" else 2.0
        return pd.DataFrame([{"ts_code": "000001.SZ", "trade_date": trade_date, "adj_factor": factor}])


class _TestFetcher(AStockDataFetcher):
    def __init__(self) -> None:
        super().__init__(max_retries=1, second_pass_enabled=False)
        self.fake = _FakeTushare()

    def _ensure_client(self) -> _FakeTushare:
        return self.fake

    def _call_tushare(self, func, *args, **kwargs):
        return func(*args, **kwargs)


class BulkIncrementalTests(unittest.TestCase):
    def test_batch_progress_log_can_write_pipeline_status(self) -> None:
        original_path = database.DB_PATH
        with tempfile.TemporaryDirectory() as tmp:
            database.DB_PATH = Path(tmp) / "test.db"

            class _StatusWriter(logging.Handler):
                def emit(self, record: logging.LogRecord) -> None:
                    database.upsert_pipeline_run(
                        {
                            "run_id": "lock-regression",
                            "status": "running",
                            "stage": record.getMessage(),
                            "logs": [record.getMessage()],
                        }
                    )

            handler = _StatusWriter()
            original_level = database.logger.level
            database.logger.addHandler(handler)
            database.logger.setLevel(logging.INFO)
            try:
                frame = pd.DataFrame(
                    [{"open": 1, "high": 2, "low": 1, "close": 2, "volume": 10, "amount": 20}],
                    index=pd.to_datetime(["2026-07-10"]),
                )
                database.upsert_price_batch({f"{index:06d}": frame for index in range(1, 252)}, "qfq")
                stored = database.get_pipeline_run("lock-regression")

                self.assertIsNotNone(stored)
                self.assertIn("251/251", str(stored["stage"]))
            finally:
                database.logger.removeHandler(handler)
                database.logger.setLevel(original_level)
                database.DB_PATH = original_path

    def test_market_turnover_coefficient_uses_full_market_amount(self) -> None:
        original_path = database.DB_PATH
        with tempfile.TemporaryDirectory() as tmp:
            database.DB_PATH = Path(tmp) / "test.db"
            try:
                first = pd.DataFrame(
                    [{"open": 1, "high": 1, "low": 1, "close": 1, "volume": 1, "amount": 900_000_000}],
                    index=pd.to_datetime(["2026-07-10"]),
                )
                second = first.copy()
                second["amount"] = 700_000_000
                database.upsert_price_batch({"000001": first, "000002": second}, "qfq")

                snapshot = database.market_turnover_snapshot("qfq", "2026-07-10")

                self.assertEqual(snapshot["amount_trillion"], 1.6)
                self.assertEqual(snapshot["coefficient"], 1.2)
            finally:
                database.DB_PATH = original_path

    def test_qfq_history_is_rebased_when_factor_changes(self) -> None:
        original_path = database.DB_PATH
        with tempfile.TemporaryDirectory() as tmp:
            database.DB_PATH = Path(tmp) / "test.db"
            try:
                old = pd.DataFrame(
                    [{"open": 9.0, "high": 11.0, "low": 8.0, "close": 10.0, "volume": 900.0, "amount": 9000.0}],
                    index=pd.to_datetime(["2026-07-09"]),
                )
                database.upsert_daily_prices("000001", "qfq", old)

                missing = _TestFetcher().bulk_incremental_update(
                    ["000001"], start_date="20260701", end_date="20260710", adjust="qfq"
                )
                loaded = database.load_daily_prices("qfq", 1, ["000001"])["000001"]

                self.assertEqual(missing, [])
                self.assertAlmostEqual(float(loaded.loc[pd.Timestamp("2026-07-09"), "close"]), 5.0)
                self.assertAlmostEqual(float(loaded.loc[pd.Timestamp("2026-07-10"), "close"]), 6.0)
            finally:
                database.DB_PATH = original_path


if __name__ == "__main__":
    unittest.main()
