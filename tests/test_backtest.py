from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import storage.database as database
import backend.app as backend_app
from backtest.schemas import BacktestRequest, BacktestResult
from backtest.service import run_backtest
from fastapi.testclient import TestClient
from pipeline.cancellation import RunCancelledError
from pipeline.schemas import Candidate
from pipeline.Selector import compute_weekly_ma
from strategies.base import StrategyContext, StrategyMeta


class _AlwaysSelectStrategy:
    meta = StrategyMeta(id="fake", name="测试策略", description="", default_config={})

    def warmup_bars(self, _: dict) -> int:
        return 1

    def prepare_all(self, data: dict[str, pd.DataFrame], _: dict, __: StrategyContext | None = None):
        return data

    def select_prepared(self, data: dict[str, pd.DataFrame], _: dict, context: StrategyContext):
        result: list[Candidate] = []
        for code, frame in data.items():
            if context.pick_date not in frame.index:
                continue
            row = frame.loc[context.pick_date]
            result.append(
                Candidate(
                    code=code,
                    name=f"股票{code[-1]}",
                    date=str(context.pick_date.date()),
                    strategy="fake",
                    close=float(row["close"]),
                    turnover_n=float(row["turnover_n"]),
                    score=float(code[-1]),
                )
            )
        return result


def _frame(values: list[tuple[str, float, float, float, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "open": open_, "high": max(open_, close) + 0.5, "low": min(open_, close) - 0.5,
                "close": close, "volume": 1000, "amount": amount,
            }
            for _, open_, close, amount, _ in values
        ],
        index=pd.to_datetime([date for date, *_ in values]),
    )


class BacktestServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_path = database.DB_PATH
        self.tmp = tempfile.TemporaryDirectory()
        database.DB_PATH = Path(self.tmp.name) / "backtest.db"
        dates = ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09"]
        first = _frame([
            (dates[0], 9, 10, 100, 0),
            (dates[1], 10, 11, 100, 0),
            (dates[2], 11, 12, 100, 0),
            (dates[3], 12, 9, 100, 0),
            (dates[4], 9, 10, 100, 0),
        ])
        second = _frame([
            (dates[0], 20, 20, 200, 0),
            (dates[1], 20, 19, 200, 0),
            (dates[2], 18, 18, 200, 0),
            (dates[3], 18, 21, 200, 0),
            (dates[4], 21, 20, 200, 0),
        ])
        database.upsert_price_batch({"000001": first, "000002": second}, "qfq")

    def tearDown(self) -> None:
        database.DB_PATH = self.original_path
        self.tmp.cleanup()

    def test_next_open_returns_and_ranking_are_correct(self) -> None:
        request = BacktestRequest(
            strategy_id="fake",
            start_date="2026-01-05",
            end_date="2026-01-06",
            holding_days=2,
            config={
                "global": {"adjust": "qfq", "top_m": 0, "n_turnover_days": 2, "markets": ["main"]},
                "strategies": {"fake": {}},
            },
        )
        with patch("backtest.service.get_strategy", return_value=_AlwaysSelectStrategy()):
            result = run_backtest("test-run", request)

        self.assertEqual(result.metrics["signal_day_count"], 2)
        self.assertEqual(result.metrics["signal_count"], 4)
        self.assertEqual(result.metrics["completed_count"], 4)
        self.assertEqual(result.metrics["win_rate_pct"], 50.0)

        trade = next(item for item in result.trades if item.code == "000001" and item.signal_date == "2026-01-05")
        self.assertEqual(trade.entry_date, "2026-01-06")
        self.assertEqual(trade.entry_open, 10.0)
        self.assertEqual(trade.exit_date, "2026-01-07")
        self.assertEqual(trade.final_return_pct, 20.0)
        self.assertEqual([item["return_pct"] for item in trade.daily_returns], [10.0, 20.0])
        self.assertEqual(result.trades[0].final_return_pct, 20.0)

    def test_result_round_trips_through_sqlite(self) -> None:
        database.upsert_backtest_run(
            {
                "backtest_id": "persisted", "strategy_id": "fake", "start_date": "2026-01-05",
                "end_date": "2026-01-06", "holding_days": 2, "status": "running", "stage": "回测中",
            }
        )
        request = BacktestRequest(
            strategy_id="fake", start_date="2026-01-05", end_date="2026-01-05", holding_days=2,
            config={"global": {"adjust": "qfq", "top_m": 0, "markets": ["main"]}, "strategies": {"fake": {}}},
        )
        with patch("backtest.service.get_strategy", return_value=_AlwaysSelectStrategy()):
            payload = run_backtest("persisted", request).to_dict()
        database.save_backtest_result("persisted", payload)

        loaded = database.load_backtest_result("persisted")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["metrics"]["signal_count"], 2)
        self.assertEqual(len(loaded["trades"]), 2)
        self.assertEqual(loaded["trades"][0]["daily_returns"][1]["day"], 2)

    def test_weekly_indicator_does_not_change_when_future_prices_change(self) -> None:
        dates = pd.bdate_range("2026-01-05", periods=40)
        base = pd.DataFrame({"close": range(1, 41)}, index=dates, dtype=float)
        changed = base.copy()
        cutoff = dates[24]
        changed.loc[changed.index > cutoff, "close"] = 9999

        first = compute_weekly_ma(base, 2, 3, 4)
        second = compute_weekly_ma(changed, 2, 3, 4)

        self.assertEqual(float(first.loc[cutoff, "wma_short"]), float(second.loc[cutoff, "wma_short"]))
        self.assertEqual(float(first.loc[cutoff, "wma_long"]), float(second.loc[cutoff, "wma_long"]))

    def test_backtest_api_runs_in_background_and_returns_result(self) -> None:
        original = backend_app.run_backtest

        def fake_run(backtest_id, request, stop_event=None, progress=None):
            if progress:
                progress("逐日选股", "逐日回测 1/1：测试完成", 1, 1)
            return BacktestResult(
                backtest_id=backtest_id,
                generated_at="2026-01-09T12:00:00",
                request={"strategy_id": request.strategy_id},
                metrics={"signal_count": 0, "completed_count": 0},
            )

        backend_app.run_backtest = fake_run
        backend_app._backtest_runs.clear()
        backend_app._backtest_cancel_events.clear()
        client = TestClient(backend_app.app)
        try:
            response = client.post(
                "/api/backtests",
                json={
                    "strategy_id": "b1",
                    "start_date": "2026-01-05",
                    "end_date": "2026-01-05",
                    "holding_days": 2,
                    "config": {
                        "data_mode": "existing", "fetch": {}, "active_strategy": "b1",
                        "global": {"adjust": "qfq", "top_m": 0, "markets": ["main"]},
                        "strategies": {"b1": {}},
                    },
                },
            )
            self.assertEqual(response.status_code, 200)
            backtest_id = response.json()["backtest_id"]
            for _ in range(100):
                status = client.get(f"/api/backtests/{backtest_id}").json()
                if status["status"] == "success":
                    break
                time.sleep(0.01)

            self.assertEqual(status["status"], "success")
            self.assertEqual(status["progress"], 100.0)
            payload = client.get(f"/api/backtests/{backtest_id}/result")
            self.assertEqual(payload.status_code, 200)
            self.assertEqual(payload.json()["metrics"]["signal_count"], 0)
        finally:
            backend_app.run_backtest = original
            backend_app._backtest_runs.clear()
            backend_app._backtest_cancel_events.clear()

    def test_backtest_api_can_cancel_a_running_job(self) -> None:
        original = backend_app.run_backtest

        def fake_run(backtest_id, request, stop_event=None, progress=None):
            if progress:
                progress("逐日选股", "逐日回测 1/20：等待终止", 1, 20)
            while stop_event is not None and not stop_event.wait(0.01):
                pass
            raise RunCancelledError("任务已被用户终止")

        backend_app.run_backtest = fake_run
        backend_app._backtest_runs.clear()
        backend_app._backtest_cancel_events.clear()
        client = TestClient(backend_app.app)
        try:
            created = client.post(
                "/api/backtests",
                json={
                    "strategy_id": "b1", "start_date": "2026-01-05", "end_date": "2026-01-06",
                    "holding_days": 2,
                    "config": {
                        "data_mode": "existing", "fetch": {}, "active_strategy": "b1",
                        "global": {"adjust": "qfq", "top_m": 0, "markets": ["main"]},
                        "strategies": {"b1": {}},
                    },
                },
            )
            backtest_id = created.json()["backtest_id"]
            cancelled = client.post(f"/api/backtests/{backtest_id}/cancel")
            self.assertEqual(cancelled.status_code, 200)
            for _ in range(100):
                payload = client.get(f"/api/backtests/{backtest_id}").json()
                if payload["status"] == "cancelled":
                    break
                time.sleep(0.01)
            self.assertEqual(payload["status"], "cancelled")
            self.assertIn("回测已由用户终止", "\n".join(payload["logs"]))
        finally:
            backend_app.run_backtest = original
            backend_app._backtest_runs.clear()
            backend_app._backtest_cancel_events.clear()


if __name__ == "__main__":
    unittest.main()
