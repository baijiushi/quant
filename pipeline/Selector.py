"""
pipeline/Selector.py
B1 选股器 — KDJ 超卖 + 知行均线多头排列 + 周线多头排列

设计原则：
  - Numba JIT 加速 KDJ 递推（若未安装 Numba 则自动降级为纯 Python）
  - prepare_df()  预计算所有指标列，返回含 _vec_pick 布尔列的 DataFrame
  - passes_on_date()  判断某日是否通过选股条件
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# =============================================================================
# Numba 加速（可选）
# =============================================================================
try:
    from numba import njit as _njit
    _HAS_NUMBA = True
except ImportError:
    _HAS_NUMBA = False
    logger.info("未安装 Numba，KDJ 使用纯 Python 实现（速度较慢但结果一致）")

    def _njit(*args, **kwargs):  # type: ignore[misc]
        """Numba 不可用时的透明装饰器。"""
        if args and callable(args[0]):
            return args[0]
        return lambda f: f


# ── KDJ 核心递推 ──────────────────────────────────────────────────────────────
@_njit(cache=True)
def _kdj_core(rsv: np.ndarray) -> tuple:  # noqa: UP006
    n = len(rsv)
    K = np.empty(n, dtype=np.float64)
    D = np.empty(n, dtype=np.float64)
    K[0] = D[0] = 50.0
    for i in range(1, n):
        K[i] = 2.0 / 3.0 * K[i - 1] + 1.0 / 3.0 * rsv[i]
        D[i] = 2.0 / 3.0 * D[i - 1] + 1.0 / 3.0 * K[i]
    J = 3.0 * K - 2.0 * D
    return K, D, J


# =============================================================================
# 指标计算函数
# =============================================================================

def compute_kdj(df: pd.DataFrame, n: int = 9) -> pd.DataFrame:
    """计算 KDJ 指标（Numba 加速）。"""
    if df.empty:
        return df.assign(K=np.nan, D=np.nan, J=np.nan)
    low_n  = df["low"].rolling(n, min_periods=1).min()
    high_n = df["high"].rolling(n, min_periods=1).max()
    rsv    = ((df["close"] - low_n) / (high_n - low_n + 1e-9) * 100).to_numpy(dtype=np.float64)
    K, D, J = _kdj_core(rsv)
    return df.assign(K=K, D=D, J=J)


def compute_zx_ma(df: pd.DataFrame, m1: int = 14, m2: int = 28,
                  m3: int = 57, m4: int = 114) -> pd.DataFrame:
    """计算知行均线（日线 SMA）。"""
    c = df["close"]
    return df.assign(
        ma14 =c.rolling(m1,  min_periods=1).mean(),
        ma28 =c.rolling(m2,  min_periods=1).mean(),
        ma57 =c.rolling(m3,  min_periods=1).mean(),
        ma114=c.rolling(m4,  min_periods=1).mean(),
    )


def compute_weekly_ma(df: pd.DataFrame,
                      short: int = 5, mid: int = 10, long_: int = 20) -> pd.DataFrame:
    """
    基于日线数据计算周线均线，forward-fill 回日线粒度。
    日线 → resample 为周线（取每周最后收盘价）→ 滚动 SMA → ffill 回日线。
    """
    if df.empty:
        return df.assign(wma_short=np.nan, wma_mid=np.nan, wma_long=np.nan)

    idx = df.index if isinstance(df.index, pd.DatetimeIndex) else pd.to_datetime(df.index)
    close_s = pd.Series(df["close"].values, index=idx, name="close")

    w_close = close_s.resample("W").last().dropna()
    if w_close.empty:
        return df.assign(wma_short=np.nan, wma_mid=np.nan, wma_long=np.nan)

    wma_s = w_close.rolling(short,  min_periods=1).mean().reindex(idx, method="ffill")
    wma_m = w_close.rolling(mid,    min_periods=1).mean().reindex(idx, method="ffill")
    wma_l = w_close.rolling(long_,  min_periods=1).mean().reindex(idx, method="ffill")

    return df.assign(
        wma_short=wma_s.values,
        wma_mid  =wma_m.values,
        wma_long =wma_l.values,
    )


def compute_macd(
    df: pd.DataFrame,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> pd.DataFrame:
    """计算 MACD 指标。"""
    if df.empty:
        return df.assign(macd_dif=np.nan, macd_dea=np.nan, macd_hist=np.nan)

    close = df["close"]
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    hist = (dif - dea) * 2
    return df.assign(macd_dif=dif, macd_dea=dea, macd_hist=hist)


def compute_volume_ratio(df: pd.DataFrame, window: int = 20) -> pd.DataFrame:
    """计算当日成交量相对均量倍数。"""
    if df.empty or "volume" not in df.columns:
        return df.assign(volume_ma=np.nan, volume_ratio=np.nan)

    volume_ma = df["volume"].rolling(window, min_periods=1).mean()
    volume_ratio = df["volume"] / volume_ma.replace(0, np.nan)
    return df.assign(volume_ma=volume_ma, volume_ratio=volume_ratio.fillna(0.0))


# =============================================================================
# B1 选股器
# =============================================================================

class B1Selector:
    """
    B1 策略选股器

    三个条件全部满足才通过：
      1. KDJ J 值 < j_threshold（默认 15）            —— 超卖
      2. 日线知行均线多头排列（MA14 > MA28 > MA57 > MA114）—— 趋势向上
      3. 周线均线多头排列（WMA5 > WMA10 > WMA20）        —— 中期趋势确认
    """

    def __init__(self, cfg: dict):
        self.kdj_n       = int(cfg.get("kdj_period",           9))
        self.m1          = int(cfg.get("zx_m1",               14))
        self.m2          = int(cfg.get("zx_m2",               28))
        self.m3          = int(cfg.get("zx_m3",               57))
        self.m4          = int(cfg.get("zx_m4",              114))
        self.j_threshold = float(cfg.get("j_threshold",      15.0))
        self.req_weekly  = bool(cfg.get("require_weekly_ma_bull", True))
        self.wma_s       = int(cfg.get("wma_short",             5))
        self.wma_m       = int(cfg.get("wma_mid",              10))
        self.wma_l       = int(cfg.get("wma_long",             20))
        self.req_macd    = bool(cfg.get("require_macd_bull", False))
        self.macd_fast   = int(cfg.get("macd_fast", 12))
        self.macd_slow   = int(cfg.get("macd_slow", 26))
        self.macd_signal = int(cfg.get("macd_signal", 9))
        self.req_volume  = bool(cfg.get("require_volume_ratio", False))
        self.volume_win  = int(cfg.get("volume_ma_window", 20))
        self.min_vol_ratio = float(cfg.get("min_volume_ratio", 1.2))

    def warmup_bars(self) -> int:
        """运行策略所需的最少历史 bar 数（含预热期）。"""
        # 最长均线 + 周线最长均线对应日线数 + 缓冲
        return max(self.m4 + self.wma_l * 5, self.macd_slow + self.macd_signal, self.volume_win) + 30

    def prepare_df(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        预计算所有指标，并添加 _vec_pick 布尔列。
        所有条件均通过向量化计算，无 Python 循环。
        """
        df = compute_kdj(df, n=self.kdj_n)
        df = compute_zx_ma(df, self.m1, self.m2, self.m3, self.m4)
        if self.req_weekly:
            df = compute_weekly_ma(df, self.wma_s, self.wma_m, self.wma_l)
        if self.req_macd:
            df = compute_macd(df, self.macd_fast, self.macd_slow, self.macd_signal)
        if self.req_volume:
            df = compute_volume_ratio(df, self.volume_win)

        # 条件 1：KDJ J 值超卖
        j_cond = df["J"] < self.j_threshold

        # 条件 2：知行均线多头排列
        zx_cond = (
            (df["ma14"]  > df["ma28"])  &
            (df["ma28"]  > df["ma57"])  &
            (df["ma57"]  > df["ma114"])
        )

        # 条件 3：周线均线多头排列
        if self.req_weekly:
            wma_cond = (
                (df["wma_short"] > df["wma_mid"]) &
                (df["wma_mid"]   > df["wma_long"])
            )
        else:
            wma_cond = pd.Series(True, index=df.index)

        if self.req_macd:
            macd_cond = (df["macd_dif"] > df["macd_dea"]) & (df["macd_hist"] > 0)
        else:
            macd_cond = pd.Series(True, index=df.index)

        if self.req_volume:
            volume_cond = df["volume_ratio"] >= self.min_vol_ratio
        else:
            volume_cond = pd.Series(True, index=df.index)

        df["_vec_pick"] = j_cond & zx_cond & wma_cond & macd_cond & volume_cond
        return df

    def passes_on_date(self, df: pd.DataFrame, date: pd.Timestamp) -> bool:
        """判断某日是否通过选股条件（单日点查）。"""
        if "_vec_pick" not in df.columns:
            df = self.prepare_df(df)
        if date not in df.index:
            return False
        return bool(df.loc[date, "_vec_pick"])
