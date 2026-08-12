"""Market breadth and risk-state calculation backed by the local SQLite store."""
from __future__ import annotations

import threading
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from pipeline.select_stock import market_of_code
from storage.database import load_market_price_window, price_data_signature

ALL_MARKETS = ("bse", "gem", "main", "star")
_CACHE_LOCK = threading.Lock()
_CACHE: dict[tuple[Any, ...], dict[str, Any]] = {}


def clear_market_breadth_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


def _percent(numerator: int, denominator: int) -> float:
    return round(numerator / denominator * 100.0, 2) if denominator else 0.0


def _component_signal(value: float) -> str:
    if value >= 70:
        return "强势"
    if value >= 55:
        return "偏强"
    if value >= 45:
        return "中性"
    if value >= 30:
        return "偏弱"
    return "弱势"


def _regime(score: float) -> tuple[str, str, str]:
    if score >= 70:
        return "进攻", "较低", "建议仓位 80%–100%"
    if score >= 55:
        return "偏强", "中低", "建议仓位 60%–80%"
    if score >= 40:
        return "中性", "中等", "建议仓位 40%–60%"
    if score >= 25:
        return "谨慎", "较高", "建议仓位 20%–40%"
    return "防守", "高", "建议仓位 0%–20%"


def _daily_snapshot(rows: pd.DataFrame) -> dict[str, Any]:
    ma20_rows = rows.dropna(subset=["ma20"])
    ma60_rows = rows.dropna(subset=["ma60"])
    high_low_rows = rows.dropna(subset=["rolling_high_60", "rolling_low_60"])
    advance_rows = rows.dropna(subset=["previous_close"])

    above_ma20 = int((ma20_rows["close"] >= ma20_rows["ma20"]).sum())
    above_ma60 = int((ma60_rows["close"] >= ma60_rows["ma60"]).sum())
    advancing = int((advance_rows["close"] > advance_rows["previous_close"]).sum())
    new_highs = int((high_low_rows["close"] >= high_low_rows["rolling_high_60"] * 0.999999).sum())
    new_lows = int((high_low_rows["close"] <= high_low_rows["rolling_low_60"] * 1.000001).sum())

    ma20_pct = _percent(above_ma20, len(ma20_rows))
    ma60_pct = _percent(above_ma60, len(ma60_rows))
    advance_pct = _percent(advancing, len(advance_rows))
    high_pct = _percent(new_highs, len(high_low_rows))
    low_pct = _percent(new_lows, len(high_low_rows))
    # 当天既无新高也无新低时取中性值，避免 0/0 被误判成极端弱势。
    high_low_strength = 50.0
    if new_highs + new_lows:
        high_low_strength = round(new_highs / (new_highs + new_lows) * 100.0, 2)
    score = round(
        ma20_pct * 0.30
        + ma60_pct * 0.25
        + advance_pct * 0.15
        + high_low_strength * 0.30,
        1,
    )
    status, risk_level, position = _regime(score)
    return {
        "trade_date": str(rows["trade_date"].iloc[-1]),
        "score": score,
        "status": status,
        "risk_level": risk_level,
        "position_guidance": position,
        "stock_count": int(rows["code"].nunique()),
        "above_ma20_pct": ma20_pct,
        "above_ma60_pct": ma60_pct,
        "advance_pct": advance_pct,
        "new_high_60_pct": high_pct,
        "new_low_60_pct": low_pct,
        "new_high_count": new_highs,
        "new_low_count": new_lows,
        "high_low_strength": high_low_strength,
    }


def calculate_market_breadth(
    adjust: str = "qfq",
    end_date: str | None = None,
    markets: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    selected_markets = tuple(sorted(set(markets or ALL_MARKETS)))
    signature = price_data_signature(adjust)
    # 数据库行数和最新交易日进入缓存键；行情更新后旧市场宽度会自动失效。
    cache_key = (adjust, end_date, selected_markets, *signature)
    with _CACHE_LOCK:
        cached = _CACHE.get(cache_key)
    if cached is not None:
        return cached

    frame = load_market_price_window(adjust=adjust, bars=90, end_date=end_date)
    if frame.empty:
        return {
            "available": False,
            "trade_date": None,
            "status": "暂无数据",
            "risk_level": "未知",
            "score": None,
            "position_guidance": "请先更新本地日线数据",
            "summary": "SQLite 中没有可用于市场宽度计算的行情。",
            "components": [],
            "history": [],
        }

    frame["code"] = frame["code"].astype(str).str.zfill(6)
    frame = frame[frame["code"].map(market_of_code).isin(selected_markets)].copy()
    if frame.empty:
        return {
            "available": False,
            "trade_date": None,
            "status": "暂无数据",
            "risk_level": "未知",
            "score": None,
            "position_guidance": "当前板块选择没有可用行情",
            "summary": "所选板块在本地数据库中没有可计算的行情。",
            "components": [],
            "history": [],
        }

    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce")
    frame = frame.dropna(subset=["trade_date", "close"]).sort_values(["code", "trade_date"])
    grouped = frame.groupby("code", sort=False)
    # 所有滚动窗口必须在单只股票内部计算，reset_index 用来恢复原 DataFrame 的行索引。
    frame["previous_close"] = grouped["close"].shift(1)
    frame["ma20"] = grouped["close"].rolling(20, min_periods=20).mean().reset_index(level=0, drop=True)
    frame["ma60"] = grouped["close"].rolling(60, min_periods=60).mean().reset_index(level=0, drop=True)
    frame["rolling_high_60"] = grouped["close"].rolling(60, min_periods=60).max().reset_index(level=0, drop=True)
    frame["rolling_low_60"] = grouped["close"].rolling(60, min_periods=60).min().reset_index(level=0, drop=True)
    frame["trade_date"] = frame["trade_date"].dt.strftime("%Y-%m-%d")

    snapshots = [_daily_snapshot(rows) for _, rows in frame.groupby("trade_date", sort=True)]
    snapshots = [item for item in snapshots if item["stock_count"]]
    latest = snapshots[-1]
    components = [
        {
            "id": "ma20_breadth",
            "name": "站上20日均线",
            "value_pct": latest["above_ma20_pct"],
            "signal": _component_signal(latest["above_ma20_pct"]),
            "description": "衡量短期趋势广度，比例越高代表上涨并非只集中在少数股票。",
        },
        {
            "id": "ma60_breadth",
            "name": "站上60日均线",
            "value_pct": latest["above_ma60_pct"],
            "signal": _component_signal(latest["above_ma60_pct"]),
            "description": "衡量中期趋势广度，用于确认短期强势是否具有持续基础。",
        },
        {
            "id": "advance_ratio",
            "name": "当日上涨家数",
            "value_pct": latest["advance_pct"],
            "signal": _component_signal(latest["advance_pct"]),
            "description": "上涨股票占当日可比股票的比例，反映最新交易日的市场情绪。",
        },
        {
            "id": "high_low_strength",
            "name": "60日新高强弱",
            "value_pct": latest["high_low_strength"],
            "signal": _component_signal(latest["high_low_strength"]),
            "description": f"60日新高 {latest['new_high_count']} 只，新低 {latest['new_low_count']} 只。",
        },
    ]
    result = {
        "available": True,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "trade_date": latest["trade_date"],
        "markets": list(selected_markets),
        "status": latest["status"],
        "risk_level": latest["risk_level"],
        "score": latest["score"],
        "position_guidance": latest["position_guidance"],
        "summary": (
            f"市场宽度得分 {latest['score']:.1f}，当前处于{latest['status']}状态；"
            f"短期宽度 {latest['above_ma20_pct']:.1f}%，60日新高/新低 "
            f"{latest['new_high_count']}/{latest['new_low_count']}。"
        ),
        "stock_count": latest["stock_count"],
        "components": components,
        "history": snapshots[-20:],
        "methodology": "MA20 30% + MA60 25% + 上涨家数 15% + 60日新高/新低强弱 30%",
        "disclaimer": "市场状态是仓位参考，不是涨跌预测或交易承诺。",
    }
    with _CACHE_LOCK:
        _CACHE.clear()
        _CACHE[cache_key] = result
    return result
