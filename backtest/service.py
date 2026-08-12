"""Daily signal backtest built on the same strategy registry as live selection."""
from __future__ import annotations

import logging
import hashlib
import inspect
import threading
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict

import numpy as np
import pandas as pd

from backtest.cache import build_cache_key, load_prepared_cache, save_prepared_cache
from backtest.schemas import BacktestRequest, BacktestResult, BacktestTrade
from pipeline.cancellation import raise_if_cancelled
from pipeline.pipeline_core import build_top_turnover_pool
from pipeline.select_stock import filter_data_by_markets, load_stock_names, normalize_strategy_config
from storage.database import daily_price_fingerprint, list_trade_dates, load_daily_prices
from strategies.base import StrategyContext
from strategies.registry import get_strategy

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str, str, int, int], None]
ROOT = Path(__file__).resolve().parent.parent
ALL_MARKETS = ["main", "gem", "star", "bse"]


def _rounded(value: float | int | None, digits: int = 4) -> float | None:
    if value is None or not np.isfinite(float(value)):
        return None
    return round(float(value), digits)


def _row_at(frame: pd.DataFrame, date: pd.Timestamp) -> pd.Series | None:
    if date not in frame.index:
        return None
    row = frame.loc[date]
    return row.iloc[-1] if isinstance(row, pd.DataFrame) else row


def _evaluate_trade(
    candidate: Any,
    frame: pd.DataFrame,
    market_dates: list[pd.Timestamp],
    signal_index: int,
    holding_days: int,
    signal_rank: int,
) -> BacktestTrade:
    trade = BacktestTrade(
        signal_date=str(market_dates[signal_index].date()),
        signal_rank=signal_rank,
        code=str(candidate.code).zfill(6),
        name=str(candidate.name),
        strategy_id=str(candidate.strategy),
        strategy_score=float(candidate.score),
        signal_close=float(candidate.close),
        holding_days=holding_days,
        extra=dict(candidate.extra or {}),
    )
    entry_index = signal_index + 1
    if entry_index >= len(market_dates):
        trade.note = "缺少下一交易日行情"
        return trade

    entry_date = market_dates[entry_index]
    trade.entry_date = str(entry_date.date())
    entry_row = _row_at(frame, entry_date)
    if entry_row is None:
        trade.status = "unexecutable"
        trade.note = "下一交易日停牌或缺少开盘价，无法按计划成交"
        return trade

    entry_open = float(entry_row.get("open", 0) or 0)
    if not np.isfinite(entry_open) or entry_open <= 0:
        trade.status = "unexecutable"
        trade.note = "下一交易日开盘价无效"
        return trade
    trade.entry_open = _rounded(entry_open)

    target_dates = market_dates[entry_index : entry_index + holding_days]
    last_close: float | None = None
    highs: list[float] = []
    lows: list[float] = []
    for day_no, target_date in enumerate(target_dates, 1):
        row = _row_at(frame, target_date)
        carried = row is None
        if row is not None:
            close = float(row.get("close", np.nan))
            high = float(row.get("high", close))
            low = float(row.get("low", close))
            if np.isfinite(close) and close > 0:
                last_close = close
            if np.isfinite(high) and high > 0:
                highs.append(high)
            if np.isfinite(low) and low > 0:
                lows.append(low)
        close_for_return = last_close
        trade.daily_returns.append(
            {
                "day": day_no,
                "date": str(target_date.date()),
                "close": _rounded(close_for_return),
                "return_pct": _rounded((close_for_return / entry_open - 1) * 100) if close_for_return else None,
                "carried": carried and close_for_return is not None,
            }
        )

    if len(target_dates) < holding_days:
        trade.status = "pending"
        trade.note = f"未来行情不足，仅有 {len(target_dates)}/{holding_days} 个交易日"
        return trade
    if last_close is None:
        trade.status = "pending"
        trade.note = "持有期内缺少有效收盘价"
        return trade

    trade.exit_date = str(target_dates[-1].date())
    trade.exit_close = _rounded(last_close)
    trade.final_return_pct = _rounded((last_close / entry_open - 1) * 100)
    trade.max_gain_pct = _rounded((max(highs) / entry_open - 1) * 100) if highs else None
    trade.max_drawdown_pct = _rounded((min(lows) / entry_open - 1) * 100) if lows else None
    trade.status = "completed"
    trade.note = "按下一交易日开盘买入、持有期末收盘卖出"
    return trade


def _build_statistics(trades: list[BacktestTrade], holding_periods: list[int]) -> tuple[dict, list[dict], list[dict]]:
    holding_days = max(holding_periods)
    completed = [trade for trade in trades if trade.status == "completed" and trade.final_return_pct is not None]
    returns = [float(trade.final_return_pct) for trade in completed]
    wins = [value for value in returns if value > 0]
    losses = [value for value in returns if value < 0]
    avg_win = float(np.mean(wins)) if wins else None
    avg_loss = float(np.mean(losses)) if losses else None
    profit_loss_ratio = avg_win / abs(avg_loss) if avg_win is not None and avg_loss not in (None, 0) else None
    profit_factor = sum(wins) / abs(sum(losses)) if wins and losses and sum(losses) else None
    metrics = {
        "signal_count": len(trades),
        "executable_count": sum(trade.entry_open is not None for trade in trades),
        "completed_count": len(completed),
        "pending_count": sum(trade.status == "pending" for trade in trades),
        "unexecutable_count": sum(trade.status == "unexecutable" for trade in trades),
        "win_count": len(wins),
        "loss_count": len(losses),
        "flat_count": len(returns) - len(wins) - len(losses),
        "win_rate_pct": _rounded(len(wins) / len(completed) * 100) if completed else None,
        "average_return_pct": _rounded(float(np.mean(returns))) if returns else None,
        "median_return_pct": _rounded(float(np.median(returns))) if returns else None,
        "average_win_pct": _rounded(avg_win),
        "average_loss_pct": _rounded(avg_loss),
        "profit_loss_ratio": _rounded(profit_loss_ratio),
        "profit_factor": _rounded(profit_factor),
        "best_return_pct": _rounded(max(returns)) if returns else None,
        "worst_return_pct": _rounded(min(returns)) if returns else None,
    }

    horizon_stats: list[dict] = []
    for day_no in holding_periods:
        values = [
            float(item["return_pct"])
            for trade in trades
            for item in trade.daily_returns
            if item["day"] == day_no and item["return_pct"] is not None
        ]
        horizon_stats.append(
            {
                "day": day_no,
                "sample_count": len(values),
                "win_rate_pct": _rounded(sum(value > 0 for value in values) / len(values) * 100) if values else None,
                "average_return_pct": _rounded(float(np.mean(values))) if values else None,
                "median_return_pct": _rounded(float(np.median(values))) if values else None,
                "best_return_pct": _rounded(max(values)) if values else None,
                "worst_return_pct": _rounded(min(values)) if values else None,
            }
        )

    grouped: dict[str, list[BacktestTrade]] = defaultdict(list)
    for trade in completed:
        grouped[trade.code].append(trade)
    stock_ranking: list[dict] = []
    for code, items in grouped.items():
        values = [float(item.final_return_pct) for item in items if item.final_return_pct is not None]
        stock_ranking.append(
            {
                "code": code,
                "name": items[0].name,
                "trade_count": len(values),
                "win_rate_pct": _rounded(sum(value > 0 for value in values) / len(values) * 100),
                "average_return_pct": _rounded(float(np.mean(values))),
                "best_return_pct": _rounded(max(values)),
                "worst_return_pct": _rounded(min(values)),
            }
        )
    stock_ranking.sort(key=lambda item: (item["average_return_pct"], item["trade_count"]), reverse=True)
    for rank, item in enumerate(stock_ranking, 1):
        item["rank"] = rank
    return metrics, horizon_stats, stock_ranking


def _strategy_signature(strategy: Any) -> str:
    source_path = inspect.getsourcefile(strategy.__class__)
    if not source_path:
        return strategy.__class__.__qualname__
    try:
        return hashlib.sha256(Path(source_path).read_bytes()).hexdigest()[:16]
    except OSError:
        return strategy.__class__.__qualname__


def _compact_prepared_cache(
    strategy: Any,
    strategy_cfg: dict,
    prepared: dict[str, pd.DataFrame],
) -> dict[str, pd.DataFrame]:
    if not hasattr(strategy, "cache_columns"):
        return prepared
    requested_columns = set(strategy.cache_columns(strategy_cfg))
    compact: dict[str, pd.DataFrame] = {}
    for code, frame in prepared.items():
        columns = [column for column in frame.columns if column in requested_columns]
        compact[code] = frame.loc[:, columns].copy()
    return compact


def run_backtest(
    backtest_id: str,
    request: BacktestRequest,
    stop_event: threading.Event | None = None,
    progress: ProgressCallback | None = None,
) -> BacktestResult:
    cfg = normalize_strategy_config(request.config)
    strategy = get_strategy(request.strategy_id)
    global_cfg = cfg.get("global", {})
    strategy_cfg = cfg.get("strategies", {}).get(request.strategy_id, {})
    holding_periods = request.resolved_holding_periods()
    if not holding_periods or holding_periods[0] < 1 or holding_periods[-1] > 60:
        raise ValueError("持有周期必须在 1 至 60 个交易日之间")
    max_holding_days = max(holding_periods)
    adjust = str(global_cfg.get("adjust", "qfq"))
    top_m = int(global_cfg.get("top_m", 3000))
    turnover_days = int(global_cfg.get("n_turnover_days", 43))
    markets = list(global_cfg.get("markets") or ALL_MARKETS)

    all_date_strings = list_trade_dates(adjust)
    market_dates = [pd.Timestamp(value) for value in all_date_strings]
    requested_start = pd.Timestamp(request.start_date)
    requested_end = pd.Timestamp(request.end_date)
    signal_indexes = [
        index for index, value in enumerate(market_dates)
        if requested_start <= value <= requested_end
    ]
    if not signal_indexes:
        raise ValueError("选定日期范围内没有本地交易日数据")
    if len(signal_indexes) > 500:
        raise ValueError("单次回测最多支持 500 个交易日，请缩短日期范围")

    warmup_bars = max(strategy.warmup_bars(strategy_cfg), turnover_days) + 5
    load_start_index = max(0, signal_indexes[0] - warmup_bars)
    load_end_index = min(len(market_dates) - 1, signal_indexes[-1] + max_holding_days)
    load_start = str(market_dates[load_start_index].date())
    load_end = str(market_dates[load_end_index].date())
    if progress:
        progress("加载行情", f"读取 {load_start} 至 {load_end} 的回测行情", 0, len(signal_indexes))
    raise_if_cancelled(stop_event)
    data = load_daily_prices(adjust, turnover_days, start_date=load_start, end_date=load_end)
    data = filter_data_by_markets(data, markets)
    if not data:
        raise ValueError("数据库中没有符合板块条件的行情，请先更新本地数据")

    names = load_stock_names(str(global_cfg.get("stock_list_file", "data/stocklist.csv")))
    indicator_data = {
        code: frame.loc[frame.index <= requested_end].copy()
        for code, frame in data.items()
    }
    indicator_config = (
        strategy.indicator_config(strategy_cfg)
        if hasattr(strategy, "indicator_config")
        else strategy_cfg
    )
    cache_metadata = {
        "strategy_id": request.strategy_id,
        "strategy_signature": _strategy_signature(strategy),
        "indicator_config": indicator_config,
        "adjust": adjust,
        "markets": sorted(markets),
        "n_turnover_days": turnover_days,
        "load_start": load_start,
        "load_end": request.end_date,
        "price_data": daily_price_fingerprint(adjust, load_start, request.end_date),
    }
    cache_key = build_cache_key(cache_metadata)
    prepared, cache_source = load_prepared_cache(request.strategy_id, cache_key)
    prepare_context = StrategyContext(
        pick_date=market_dates[signal_indexes[0]],
        names=names,
        markets=markets,
        cancel_requested=(lambda: bool(stop_event and stop_event.is_set())),
        progress_enabled=True,
        progress_callback=(
            (lambda message, current, total: progress("指标预计算", message, current, total))
            if progress else None
        ),
    )
    if prepared is not None:
        source_label = "进程内存" if cache_source == "memory" else "磁盘"
        message = f"命中{source_label}指标缓存，复用 {len(prepared)} 只股票的 {strategy.meta.name} 指标"
        logger.info(message)
        if progress:
            progress("指标预计算", message, len(indicator_data), len(indicator_data))
    else:
        if progress:
            progress("指标预计算", f"缓存未命中，开始预计算 {len(indicator_data)} 只股票的 {strategy.meta.name} 指标", 0, len(indicator_data))
        prepared = strategy.prepare_all(indicator_data, strategy_cfg, prepare_context)
        raise_if_cancelled(stop_event)
        try:
            cached_prepared = _compact_prepared_cache(strategy, strategy_cfg, prepared)
            cache_path = save_prepared_cache(request.strategy_id, cache_key, cached_prepared, cache_metadata)
            cache_source = "created"
            message = f"指标预计算完成并写入缓存：{cache_path.name}"
            logger.info(message)
            if progress:
                progress("指标预计算", message, len(indicator_data), len(indicator_data))
        except Exception as exc:  # noqa: BLE001
            cache_source = "save_failed"
            logger.warning("指标缓存写入失败，本次回测继续执行：%s", exc)
    raise_if_cancelled(stop_event)

    trades: list[BacktestTrade] = []
    daily_stats: list[dict] = []
    total_days = len(signal_indexes)
    for completed_days, signal_index in enumerate(signal_indexes, 1):
        raise_if_cancelled(stop_event)
        signal_date = market_dates[signal_index]
        pool = build_top_turnover_pool(prepared, top_m, signal_date, stop_event=stop_event)
        context = StrategyContext(
            pick_date=signal_date,
            names=names,
            pool=pool,
            markets=markets,
            cancel_requested=(lambda: bool(stop_event and stop_event.is_set())),
            progress_enabled=False,
        )
        candidates = strategy.select_prepared(prepared, strategy_cfg, context)
        day_trades: list[BacktestTrade] = []
        for signal_rank, candidate in enumerate(candidates, 1):
            frame = data.get(str(candidate.code).zfill(6))
            if frame is None or frame.empty:
                continue
            day_trades.append(
                _evaluate_trade(candidate, frame, market_dates, signal_index, max_holding_days, signal_rank)
            )
        trades.extend(day_trades)
        completed_returns = [
            float(item.final_return_pct)
            for item in day_trades
            if item.status == "completed" and item.final_return_pct is not None
        ]
        daily_stats.append(
            {
                "signal_date": str(signal_date.date()),
                "selected_count": len(candidates),
                "completed_count": len(completed_returns),
                "win_rate_pct": _rounded(sum(value > 0 for value in completed_returns) / len(completed_returns) * 100)
                if completed_returns else None,
                "average_return_pct": _rounded(float(np.mean(completed_returns))) if completed_returns else None,
            }
        )
        message = (
            f"逐日回测 {completed_days}/{total_days}：{signal_date.date()}，"
            f"选出 {len(candidates)} 只，累计 {len(trades)} 条信号"
        )
        logger.info(message)
        if progress:
            progress("逐日选股", message, completed_days, total_days)

    trades.sort(
        key=lambda item: (
            item.status == "completed",
            item.final_return_pct if item.final_return_pct is not None else float("-inf"),
        ),
        reverse=True,
    )
    for rank, trade in enumerate(trades, 1):
        trade.rank = rank
    metrics, horizon_stats, stock_ranking = _build_statistics(trades, holding_periods)
    metrics["signal_day_count"] = len(signal_indexes)

    return BacktestResult(
        backtest_id=backtest_id,
        generated_at=datetime.now().isoformat(timespec="seconds"),
        request={
            "strategy_id": request.strategy_id,
            "strategy_name": strategy.meta.name,
            "start_date": request.start_date,
            "end_date": request.end_date,
            "holding_days": max_holding_days,
            "holding_periods": holding_periods,
        },
        metrics=metrics,
        horizon_stats=horizon_stats,
        daily_stats=daily_stats,
        stock_ranking=stock_ranking,
        trades=trades,
        meta={
            "adjust": adjust,
            "markets": markets,
            "top_m": top_m,
            "n_turnover_days": turnover_days,
            "loaded_range": {"start": load_start, "end": load_end},
            "loaded_stocks": len(data),
            "indicator_cache": {
                "key": cache_key,
                "source": cache_source,
                "reused": cache_source in {"memory", "disk"},
            },
            "assumptions": [
                "信号使用选股日收盘前可见数据",
                "下一市场交易日开盘价买入",
                "第 X 个市场交易日收盘价卖出",
                "暂不计佣金、印花税、滑点和涨跌停无法成交影响",
                "每条信号独立统计，不限制同时持仓数量",
            ],
        },
    )
