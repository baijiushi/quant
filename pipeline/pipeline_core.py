"""
pipeline/pipeline_core.py
数据加载与基础设施层

提供：
  - load_cache_data()         从 data/cache/ 批量加载并规范化 CSV
  - build_top_turnover_pool() 按滚动成交额筛选流动性池
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import numpy as np
import pandas as pd
from tqdm import tqdm

logger = logging.getLogger(__name__)


# =============================================================================
# 内部辅助：DataFrame 规范化
# =============================================================================

def _normalize_df(df: pd.DataFrame) -> Optional[pd.DataFrame]:
    """
    统一列名 / 日期格式，设置 DatetimeIndex，返回规范化后的 DataFrame。
    若关键列缺失则返回 None。
    """
    if df is None or df.empty:
        return None

    result = df.copy()
    result.columns = [c.lower() for c in result.columns]

    # 中文列名映射
    rename_map = {
        "日期": "date", "开盘": "open", "收盘": "close",
        "最高": "high", "最低": "low",  "成交量": "volume",
        "成交额": "amount", "涨跌幅": "pct_chg", "换手率": "turnover",
    }
    result = result.rename(columns=rename_map)

    # 确保 date 列存在
    if "date" not in result.columns:
        if result.index.name == "date":
            result = result.reset_index()
        else:
            result = result.reset_index()
            if "index" in result.columns:
                result = result.rename(columns={"index": "date"})

    required = ["date", "open", "close", "high", "low"]
    if not all(c in result.columns for c in required):
        return None

    result["date"] = pd.to_datetime(result["date"], errors="coerce")
    result = result.dropna(subset=["date"])

    for col in ["open", "close", "high", "low", "volume"]:
        if col in result.columns:
            result[col] = pd.to_numeric(result[col], errors="coerce")

    result = result.dropna(subset=["close", "open", "high", "low"])
    result = result.set_index("date").sort_index()

    # 补充成交额（若缺失则用 (open+close)/2 * volume 估算）
    if "amount" not in result.columns or result["amount"].isna().all() or (result["amount"] == 0).all():
        if "volume" in result.columns:
            result["amount"] = (result["open"] + result["close"]) / 2 * result["volume"].fillna(0)
        else:
            result["amount"] = 0.0
    else:
        result["amount"] = pd.to_numeric(result["amount"], errors="coerce").fillna(0.0)

    return result if not result.empty else None


def _load_one_csv(item: Tuple[str, Path], n_turnover_days: int) -> Tuple[str, pd.DataFrame]:
    """加载单只股票 CSV 并计算 turnover_n。"""
    code, fpath = item
    try:
        df = pd.read_csv(fpath)
        df = _normalize_df(df)
        if df is None:
            return code, pd.DataFrame()
        df["turnover_n"] = df["amount"].rolling(n_turnover_days, min_periods=1).sum()
        return code, df
    except Exception as e:
        logger.debug("加载 %s 失败: %s", code, e)
        return code, pd.DataFrame()


# =============================================================================
# 公开 API
# =============================================================================

def load_cache_data(
    cache_dir: str,
    adjust: str = "qfq",
    symbols: Optional[List[str]] = None,
    n_turnover_days: int = 43,
    max_workers: int = 8,
) -> Dict[str, pd.DataFrame]:
    """
    从缓存目录批量加载股票数据（{code}_{adjust}.csv），
    规范化列名并计算 turnover_n（滚动成交额）。

    Args:
        cache_dir:       缓存目录路径
        adjust:          复权类型，与文件名后缀对应
        symbols:         仅加载指定代码，None 表示全量
        n_turnover_days: 滚动成交额窗口
        max_workers:     并发线程数

    Returns:
        dict[code → DataFrame]，DataFrame 以 DatetimeIndex 排序
    """
    cache_path = Path(cache_dir)
    if not cache_path.exists():
        raise FileNotFoundError(f"缓存目录不存在: {cache_dir}")

    files: Dict[str, Path] = {}
    for f in cache_path.glob(f"*_{adjust}.csv"):
        code = f.stem[: -(len(adjust) + 1)]  # 去掉 _{adjust} 后缀
        if symbols is None or code in symbols:
            files[code] = f

    if not files:
        logger.warning("在 %s 中未找到 *_%s.csv 文件", cache_dir, adjust)
        return {}

    logger.info("发现 %d 只股票缓存文件，开始并发加载...", len(files))

    result: Dict[str, pd.DataFrame] = {}
    items = list(files.items())

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_load_one_csv, item, n_turnover_days): item[0]
                   for item in items}
        for future in tqdm(as_completed(futures), total=len(futures),
                           desc="加载缓存数据", unit="只"):
            code, df = future.result()
            if not df.empty:
                result[code] = df

    logger.info("成功加载 %d / %d 只股票数据", len(result), len(files))
    return result


def build_top_turnover_pool(
    data: Dict[str, pd.DataFrame],
    top_m: int,
    pick_date: pd.Timestamp,
) -> Optional[Set[str]]:
    """
    按 pick_date 当日的 turnover_n 降序排名，返回前 top_m 只股票代码集合。
    若 top_m <= 0 则返回 None（表示不过滤）。
    """
    if top_m <= 0:
        return None

    rows: List[Tuple[str, float]] = []
    for code, df in data.items():
        if pick_date in df.index and "turnover_n" in df.columns:
            val = df.loc[pick_date, "turnover_n"]
            # 索引有重复日期时 loc 返回 Series，取最后一个值转为标量
            if isinstance(val, pd.Series):
                val = val.iloc[-1]
            # pd.Scalar 联合类型包含 complex 导致 Pylance 误报，运行时 val 实际为 float
            turnover: float = float(val) if pd.notna(val) else 0.0  # type: ignore[arg-type]
            rows.append((code, turnover))

    if not rows:
        logger.warning("build_top_turnover_pool: 未找到 %s 当日数据，不过滤", pick_date)
        return None

    rows.sort(key=lambda x: x[1], reverse=True)
    pool: Set[str] = {r[0] for r in rows[:top_m]}
    logger.info("流动性池：选出 %d 只（目标 top %d）", len(pool), top_m)
    return pool
