"""
pipeline/select_stock.py
量化初选核心业务逻辑

职责：
  1. 读取 rules_preselect.yaml 参数
  2. 通过 pipeline_core 加载本地缓存数据
  3. 流动性过滤（top_m 只）
  4. 运行 B1Selector，收集 Candidate
  5. 通过 io.save_candidates() 写入结果 JSON

用法：
    from select_stock import run
    run()

直接运行：
    python pipeline/select_stock.py
    python pipeline/select_stock.py --pick-date 2026-04-22
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import yaml
from tqdm import tqdm

# 路径处理
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "pipeline"))

from pipeline.schemas import Candidate, CandidateRun       # noqa: E402
from pipeline.Selector import B1Selector                    # noqa: E402
from pipeline.pipeline_core import load_cache_data, build_top_turnover_pool  # noqa: E402
from pipeline.io import save_candidates                     # noqa: E402

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG = _ROOT / "config" / "rules_preselect.yaml"


# =============================================================================
# 配置加载
# =============================================================================

def load_config(config_path: Optional[str] = None) -> dict:
    path = Path(config_path) if config_path else _DEFAULT_CONFIG
    if not path.exists():
        raise FileNotFoundError(f"找不到配置文件: {path}")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# =============================================================================
# 辅助函数
# =============================================================================

def _load_stock_names(cache_dir: str) -> Dict[str, str]:
    """加载股票名称映射（代码 → 名称）。"""
    name_file = Path(cache_dir) / "stock_list.csv"
    if not name_file.exists():
        return {}
    try:
        df = pd.read_csv(name_file, dtype={"代码": str})
        if "代码" in df.columns and "名称" in df.columns:
            return dict(zip(df["代码"], df["名称"]))
    except Exception as e:
        logger.warning("读取股票名称失败: %s", e)
    return {}


def _resolve_pick_date(data: Dict[str, pd.DataFrame]) -> pd.Timestamp:
    """确定选股基准日期（所有股票中最晚的公共可用交易日）。"""
    all_dates = sorted({d for df in data.values() for d in df.index})
    if not all_dates:
        raise ValueError("数据为空，无法确定选股日期")
    return all_dates[-1]


def _safe_float(val, default: float = 0.0) -> float:
    try:
        # loc 在重复索引时可能返回 Series，取最后一个标量
        if isinstance(val, pd.Series):
            val = val.iloc[-1]
        v = float(val)
        return default if np.isnan(v) else v
    except Exception:
        return default


def _safe_bool(val) -> bool:
    try:
        if isinstance(val, pd.Series):
            val = val.iloc[-1]
        return bool(val) and not (isinstance(val, float) and np.isnan(val))
    except Exception:
        return False


# =============================================================================
# 主函数
# =============================================================================

def run(
    config_path: Optional[str] = None,
    output_dir: Optional[str] = None,
    pick_date: Optional[str] = None,
) -> CandidateRun:
    """
    运行 B1 量化初选。

    Args:
        config_path: rules_preselect.yaml 路径（None 使用默认）
        output_dir:  候选结果输出目录（None 使用配置值）
        pick_date:   选股基准日期，格式 "YYYY-MM-DD"（None 使用最新交易日）

    Returns:
        CandidateRun 对象（同时已写入磁盘）
    """
    cfg     = load_config(config_path)
    g_cfg   = cfg.get("global", {})
    b1_cfg  = cfg.get("b1",     {})

    if not b1_cfg.get("enabled", True):
        logger.info("B1 策略已禁用（b1.enabled = false）")
        return CandidateRun(run_date=datetime.now().strftime("%Y-%m-%d"), pick_date="")

    data_dir       = g_cfg.get("data_dir",       "./data/cache")
    out_dir        = output_dir or g_cfg.get("output_dir", "./data/candidates")
    adjust         = g_cfg.get("adjust",          "qfq")
    top_m          = int(g_cfg.get("top_m",        3000))
    n_turn_days    = int(g_cfg.get("n_turnover_days", 43))

    # ── 1. 加载缓存数据 ────────────────────────────────────────────────────────
    logger.info("=== 步骤 1/4  加载本地缓存数据 ===")
    data = load_cache_data(data_dir, adjust=adjust, n_turnover_days=n_turn_days)
    if not data:
        logger.error("未加载到任何数据，请先运行数据拉取步骤")
        return CandidateRun(run_date=datetime.now().strftime("%Y-%m-%d"), pick_date="")

    # ── 2. 确定选股日期 ────────────────────────────────────────────────────────
    logger.info("=== 步骤 2/4  确定选股基准日期 ===")
    if pick_date:
        pd_ts = pd.Timestamp(pick_date)
    else:
        pd_ts = _resolve_pick_date(data)
    logger.info("选股基准日期: %s", pd_ts.strftime("%Y-%m-%d"))

    # ── 3. 流动性过滤 ──────────────────────────────────────────────────────────
    logger.info("=== 步骤 3/4  流动性过滤（top %d）===", top_m)
    pool = build_top_turnover_pool(data, top_m, pd_ts)

    # ── 4. 运行 B1 选股器 ──────────────────────────────────────────────────────
    logger.info("=== 步骤 4/4  运行 B1 选股器 ===")
    selector   = B1Selector(b1_cfg)
    warmup     = selector.warmup_bars()
    names      = _load_stock_names(data_dir)
    candidates: List[Candidate] = []
    skipped    = 0

    for code, df in tqdm(data.items(), desc="B1 选股", unit="只"):
        # 流动性过滤
        if pool is not None and code not in pool:
            continue
        # 数据量不足
        if len(df) < warmup:
            skipped += 1
            continue
        # 选股日期不在数据范围内
        if pd_ts not in df.index:
            skipped += 1
            continue

        try:
            prepared = selector.prepare_df(df)
        except Exception as e:
            logger.debug("prepare_df 失败 %s: %s", code, e)
            skipped += 1
            continue

        if not _safe_bool(prepared.loc[pd_ts, "_vec_pick"]):
            continue

        row = prepared.loc[pd_ts]
        req_weekly = b1_cfg.get("require_weekly_ma_bull", True)

        zx_aligned = (
            _safe_float(row.get("ma14"))  > _safe_float(row.get("ma28"))  and
            _safe_float(row.get("ma28"))  > _safe_float(row.get("ma57"))  and
            _safe_float(row.get("ma57"))  > _safe_float(row.get("ma114"))
        )
        weekly_aligned = (
            _safe_float(row.get("wma_short")) > _safe_float(row.get("wma_mid")) and
            _safe_float(row.get("wma_mid"))   > _safe_float(row.get("wma_long"))
        ) if req_weekly else True

        candidates.append(Candidate(
            code          = code,
            name          = names.get(code, code),
            date          = str(pd_ts.date()),
            strategy      = "b1",
            close         = _safe_float(row.get("close")),
            turnover_n    = _safe_float(row.get("turnover_n")),
            J             = _safe_float(row.get("J")),
            K             = _safe_float(row.get("K")),
            D             = _safe_float(row.get("D")),
            ma14          = _safe_float(row.get("ma14")),
            ma28          = _safe_float(row.get("ma28")),
            ma57          = _safe_float(row.get("ma57")),
            ma114         = _safe_float(row.get("ma114")),
            zx_aligned    = zx_aligned,
            weekly_aligned= weekly_aligned,
        ))

    # 按 J 值升序排列（越小越超卖，排在前面）
    candidates.sort(key=lambda c: c.J)

    # ── 汇总并保存 ─────────────────────────────────────────────────────────────
    scanned = len([c for c in data if pool is None or c in pool])
    run_result = CandidateRun(
        run_date  = datetime.now().strftime("%Y-%m-%d"),
        pick_date = str(pd_ts.date()),
        candidates= candidates,
        meta      = {
            "total_in_cache":  len(data),
            "pool_size":       len(pool) if pool else len(data),
            "scanned":         scanned,
            "skipped":         skipped,
            "selected":        len(candidates),
            "strategy":        "b1",
            "config":          {
                "j_threshold":        b1_cfg.get("j_threshold"),
                "zx_ma":              [b1_cfg.get("zx_m1"), b1_cfg.get("zx_m2"),
                                       b1_cfg.get("zx_m3"), b1_cfg.get("zx_m4")],
                "require_weekly":     b1_cfg.get("require_weekly_ma_bull"),
            },
        },
    )

    save_candidates(run_result, out_dir)

    logger.info("=" * 60)
    logger.info("选股完成！日期: %s", pd_ts.strftime("%Y-%m-%d"))
    logger.info("扫描 %d 只 | 跳过 %d 只 | 命中 %d 只", scanned, skipped, len(candidates))
    logger.info("=" * 60)

    # 打印候选列表摘要
    if candidates:
        print(f"\n{'排名':>4}  {'代码':>8}  {'名称':>8}  {'收盘':>8}  "
              f"{'J值':>6}  {'MA14':>8}  {'MA114':>8}")
        print("-" * 68)
        for i, c in enumerate(candidates, 1):
            print(f"{i:>4}  {c.code:>8}  {c.name:>8}  {c.close:>8.2f}  "
                  f"{c.J:>6.1f}  {c.ma14:>8.2f}  {c.ma114:>8.2f}")
    else:
        print("\n当前日期无符合条件的股票。")

    return run_result


# =============================================================================
# 直接运行入口
# =============================================================================

if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s - %(levelname)s - %(message)s")

    parser = argparse.ArgumentParser(description="运行 B1 量化初选")
    parser.add_argument("--config",     default=None, help="rules_preselect.yaml 路径")
    parser.add_argument("--output-dir", default=None, help="候选结果输出目录")
    parser.add_argument("--pick-date",  default=None, help="选股日期 YYYY-MM-DD")
    args = parser.parse_args()

    run(config_path=args.config, output_dir=args.output_dir, pick_date=args.pick_date)
