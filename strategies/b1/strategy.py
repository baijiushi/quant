"""B1 stock-selection strategy."""
from __future__ import annotations

import logging
from typing import Dict

import numpy as np
import pandas as pd
from tqdm import tqdm

from pipeline.schemas import Candidate
from pipeline.cancellation import RunCancelledError
from pipeline.Selector import (
    compute_kdj,
    compute_macd,
    compute_volume_ratio,
    compute_weekly_ma,
    compute_zx_ma,
)
from strategies.base import StrategyContext, StrategyMeta

logger = logging.getLogger(__name__)


DEFAULT_CONFIG = {
    "enabled": True,
    "kdj_period": 9,
    "j_threshold": 10,
    "zx_m1": 14,
    "zx_m2": 28,
    "zx_m3": 57,
    "zx_m4": 114,
    "require_weekly_ma_bull": True,
    "wma_short": 5,
    "wma_mid": 10,
    "wma_long": 20,
    "require_macd_bull": True,
    "macd_fast": 12,
    "macd_slow": 26,
    "macd_signal": 9,
    "require_volume_ratio": False,
    "volume_ma_window": 20,
    "min_volume_ratio": 1.2,
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


class B1Strategy:
    meta = StrategyMeta(
        id="b1",
        name="B1 战法",
        description="KDJ 超卖 + 日线/周线多头排列，可选 MACD 与成交量过滤。",
        default_config=DEFAULT_CONFIG,
    )

    def _cfg(self, cfg: dict) -> dict:
        merged = dict(DEFAULT_CONFIG)
        merged.update(cfg or {})
        return merged

    def warmup_bars(self, cfg: dict) -> int:
        cfg = self._cfg(cfg)
        m4 = int(cfg.get("zx_m4", 114))
        wma_l = int(cfg.get("wma_long", 20))
        macd_slow = int(cfg.get("macd_slow", 26))
        macd_signal = int(cfg.get("macd_signal", 9))
        volume_win = int(cfg.get("volume_ma_window", 20))
        return max(m4 + wma_l * 5, macd_slow + macd_signal, volume_win) + 30

    def prepare_all(self, data: Dict[str, pd.DataFrame], cfg: dict) -> Dict[str, pd.DataFrame]:
        cfg = self._cfg(cfg)
        prepared: Dict[str, pd.DataFrame] = {}
        for code, df in data.items():
            try:
                item = compute_kdj(df, int(cfg["kdj_period"]))
                item = compute_zx_ma(item, int(cfg["zx_m1"]), int(cfg["zx_m2"]), int(cfg["zx_m3"]), int(cfg["zx_m4"]))
                if cfg.get("require_weekly_ma_bull", True):
                    item = compute_weekly_ma(item, int(cfg["wma_short"]), int(cfg["wma_mid"]), int(cfg["wma_long"]))
                if cfg.get("require_macd_bull", False):
                    item = compute_macd(item, int(cfg["macd_fast"]), int(cfg["macd_slow"]), int(cfg["macd_signal"]))
                if cfg.get("require_volume_ratio", False):
                    item = compute_volume_ratio(item, int(cfg["volume_ma_window"]))
                prepared[code] = item
            except Exception as exc:
                logger.debug("B1 prepare failed %s: %s", code, exc)
        return prepared

    def _passes(self, row: pd.Series, cfg: dict) -> bool:
        j_cond = _safe_float(row.get("J")) < float(cfg["j_threshold"])
        zx_cond = (
            _safe_float(row.get("ma14")) > _safe_float(row.get("ma28"))
            and _safe_float(row.get("ma28")) > _safe_float(row.get("ma57"))
            and _safe_float(row.get("ma57")) > _safe_float(row.get("ma114"))
        )
        if cfg.get("require_weekly_ma_bull", True):
            weekly_cond = (
                _safe_float(row.get("wma_short")) > _safe_float(row.get("wma_mid"))
                and _safe_float(row.get("wma_mid")) > _safe_float(row.get("wma_long"))
            )
        else:
            weekly_cond = True
        if cfg.get("require_macd_bull", False):
            macd_cond = _safe_float(row.get("macd_dif")) > _safe_float(row.get("macd_dea")) and _safe_float(row.get("macd_hist")) > 0
        else:
            macd_cond = True
        if cfg.get("require_volume_ratio", False):
            volume_cond = _safe_float(row.get("volume_ratio")) >= float(cfg.get("min_volume_ratio", 1.2))
        else:
            volume_cond = True
        return bool(j_cond and zx_cond and weekly_cond and macd_cond and volume_cond)

    def select(
        self,
        data: Dict[str, pd.DataFrame],
        cfg: dict,
        context: StrategyContext,
    ) -> list[Candidate]:
        cfg = self._cfg(cfg)
        if not cfg.get("enabled", True):
            logger.info("B1 策略已禁用")
            return []

        warmup = self.warmup_bars(cfg)
        prepared_data = self.prepare_all(data, cfg)
        candidates: list[Candidate] = []
        skipped = 0
        processed = 0

        for code, df in tqdm(prepared_data.items(), desc="B1 选股", unit="只"):
            if context.cancel_requested and context.cancel_requested():
                raise RunCancelledError("任务已被用户终止")
            processed += 1
            if processed % 250 == 0 or processed == len(prepared_data):
                logger.info("B1选股进度 %d/%d，当前命中 %d 只，跳过 %d 只", processed, len(prepared_data), len(candidates), skipped)
            if context.pool is not None and code not in context.pool:
                continue
            if len(df) < warmup or context.pick_date not in df.index:
                skipped += 1
                continue

            row = df.loc[context.pick_date]
            if isinstance(row, pd.DataFrame):
                row = row.iloc[-1]
            if not self._passes(row, cfg):
                continue

            zx_aligned = (
                _safe_float(row.get("ma14")) > _safe_float(row.get("ma28"))
                and _safe_float(row.get("ma28")) > _safe_float(row.get("ma57"))
                and _safe_float(row.get("ma57")) > _safe_float(row.get("ma114"))
            )
            weekly_aligned = (
                _safe_float(row.get("wma_short")) > _safe_float(row.get("wma_mid"))
                and _safe_float(row.get("wma_mid")) > _safe_float(row.get("wma_long"))
            ) if cfg.get("require_weekly_ma_bull", True) else True
            macd_bull = (
                _safe_float(row.get("macd_dif")) > _safe_float(row.get("macd_dea"))
                and _safe_float(row.get("macd_hist")) > 0
            ) if cfg.get("require_macd_bull", False) else True
            volume_ok = (
                _safe_float(row.get("volume_ratio")) >= float(cfg.get("min_volume_ratio", 1.2))
            ) if cfg.get("require_volume_ratio", False) else True
            j_value = _safe_float(row.get("J"))

            candidates.append(Candidate(
                code=code,
                name=context.names.get(code, code),
                date=str(context.pick_date.date()),
                strategy=self.meta.id,
                close=_safe_float(row.get("close")),
                turnover_n=_safe_float(row.get("turnover_n")),
                score=-j_value,
                extra={
                    "J": j_value,
                    "K": _safe_float(row.get("K")),
                    "D": _safe_float(row.get("D")),
                    "ma14": _safe_float(row.get("ma14")),
                    "ma28": _safe_float(row.get("ma28")),
                    "ma57": _safe_float(row.get("ma57")),
                    "ma114": _safe_float(row.get("ma114")),
                    "zx_aligned": zx_aligned,
                    "weekly_aligned": weekly_aligned,
                    "macd_dif": _safe_float(row.get("macd_dif")),
                    "macd_dea": _safe_float(row.get("macd_dea")),
                    "macd_hist": _safe_float(row.get("macd_hist")),
                    "macd_bull": macd_bull,
                    "volume_ratio": _safe_float(row.get("volume_ratio")),
                    "volume_ok": volume_ok,
                },
            ))

        candidates.sort(key=lambda item: item.score, reverse=True)
        return candidates
