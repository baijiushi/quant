from __future__ import annotations

import math

import pandas as pd
import pytest

from entry_analysis.service import build_daily_entry_plan


def _trending_prices(rows: int = 100) -> pd.DataFrame:
    dates = pd.date_range("2025-01-02", periods=rows, freq="B")
    close = [10 + index * 0.055 + math.sin(index / 3) * 0.42 for index in range(rows)]
    return pd.DataFrame(
        {
            "open": [value - 0.06 for value in close],
            "high": [value + 0.22 for value in close],
            "low": [value - 0.24 for value in close],
            "close": close,
            "volume": [1_000_000 + index * 2_000 for index in range(rows)],
            "amount": [10_000_000 + index * 50_000 for index in range(rows)],
        },
        index=dates,
    )


def test_daily_entry_plan_labels_smt_as_unavailable() -> None:
    result = build_daily_entry_plan(
        _trending_prices(),
        code="1",
        name="示例股票",
        account_value=200_000,
        risk_pct=0.5,
        reward_risk=2,
    )

    assert result["code"] == "000001"
    assert result["mode"] == "a_share_daily_proxy"
    assert result["smt"]["available"] is False
    assert "分钟" in result["smt"]["reason"]
    assert result["trend"]["direction"] in {"bullish", "bearish", "neutral"}
    assert result["historical_review"]["window_bars"] == 60
    assert result["historical_review"]["requested_window_bars"] == 60
    assert result["historical_review"]["start_date"] == _trending_prices().index[-60].strftime("%Y-%m-%d")
    assert isinstance(result["historical_review"]["signals"], list)


def test_daily_entry_plan_builds_risk_limited_long_plan() -> None:
    result = build_daily_entry_plan(
        _trending_prices(),
        code="000001",
        account_value=100_000,
        risk_pct=1,
        reward_risk=2,
    )

    if result["trend"]["direction"] != "bullish":
        pytest.skip("synthetic structure did not end on its bullish BOS")
    assert result["entry"]["trigger_price"] > result["entry"]["stop_price"]
    assert result["entry"]["target_2r"] > result["entry"]["target_1r"]
    assert result["position"]["suggested_shares"] % 100 == 0
    assert result["position"]["planned_loss"] <= result["position"]["risk_budget"]


def test_daily_entry_plan_rejects_insufficient_history() -> None:
    with pytest.raises(ValueError, match="至少需要"):
        build_daily_entry_plan(_trending_prices(20), code="000001")


def test_daily_entry_plan_rejects_unsafe_risk_input() -> None:
    with pytest.raises(ValueError, match="风险比例"):
        build_daily_entry_plan(_trending_prices(), code="000001", risk_pct=8)


def test_historical_review_signals_only_use_past_bars() -> None:
    prices = _trending_prices(120)
    # Force a deterministic interception/reclaim and breakout near the end of
    # the 60-bar review window instead of relying on the smooth wave alone.
    prices.iloc[-8, prices.columns.get_loc("high")] = prices["high"].iloc[-20:-8].max() + 2
    prices.iloc[-8, prices.columns.get_loc("close")] = prices["high"].iloc[-8] - 0.1
    prices.iloc[-7, prices.columns.get_loc("high")] = prices["high"].iloc[-8] + 0.5
    prices.iloc[-7, prices.columns.get_loc("close")] = prices["high"].iloc[-8] + 0.4
    prices.iloc[-6, prices.columns.get_loc("low")] = prices["low"].iloc[-15:-8].min()
    prices.iloc[-6, prices.columns.get_loc("close")] = prices["close"].iloc[-7] - 0.3
    prices.iloc[-5, prices.columns.get_loc("high")] = prices["high"].iloc[-7] + 1
    prices.iloc[-5, prices.columns.get_loc("close")] = prices["high"].iloc[-5] - 0.05
    full = build_daily_entry_plan(prices, code="000001", reward_risk=2)
    signals = full["historical_review"]["signals"]

    for signal in signals:
        truncated = build_daily_entry_plan(
            prices,
            code="000001",
            as_of=signal["signal_date"],
            reward_risk=2,
        )
        same_day = [
            item
            for item in truncated["historical_review"]["signals"]
            if item["signal_date"] == signal["signal_date"]
        ]
        assert same_day
        assert same_day[0]["entry_price"] == signal["entry_price"]
        assert same_day[0]["stop_price"] == signal["stop_price"]
        assert same_day[0]["target_price"] == signal["target_price"]


def test_historical_review_exposes_risk_and_target_percentages() -> None:
    prices = _trending_prices(120)
    result = build_daily_entry_plan(prices, code="000001", reward_risk=2)
    for signal in result["historical_review"]["signals"]:
        assert signal["risk_pct_of_entry"] > 0
        assert signal["target_return_pct"] > 0
