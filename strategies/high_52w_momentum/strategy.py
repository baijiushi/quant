"""52-week-high momentum strategy built on the standard OHLCV schema."""
from __future__ import annotations

import logging
from typing import Dict

import numpy as np
import pandas as pd
from tqdm import tqdm

from pipeline.cancellation import RunCancelledError
from pipeline.schemas import Candidate
from strategies.base import StrategyContext, StrategyMeta

logger = logging.getLogger(__name__)


DEFAULT_CONFIG = {
    "enabled": True,
    "high_lookback_days": 252,
    "momentum_lookback_days": 126,
    "momentum_skip_days": 20,
    "trend_ma_days": 60,
    "min_high_proximity": 0.90,
    "min_momentum_return": 0.0,
    "require_above_trend_ma": True,
    "high_proximity_weight": 0.60,
    "momentum_weight": 0.40,
    "max_candidates": 30,
}


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        if isinstance(value, pd.Series):
            value = value.iloc[-1]
        result = float(value)
        return default if not np.isfinite(result) else result
    except (TypeError, ValueError, IndexError):
        return default


class High52WeekMomentumStrategy:
    meta = StrategyMeta(
        id="high_52w_momentum",
        name="52周新高动量",
        description="寻找接近过去 52 周高点且中期动量为正的股票，使用截面排名合成评分。",
        default_config=DEFAULT_CONFIG,
    )

    def _cfg(self, cfg: dict) -> dict:
        merged = dict(DEFAULT_CONFIG)
        merged.update(cfg or {})
        return merged

    def warmup_bars(self, cfg: dict) -> int:
        cfg = self._cfg(cfg)
        return max(
            int(cfg["high_lookback_days"]),
            int(cfg["momentum_lookback_days"]) + int(cfg["momentum_skip_days"]),
            int(cfg["trend_ma_days"]),
        ) + 5

    def indicator_config(self, cfg: dict) -> dict:
        cfg = self._cfg(cfg)
        keys = {
            "high_lookback_days",
            "momentum_lookback_days",
            "momentum_skip_days",
            "trend_ma_days",
        }
        return {key: cfg[key] for key in sorted(keys)}

    def cache_columns(self, cfg: dict) -> set[str]:
        return {
            "close",
            "turnover_n",
            "high_52w",
            "high_proximity",
            "momentum_return",
            "trend_ma",
            "above_trend_ma",
        }

    def prepare_all(
        self,
        data: Dict[str, pd.DataFrame],
        cfg: dict,
        context: StrategyContext | None = None,
    ) -> Dict[str, pd.DataFrame]:
        cfg = self._cfg(cfg)
        high_window = max(20, int(cfg["high_lookback_days"]))
        momentum_window = max(2, int(cfg["momentum_lookback_days"]))
        skip_days = max(0, int(cfg["momentum_skip_days"]))
        trend_window = max(2, int(cfg["trend_ma_days"]))
        prepared: Dict[str, pd.DataFrame] = {}
        total = len(data)

        logger.info("52周新高动量指标预计算开始：%d 只股票", total)
        for index, (code, frame) in enumerate(data.items(), 1):
            if context and context.cancel_requested and context.cancel_requested():
                raise RunCancelledError("任务已被用户终止")
            try:
                item = frame.copy()
                close = pd.to_numeric(item["close"], errors="coerce")
                high = pd.to_numeric(item["high"], errors="coerce")
                item["high_52w"] = high.rolling(high_window, min_periods=high_window).max()
                item["high_proximity"] = close / item["high_52w"].replace(0, np.nan)
                # 跳过最近一段行情再计算中期收益，可减弱短期反转对动量信号的干扰。
                momentum_end = close.shift(skip_days)
                momentum_start = close.shift(momentum_window + skip_days)
                item["momentum_return"] = momentum_end / momentum_start.replace(0, np.nan) - 1.0
                item["trend_ma"] = close.rolling(trend_window, min_periods=trend_window).mean()
                item["above_trend_ma"] = close >= item["trend_ma"]
                prepared[code] = item
            except Exception as exc:  # noqa: BLE001
                logger.debug("high_52w_momentum prepare failed %s: %s", code, exc)

            if (not context or context.progress_enabled) and (index % 250 == 0 or index == total):
                message = f"52周新高动量指标预计算进度 {index}/{total}，成功 {len(prepared)} 只"
                logger.info(message)
                if context and context.progress_callback:
                    context.progress_callback(message, index, total)
        return prepared

    @staticmethod
    def _add_cross_section_ranks(
        data: Dict[str, pd.DataFrame],
        pick_date: pd.Timestamp,
        pool: set[str] | None,
    ) -> None:
        # rank 是选股日的全市场截面排名，不能在单只股票的时间序列内部计算。
        proximity: dict[str, float] = {}
        momentum: dict[str, float] = {}
        for code, frame in data.items():
            if pool is not None and code not in pool:
                continue
            if pick_date not in frame.index:
                continue
            row = frame.loc[pick_date]
            if isinstance(row, pd.DataFrame):
                row = row.iloc[-1]
            proximity_value = _safe_float(row.get("high_proximity"), np.nan)
            momentum_value = _safe_float(row.get("momentum_return"), np.nan)
            if np.isfinite(proximity_value) and np.isfinite(momentum_value):
                proximity[code] = proximity_value
                momentum[code] = momentum_value

        if not proximity:
            return
        proximity_ranks = pd.Series(proximity).rank(pct=True, method="average")
        momentum_ranks = pd.Series(momentum).rank(pct=True, method="average")
        for code in proximity:
            data[code].loc[pick_date, "high_proximity_rank"] = float(proximity_ranks[code])
            data[code].loc[pick_date, "momentum_rank"] = float(momentum_ranks[code])

    def select(
        self,
        data: Dict[str, pd.DataFrame],
        cfg: dict,
        context: StrategyContext,
    ) -> list[Candidate]:
        cfg = self._cfg(cfg)
        if not cfg.get("enabled", True):
            logger.info("52周新高动量策略已禁用")
            return []
        return self.select_prepared(self.prepare_all(data, cfg, context), cfg, context)

    def select_prepared(
        self,
        data: Dict[str, pd.DataFrame],
        cfg: dict,
        context: StrategyContext,
    ) -> list[Candidate]:
        cfg = self._cfg(cfg)
        if not cfg.get("enabled", True):
            return []

        self._add_cross_section_ranks(data, context.pick_date, context.pool)
        high_weight = max(0.0, float(cfg["high_proximity_weight"]))
        momentum_weight = max(0.0, float(cfg["momentum_weight"]))
        weight_sum = high_weight + momentum_weight
        if weight_sum <= 0:
            high_weight, momentum_weight, weight_sum = 0.6, 0.4, 1.0
        # 前端允许任意非负权重，这里统一归一化，保证最终得分仍落在 0–100。
        high_weight /= weight_sum
        momentum_weight /= weight_sum
        warmup = self.warmup_bars(cfg)
        candidates: list[Candidate] = []

        for code, frame in tqdm(
            data.items(),
            desc="52周新高动量选股",
            unit="只",
            disable=not context.progress_enabled,
        ):
            if context.cancel_requested and context.cancel_requested():
                raise RunCancelledError("任务已被用户终止")
            if context.pool is not None and code not in context.pool:
                continue
            if context.pick_date not in frame.index:
                continue
            history_bars = int(frame.index.searchsorted(context.pick_date, side="right"))
            if history_bars < warmup:
                continue

            row = frame.loc[context.pick_date]
            if isinstance(row, pd.DataFrame):
                row = row.iloc[-1]
            proximity = _safe_float(row.get("high_proximity"), np.nan)
            momentum_return = _safe_float(row.get("momentum_return"), np.nan)
            proximity_rank = _safe_float(row.get("high_proximity_rank"), np.nan)
            momentum_rank = _safe_float(row.get("momentum_rank"), np.nan)
            if not all(np.isfinite(value) for value in [proximity, momentum_return, proximity_rank, momentum_rank]):
                continue
            if proximity < float(cfg["min_high_proximity"]):
                continue
            if momentum_return < float(cfg["min_momentum_return"]):
                continue
            if cfg.get("require_above_trend_ma", True) and not bool(row.get("above_trend_ma", False)):
                continue

            score = (high_weight * proximity_rank + momentum_weight * momentum_rank) * 100.0
            candidates.append(Candidate(
                code=code,
                name=context.names.get(code, code),
                date=str(context.pick_date.date()),
                strategy=self.meta.id,
                close=_safe_float(row.get("close")),
                turnover_n=_safe_float(row.get("turnover_n")),
                score=float(score),
                extra={
                    "high_52w": _safe_float(row.get("high_52w")),
                    "high_proximity": float(proximity),
                    "distance_to_high_pct": float((1.0 - proximity) * 100.0),
                    "momentum_return": float(momentum_return),
                    "high_proximity_rank": float(proximity_rank),
                    "momentum_rank": float(momentum_rank),
                    "trend_ma": _safe_float(row.get("trend_ma")),
                    "high_lookback_days": int(cfg["high_lookback_days"]),
                },
            ))

        candidates.sort(key=lambda item: item.score, reverse=True)
        max_candidates = max(0, int(cfg.get("max_candidates", 0)))
        return candidates[:max_candidates] if max_candidates else candidates
