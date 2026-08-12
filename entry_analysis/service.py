"""Build an evidence-labelled entry plan from the daily bars already in SQLite.

This module deliberately does not call the result an SMT signal. True SMT needs
two correlated instruments on aligned intraday bars; the current data store only
contains individual A-share daily bars.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import floor, isfinite
from typing import Any

import pandas as pd


MINIMUM_BARS = 40
DEFAULT_REVIEW_BARS = 60


@dataclass(frozen=True)
class StructureEvent:
    direction: str
    event_index: int
    pivot_index: int
    pivot_price: float
    structure_index: int | None
    structure_price: float | None


def _round_price(value: float | None) -> float | None:
    if value is None or not isfinite(float(value)):
        return None
    return round(float(value), 3)


def _prepare_prices(frame: pd.DataFrame, as_of: str | None) -> pd.DataFrame:
    prices = frame.copy()
    prices.columns = [str(column).lower() for column in prices.columns]
    if "date" in prices.columns:
        prices.index = pd.to_datetime(prices.pop("date"), errors="coerce")
    else:
        prices.index = pd.to_datetime(prices.index, errors="coerce")
    prices = prices[~prices.index.isna()].sort_index()
    required = ["open", "high", "low", "close"]
    missing = [column for column in required if column not in prices.columns]
    if missing:
        raise ValueError(f"行情缺少字段: {', '.join(missing)}")
    for column in [*required, "volume", "amount"]:
        if column in prices.columns:
            prices[column] = pd.to_numeric(prices[column], errors="coerce")
    prices = prices.dropna(subset=required)
    if as_of:
        cutoff = pd.Timestamp(as_of)
        prices = prices.loc[prices.index <= cutoff]
    return prices


def _atr(prices: pd.DataFrame, window: int) -> pd.Series:
    previous_close = prices["close"].shift(1)
    ranges = pd.concat(
        [
            prices["high"] - prices["low"],
            (prices["high"] - previous_close).abs(),
            (prices["low"] - previous_close).abs(),
        ],
        axis=1,
    )
    return ranges.max(axis=1).rolling(window, min_periods=max(3, window // 2)).mean()


def _confirmed_pivots(prices: pd.DataFrame, window: int) -> tuple[list[int], list[int]]:
    highs = prices["high"].to_numpy(dtype=float)
    lows = prices["low"].to_numpy(dtype=float)
    pivot_highs: list[int] = []
    pivot_lows: list[int] = []
    for index in range(window, len(prices) - window):
        high_slice = highs[index - window : index + window + 1]
        low_slice = lows[index - window : index + window + 1]
        if highs[index] >= high_slice.max():
            pivot_highs.append(index)
        if lows[index] <= low_slice.min():
            pivot_lows.append(index)
    return pivot_highs, pivot_lows


def _latest_structure_event(prices: pd.DataFrame, swing_window: int) -> StructureEvent | None:
    pivot_highs, pivot_lows = _confirmed_pivots(prices, swing_window)
    closes = prices["close"].to_numpy(dtype=float)
    highs = prices["high"].to_numpy(dtype=float)
    lows = prices["low"].to_numpy(dtype=float)
    events: list[StructureEvent] = []

    for pivot_index in pivot_highs:
        first_eligible = pivot_index + swing_window + 1
        for index in range(max(first_eligible, 1), len(prices)):
            if closes[index] > highs[pivot_index] and closes[index - 1] > highs[pivot_index]:
                supports = [item for item in pivot_lows if pivot_index < item < index]
                if not supports:
                    supports = [item for item in pivot_lows if item < index]
                structure_index = supports[-1] if supports else None
                events.append(
                    StructureEvent(
                        direction="bullish",
                        event_index=index,
                        pivot_index=pivot_index,
                        pivot_price=float(highs[pivot_index]),
                        structure_index=structure_index,
                        structure_price=float(lows[structure_index]) if structure_index is not None else None,
                    )
                )
                break

    for pivot_index in pivot_lows:
        first_eligible = pivot_index + swing_window + 1
        for index in range(max(first_eligible, 1), len(prices)):
            if closes[index] < lows[pivot_index] and closes[index - 1] < lows[pivot_index]:
                resistances = [item for item in pivot_highs if pivot_index < item < index]
                if not resistances:
                    resistances = [item for item in pivot_highs if item < index]
                structure_index = resistances[-1] if resistances else None
                events.append(
                    StructureEvent(
                        direction="bearish",
                        event_index=index,
                        pivot_index=pivot_index,
                        pivot_price=float(lows[pivot_index]),
                        structure_index=structure_index,
                        structure_price=float(highs[structure_index]) if structure_index is not None else None,
                    )
                )
                break

    if not events:
        return None
    return max(events, key=lambda item: (item.event_index, item.pivot_index))


def _active_fvg(prices: pd.DataFrame, direction: str) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    highs = prices["high"].to_numpy(dtype=float)
    lows = prices["low"].to_numpy(dtype=float)
    for index in range(2, len(prices)):
        if direction == "bullish" and lows[index] > highs[index - 2]:
            zone_low, zone_high = float(highs[index - 2]), float(lows[index])
            later_lows = lows[index + 1 :]
            active = not len(later_lows) or float(later_lows.min()) > zone_low
        elif direction == "bearish" and highs[index] < lows[index - 2]:
            zone_low, zone_high = float(highs[index]), float(lows[index - 2])
            later_highs = highs[index + 1 :]
            active = not len(later_highs) or float(later_highs.max()) < zone_high
        else:
            continue
        if active:
            candidates.append(
                {
                    "source": "daily_fvg_proxy",
                    "created_at": prices.index[index].strftime("%Y-%m-%d"),
                    "zone_low": zone_low,
                    "zone_high": zone_high,
                }
            )
    return candidates[-1] if candidates else None


def _interception_zone(
    prices: pd.DataFrame,
    direction: str,
    event: StructureEvent,
    atr_value: float,
) -> dict[str, Any]:
    fvg = _active_fvg(prices, direction)
    if fvg is not None:
        zone = fvg
    else:
        close = float(prices["close"].iloc[-1])
        ema20 = float(prices["close"].ewm(span=20, adjust=False).mean().iloc[-1])
        structure = event.structure_price
        if direction == "bullish":
            valid_levels = [value for value in [structure, ema20] if value is not None and value < close]
            base = max(valid_levels) if valid_levels else close - atr_value
        else:
            valid_levels = [value for value in [structure, ema20] if value is not None and value > close]
            base = min(valid_levels) if valid_levels else close + atr_value
        zone = {
            "source": "structure_atr_proxy",
            "created_at": prices.index[event.event_index].strftime("%Y-%m-%d"),
            "zone_low": base - atr_value * 0.25,
            "zone_high": base + atr_value * 0.25,
        }

    recent = prices.tail(5)
    touched = bool(
        (
            (recent["low"] <= float(zone["zone_high"]))
            & (recent["high"] >= float(zone["zone_low"]))
        ).any()
    )
    last_close = float(prices["close"].iloc[-1])
    reclaimed = touched and (
        last_close > float(zone["zone_high"])
        if direction == "bullish"
        else last_close < float(zone["zone_low"])
    )
    return {
        **zone,
        "zone_low": _round_price(float(zone["zone_low"])),
        "zone_high": _round_price(float(zone["zone_high"])),
        "touched_recently": touched,
        "reclaimed": reclaimed,
        "status": "已进入并收回" if reclaimed else "已进入区域" if touched else "等待回到区域",
    }


def _position_plan(
    entry_price: float,
    stop_price: float,
    account_value: float,
    risk_pct: float,
    lot_size: int,
) -> dict[str, Any]:
    risk_per_share = max(0.0, entry_price - stop_price)
    risk_budget = account_value * risk_pct / 100
    if risk_per_share <= 0:
        shares = 0
    else:
        risk_limited = floor(risk_budget / risk_per_share / lot_size) * lot_size
        cash_limited = floor(account_value / entry_price / lot_size) * lot_size
        shares = max(0, min(risk_limited, cash_limited))
    return {
        "account_value": round(account_value, 2),
        "risk_pct": round(risk_pct, 3),
        "risk_budget": round(risk_budget, 2),
        "risk_per_share": _round_price(risk_per_share),
        "suggested_shares": shares,
        "position_value": round(shares * entry_price, 2),
        "planned_loss": round(shares * risk_per_share, 2),
        "lot_size": lot_size,
    }


def _build_daily_snapshot(
    frame: pd.DataFrame,
    *,
    code: str,
    name: str = "",
    as_of: str | None = None,
    account_value: float = 100_000,
    risk_pct: float = 0.5,
    reward_risk: float = 1.0,
    atr_window: int = 14,
    swing_window: int = 2,
    lot_size: int = 100,
) -> dict[str, Any]:
    """Return an A-share daily proxy plan with explicit data limitations."""
    if account_value <= 0:
        raise ValueError("账户资金必须大于 0")
    if not 0 < risk_pct <= 5:
        raise ValueError("单笔风险比例必须在 0 到 5% 之间")
    if not 0.5 <= reward_risk <= 5:
        raise ValueError("目标盈亏比必须在 0.5R 到 5R 之间")
    if not 1 <= swing_window <= 10:
        raise ValueError("结构窗口必须在 1 到 10 之间")

    prices = _prepare_prices(frame, as_of)
    if len(prices) < MINIMUM_BARS:
        raise ValueError(f"至少需要 {MINIMUM_BARS} 根日线，当前只有 {len(prices)} 根")

    atr_series = _atr(prices, atr_window)
    atr_value = float(atr_series.iloc[-1])
    if not isfinite(atr_value) or atr_value <= 0:
        raise ValueError("无法计算有效 ATR")
    event = _latest_structure_event(prices, swing_window)
    last_date = prices.index[-1].strftime("%Y-%m-%d")
    last_close = float(prices["close"].iloc[-1])

    base: dict[str, Any] = {
        "code": str(code).zfill(6),
        "name": name,
        "as_of": last_date,
        "mode": "a_share_daily_proxy",
        "framework": "趋势 -> 截取 -> 入场",
        "source": {
            "author": "森林查尔斯",
            "videos": [
                {
                    "title": "从精通到入门 --- 模块一 市场认知",
                    "url": "https://www.bilibili.com/video/BV1BForBXE9p/",
                },
                {
                    "title": "从精通到入门 --- 模块二 逻辑",
                    "url": "https://www.bilibili.com/video/BV1boVK6vEHY/",
                },
            ],
        },
        "smt": {
            "status": "数据不足，未验证",
            "available": False,
            "reason": "真正 SMT 需要相关品种在同一时间轴上的多周期/分钟行情；当前数据库只有单只 A 股日线。",
            "required_data": ["相关指数或同类资产 15 分钟线", "候选股 15 分钟线", "候选股 1-5 分钟确认线"],
        },
        "warnings": [
            "这是对作者框架的 A 股日线工程适配，不是作者原版 NQ/ES SMT 信号。",
            "日线 FVG 只是 OHLC 形态代理，不代表已验证真实逐笔成交、深度或订单流失衡。",
            "A 股存在 T+1、涨跌停和跳空风险，计划止损价不保证能够成交。",
            "价格到达截取区不等于立即买入，仍需等待更低周期确认。",
        ],
    }

    if event is None:
        return {
            **base,
            "trend": {
                "direction": "neutral",
                "status": "结构不清晰，跳过",
                "basis": "未发现经过确认的双收盘 BOS",
                "close": _round_price(last_close),
                "atr": _round_price(atr_value),
            },
            "interception": None,
            "entry": {"action": "skip", "status": "等待形成清晰趋势结构"},
            "position": None,
        }

    direction_label = "多头" if event.direction == "bullish" else "空头"
    interception = _interception_zone(prices, event.direction, event, atr_value)
    event_date = prices.index[event.event_index].strftime("%Y-%m-%d")
    structure_date = (
        prices.index[event.structure_index].strftime("%Y-%m-%d")
        if event.structure_index is not None
        else None
    )
    trend = {
        "direction": event.direction,
        "status": f"{direction_label}结构",
        "basis": "最近一次双收盘有效突破（BOS）",
        "bos_date": event_date,
        "broken_level": _round_price(event.pivot_price),
        "structure_date": structure_date,
        "structure_price": _round_price(event.structure_price),
        "close": _round_price(last_close),
        "atr": _round_price(atr_value),
    }

    if event.direction == "bearish":
        return {
            **base,
            "trend": trend,
            "interception": interception,
            "entry": {
                "action": "avoid_or_reduce",
                "status": "当前为日线空头结构，A 股现货不生成做空开仓建议",
            },
            "position": None,
        }

    recent_high = float(prices["high"].iloc[-6:-1].max())
    tick = max(0.01, atr_value * 0.02)
    confirmed = bool(interception["touched_recently"] and last_close > recent_high)
    entry_price = last_close if confirmed else recent_high + tick
    stop_candidates = [float(interception["zone_low"])]
    if event.structure_price is not None:
        stop_candidates.append(float(event.structure_price))
    stop_price = min(stop_candidates) - atr_value * 0.1
    if stop_price >= entry_price:
        stop_price = entry_price - atr_value
    risk_per_share = entry_price - stop_price
    target_1r = entry_price + risk_per_share
    target_2r = entry_price + risk_per_share * 2
    selected_target = entry_price + risk_per_share * reward_risk
    if confirmed:
        action, status = "ready", "截取区后出现日线突破确认；下单前仍应检查分钟级结构"
    elif interception["touched_recently"]:
        action, status = "wait_confirmation", "已到截取区，等待突破最近 5 日高点确认"
    else:
        action, status = "wait_interception", "趋势成立，等待回到截取区"

    position = _position_plan(entry_price, stop_price, account_value, risk_pct, lot_size)
    entry = {
        "action": action,
        "status": status,
        "confirmation": "下一交易时段有效突破最近 5 日高点，并在更低周期出现 MSS/IFVG 等确认",
        "trigger_price": _round_price(entry_price),
        "stop_price": _round_price(stop_price),
        "target_1r": _round_price(target_1r),
        "target_2r": _round_price(target_2r),
        "selected_reward_risk": round(reward_risk, 2),
        "selected_target": _round_price(selected_target),
    }
    return {**base, "trend": trend, "interception": interception, "entry": entry, "position": position}


def _review_signal_outcome(
    prices: pd.DataFrame,
    signal_index: int,
    entry_price: float,
    stop_price: float,
    target_price: float,
) -> dict[str, Any]:
    """Evaluate bars after a historical close-confirmed signal without using them to create it."""
    risk = entry_price - stop_price
    if risk <= 0:
        return {"outcome": "invalid", "outcome_label": "风控价无效", "exit_date": None, "realized_r": None}

    future = prices.iloc[signal_index + 1 :]
    for date, row in future.iterrows():
        hit_stop = float(row["low"]) <= stop_price
        hit_target = float(row["high"]) >= target_price
        if hit_stop and hit_target:
            return {
                "outcome": "ambiguous",
                "outcome_label": "同日触及止盈止损（日线无法判先后）",
                "exit_date": date.strftime("%Y-%m-%d"),
                "realized_r": None,
            }
        if hit_stop:
            return {
                "outcome": "stopped",
                "outcome_label": "止损",
                "exit_date": date.strftime("%Y-%m-%d"),
                "realized_r": -1.0,
            }
        if hit_target:
            realized_r = (target_price - entry_price) / risk
            return {
                "outcome": "target",
                "outcome_label": "达到目标",
                "exit_date": date.strftime("%Y-%m-%d"),
                "realized_r": round(realized_r, 3),
            }

    last_close = float(prices["close"].iloc[-1])
    return {
        "outcome": "open",
        "outcome_label": "截至观察日未结束",
        "exit_date": None,
        "realized_r": round((last_close - entry_price) / risk, 3),
    }


def _scan_historical_entries(
    prices: pd.DataFrame,
    *,
    code: str,
    name: str,
    review_bars: int,
    account_value: float,
    risk_pct: float,
    reward_risk: float,
    atr_window: int,
    swing_window: int,
    lot_size: int,
) -> dict[str, Any]:
    review_start = max(MINIMUM_BARS - 1, len(prices) - review_bars)
    review_prices = prices.iloc[review_start:]
    signals: list[dict[str, Any]] = []
    previous_ready = False

    for signal_index in range(review_start, len(prices)):
        prefix = prices.iloc[: signal_index + 1]
        snapshot = _build_daily_snapshot(
            prefix,
            code=code,
            name=name,
            account_value=account_value,
            risk_pct=risk_pct,
            reward_risk=reward_risk,
            atr_window=atr_window,
            swing_window=swing_window,
            lot_size=lot_size,
        )
        ready = snapshot["entry"].get("action") == "ready"
        # A confirmation can remain true for adjacent bars. Record the transition,
        # not every bar in the same confirmation cluster.
        if ready and not previous_ready:
            entry = snapshot["entry"]
            interception = snapshot.get("interception") or {}
            entry_price = float(entry["trigger_price"])
            stop_price = float(entry["stop_price"])
            target_price = float(entry["selected_target"])
            risk_pct_of_entry = (entry_price - stop_price) / entry_price * 100
            target_return_pct = (target_price - entry_price) / entry_price * 100
            outcome = _review_signal_outcome(
                prices,
                signal_index,
                entry_price,
                stop_price,
                target_price,
            )
            signals.append(
                {
                    "signal_date": prices.index[signal_index].strftime("%Y-%m-%d"),
                    "entry_price": _round_price(entry_price),
                    "stop_price": _round_price(stop_price),
                    "target_price": _round_price(target_price),
                    "planned_reward_risk": round(reward_risk, 2),
                    "risk_pct_of_entry": round(risk_pct_of_entry, 2),
                    "target_return_pct": round(target_return_pct, 2),
                    "zone_low": interception.get("zone_low"),
                    "zone_high": interception.get("zone_high"),
                    "zone_source": interception.get("source"),
                    "bos_date": snapshot["trend"].get("bos_date"),
                    **outcome,
                }
            )
        previous_ready = ready

    completed = [item for item in signals if item["outcome"] in {"target", "stopped"}]
    wins = sum(item["outcome"] == "target" for item in completed)
    losses = sum(item["outcome"] == "stopped" for item in completed)
    return {
        "window_bars": len(review_prices),
        "requested_window_bars": review_bars,
        "start_date": review_prices.index[0].strftime("%Y-%m-%d"),
        "end_date": review_prices.index[-1].strftime("%Y-%m-%d"),
        "signal_count": len(signals),
        "completed_count": len(completed),
        "win_count": wins,
        "loss_count": losses,
        "win_rate": round(wins / len(completed), 4) if completed else None,
        "completed_profit_loss_ratio": round(reward_risk, 3) if wins and losses else None,
        "signals": signals,
        "methodology": (
            "逐日仅使用当日及以前K线生成信号；记录最近60个交易日内从未确认到确认的入场点，"
            "再用其后的日线检查止盈/止损。若同一日同时触及两者，结果标为无法判断。"
        ),
    }


def build_daily_entry_plan(
    frame: pd.DataFrame,
    *,
    code: str,
    name: str = "",
    as_of: str | None = None,
    account_value: float = 100_000,
    risk_pct: float = 0.5,
    reward_risk: float = 1.0,
    atr_window: int = 14,
    swing_window: int = 2,
    lot_size: int = 100,
    review_bars: int = DEFAULT_REVIEW_BARS,
) -> dict[str, Any]:
    """Scan historical entry points in the review window and retain a current snapshot for context."""
    if not 20 <= review_bars <= 250:
        raise ValueError("历史观察窗口必须在 20 到 250 个交易日之间")
    prices = _prepare_prices(frame, as_of)
    snapshot = _build_daily_snapshot(
        prices,
        code=code,
        name=name,
        account_value=account_value,
        risk_pct=risk_pct,
        reward_risk=reward_risk,
        atr_window=atr_window,
        swing_window=swing_window,
        lot_size=lot_size,
    )
    history = _scan_historical_entries(
        prices,
        code=code,
        name=name,
        review_bars=review_bars,
        account_value=account_value,
        risk_pct=risk_pct,
        reward_risk=reward_risk,
        atr_window=atr_window,
        swing_window=swing_window,
        lot_size=lot_size,
    )
    snapshot["framework"] = "最近交易日历史截取/入场复盘"
    snapshot["historical_review"] = history
    snapshot["warnings"] = [
        "历史标记是日线规则复盘，不是当前买入推荐。",
        "确认点按当日收盘价计算；实盘只能在之后交易，跳空与滑点会改变盈亏比。",
        *snapshot["warnings"],
    ]
    return snapshot
