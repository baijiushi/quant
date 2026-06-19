"""Volume contraction new-high strategy."""
from __future__ import annotations

import logging
from typing import Dict

import numpy as np
import pandas as pd
from tqdm import tqdm

from pipeline.schemas import Candidate
from pipeline.cancellation import RunCancelledError
from strategies.base import StrategyContext, StrategyMeta

logger = logging.getLogger(__name__)


DEFAULT_CONFIG = {
    "enabled": True,
    "corr_window": 10,
    "stddev_window": 10,
    "new_high_window": 60,
    "volume_ma_window": 20,
    "max_volume_ratio": 0.85,
    "min_score": 0.0,
}


def _safe_float(val, default: float = 0.0) -> float:
    try:
        if isinstance(val, pd.Series):
            val = val.iloc[-1]
        value = float(val)
        return default if np.isnan(value) else value
    except Exception:
        return default


def _safe_bool(val) -> bool:
    try:
        if isinstance(val, pd.Series):
            val = val.iloc[-1]
        return bool(val) and not (isinstance(val, float) and np.isnan(val))
    except Exception:
        return False


class VolumeNewHighStrategy:
    meta = StrategyMeta(
        id="volume_new_high",
        name="缩量新高",
        description="缩量创阶段新高，并使用 -corr(HIGH,VOLUME,10) * rank(stddev(HIGH,10)) 评分。",
        default_config=DEFAULT_CONFIG,
    )

    def _cfg(self, cfg: dict) -> dict:
        merged = dict(DEFAULT_CONFIG)
        merged.update(cfg or {})
        return merged

    def warmup_bars(self, cfg: dict) -> int:
        cfg = self._cfg(cfg)
        return max(
            int(cfg["corr_window"]),
            int(cfg["stddev_window"]),
            int(cfg["new_high_window"]),
            int(cfg["volume_ma_window"]),
        ) + 5

    def prepare_all(self, data: Dict[str, pd.DataFrame], cfg: dict) -> Dict[str, pd.DataFrame]:
        cfg = self._cfg(cfg)
        prepared: Dict[str, pd.DataFrame] = {}
        corr_window = int(cfg["corr_window"])
        std_window = int(cfg["stddev_window"])
        high_window = int(cfg["new_high_window"])
        volume_window = int(cfg["volume_ma_window"])

        for code, df in data.items():
            try:
                item = df.copy()
                volume = item["volume"] if "volume" in item.columns else pd.Series(0.0, index=item.index)
                item["high_volume_corr"] = item["high"].rolling(corr_window, min_periods=corr_window).corr(volume)
                item["high_stddev"] = item["high"].rolling(std_window, min_periods=std_window).std()
                item["rolling_high"] = item["high"].rolling(high_window, min_periods=high_window).max()
                item["is_new_high"] = item["high"] >= item["rolling_high"]
                item["volume_ma"] = volume.rolling(volume_window, min_periods=1).mean()
                item["volume_ratio"] = (volume / item["volume_ma"].replace(0, np.nan)).fillna(0.0)
                prepared[code] = item
            except Exception as exc:
                logger.debug("volume_new_high prepare failed %s: %s", code, exc)
        return prepared

    def _add_cross_section_rank(
        self,
        data: Dict[str, pd.DataFrame],
        pick_date: pd.Timestamp,
    ) -> None:
        rows: list[tuple[str, float]] = []
        for code, df in data.items():
            if pick_date not in df.index or "high_stddev" not in df.columns:
                continue
            value = df.loc[pick_date, "high_stddev"]
            if isinstance(value, pd.Series):
                value = value.iloc[-1]
            if pd.notna(value):
                rows.append((code, float(value)))
        if not rows:
            return
        values = pd.Series({code: value for code, value in rows})
        ranks = values.rank(pct=True)
        for code, rank_value in ranks.items():
            data[code].loc[pick_date, "high_stddev_rank"] = float(rank_value)

    def select(
        self,
        data: Dict[str, pd.DataFrame],
        cfg: dict,
        context: StrategyContext,
    ) -> list[Candidate]:
        cfg = self._cfg(cfg)
        if not cfg.get("enabled", True):
            logger.info("缩量新高策略已禁用")
            return []

        prepared_data = self.prepare_all(data, cfg)
        self._add_cross_section_rank(prepared_data, context.pick_date)
        warmup = self.warmup_bars(cfg)
        candidates: list[Candidate] = []
        skipped = 0
        processed = 0

        for code, df in tqdm(prepared_data.items(), desc="缩量新高选股", unit="只"):
            if context.cancel_requested and context.cancel_requested():
                raise RunCancelledError("任务已被用户终止")
            processed += 1
            if processed % 250 == 0 or processed == len(prepared_data):
                logger.info("缩量新高进度 %d/%d，当前命中 %d 只，跳过 %d 只", processed, len(prepared_data), len(candidates), skipped)
            if context.pool is not None and code not in context.pool:
                continue
            if len(df) < warmup or context.pick_date not in df.index:
                skipped += 1
                continue
            row = df.loc[context.pick_date]
            if isinstance(row, pd.DataFrame):
                row = row.iloc[-1]

            high_volume_corr = _safe_float(row.get("high_volume_corr"), default=np.nan)
            high_stddev = _safe_float(row.get("high_stddev"), default=np.nan)
            high_stddev_rank = _safe_float(row.get("high_stddev_rank"), default=np.nan)
            volume_ratio = _safe_float(row.get("volume_ratio"), default=np.inf)
            if any(np.isnan(x) for x in [high_volume_corr, high_stddev, high_stddev_rank]):
                skipped += 1
                continue

            score = -high_volume_corr * high_stddev_rank
            passes = (
                _safe_bool(row.get("is_new_high"))
                and volume_ratio <= float(cfg["max_volume_ratio"])
                and score >= float(cfg["min_score"])
            )
            if not passes:
                continue

            candidates.append(Candidate(
                code=code,
                name=context.names.get(code, code),
                date=str(context.pick_date.date()),
                strategy=self.meta.id,
                close=_safe_float(row.get("close")),
                turnover_n=_safe_float(row.get("turnover_n")),
                score=float(score),
                extra={
                    "high_volume_corr": float(high_volume_corr),
                    "high_stddev": float(high_stddev),
                    "high_stddev_rank": float(high_stddev_rank),
                    "volume_ratio": float(volume_ratio),
                    "new_high_window": int(cfg["new_high_window"]),
                    "max_volume_ratio": float(cfg["max_volume_ratio"]),
                    "rolling_high": _safe_float(row.get("rolling_high")),
                },
            ))

        candidates.sort(key=lambda item: item.score, reverse=True)
        return candidates
