from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

import storage.database as database
from market_analysis.breadth import calculate_market_breadth, clear_market_breadth_cache
from strategies.base import StrategyContext
from strategies.high_52w_momentum.strategy import High52WeekMomentumStrategy
from strategies.registry import get_strategy, list_strategies


def _price_frame(closes: np.ndarray, volume: float = 1_000_000) -> pd.DataFrame:
    dates = pd.bdate_range("2025-01-02", periods=len(closes))
    close = pd.Series(closes, index=dates, dtype=float)
    frame = pd.DataFrame(index=dates)
    frame["open"] = close * 0.995
    frame["high"] = close * 1.01
    frame["low"] = close * 0.99
    frame["close"] = close
    frame["volume"] = volume
    frame["amount"] = close * volume
    frame["pct_chg"] = close.pct_change().fillna(0) * 100
    frame["turnover_n"] = frame["amount"].rolling(20, min_periods=1).sum()
    return frame


class High52WeekMomentumTests(unittest.TestCase):
    def test_strategy_is_registered(self) -> None:
        self.assertIsInstance(get_strategy("high_52w_momentum"), High52WeekMomentumStrategy)
        self.assertIn("high_52w_momentum", {item.id for item in list_strategies()})

    def test_selects_near_high_positive_momentum_and_orders_by_score(self) -> None:
        strategy = High52WeekMomentumStrategy()
        strong = np.linspace(10, 25, 270)
        moderate = np.linspace(10, 20, 270)
        moderate[-12:] = np.linspace(19.6, 20.0, 12)
        falling = np.linspace(25, 10, 270)
        data = {
            "000001": _price_frame(strong),
            "000002": _price_frame(moderate),
            "000003": _price_frame(falling),
        }
        pick_date = data["000001"].index[-1]
        context = StrategyContext(
            pick_date=pick_date,
            names={code: code for code in data},
            pool=set(data),
            progress_enabled=False,
        )

        candidates = strategy.select(data, {}, context)

        self.assertEqual([item.code for item in candidates], ["000001", "000002"])
        self.assertGreater(candidates[0].score, candidates[1].score)
        self.assertGreaterEqual(candidates[0].extra["high_proximity"], 0.9)
        self.assertGreater(candidates[0].extra["momentum_return"], 0)
        self.assertIn("distance_to_high_pct", candidates[0].extra)


class MarketBreadthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_path = database.DB_PATH
        self.tmp = tempfile.TemporaryDirectory()
        database.DB_PATH = Path(self.tmp.name) / "breadth.db"
        clear_market_breadth_cache()

    def tearDown(self) -> None:
        database.DB_PATH = self.original_path
        clear_market_breadth_cache()
        self.tmp.cleanup()

    def test_empty_database_returns_clear_unavailable_state(self) -> None:
        result = calculate_market_breadth()

        self.assertFalse(result["available"])
        self.assertEqual(result["status"], "暂无数据")

    def test_rising_market_is_classified_as_offensive(self) -> None:
        database.upsert_price_batch(
            {
                "000001": _price_frame(np.linspace(10, 20, 70)),
                "600000": _price_frame(np.linspace(15, 30, 70)),
            },
            "qfq",
        )

        result = calculate_market_breadth(markets=["main"])
        components = {item["id"]: item for item in result["components"]}

        self.assertTrue(result["available"])
        self.assertEqual(result["status"], "进攻")
        self.assertEqual(result["risk_level"], "较低")
        self.assertEqual(result["stock_count"], 2)
        self.assertEqual(components["ma20_breadth"]["value_pct"], 100.0)
        self.assertEqual(components["ma60_breadth"]["value_pct"], 100.0)


if __name__ == "__main__":
    unittest.main()
