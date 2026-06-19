"""Strategy orchestration for stock selection."""
from __future__ import annotations

import logging
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

import pandas as pd
import yaml

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from pipeline.io import save_candidates  # noqa: E402
from pipeline.cancellation import raise_if_cancelled  # noqa: E402
from pipeline.pipeline_core import build_top_turnover_pool  # noqa: E402
from pipeline.providers import LocalCsvProvider  # noqa: E402
from pipeline.schemas import CandidateRun  # noqa: E402
from strategies.base import StrategyContext  # noqa: E402
from strategies.registry import default_strategy_configs, get_strategy  # noqa: E402

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG = _ROOT / "config" / "rules_preselect.yaml"
_ALL_MARKETS = {"main", "gem", "star", "bse"}


def load_config(config_path: Optional[str] = None) -> dict:
    path = Path(config_path) if config_path else _DEFAULT_CONFIG
    if not path.is_absolute():
        path = _ROOT / path
    if not path.exists():
        raise FileNotFoundError(f"找不到配置文件: {path}")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def normalize_strategy_config(cfg: dict) -> dict:
    """Upgrade old b1-only config shape to the multi-strategy shape."""
    result = dict(cfg or {})
    defaults = default_strategy_configs()
    strategies = dict(result.get("strategies") or {})
    for strategy_id, default_cfg in defaults.items():
        merged = dict(default_cfg)
        if strategy_id == "b1" and result.get("b1"):
            merged.update(result.get("b1") or {})
        merged.update(strategies.get(strategy_id) or {})
        strategies[strategy_id] = merged
    result["strategies"] = strategies
    result["active_strategy"] = result.get("active_strategy") or "b1"
    result.setdefault("global", {})
    return result


def _resolve_project_path(path_like: str) -> Path:
    path = Path(path_like)
    return path if path.is_absolute() else (_ROOT / path)


def _load_stock_names(stock_list_file: str) -> Dict[str, str]:
    name_file = _resolve_project_path(stock_list_file)
    if not name_file.exists():
        return {}
    try:
        df = pd.read_csv(name_file, dtype={"代码": str})
        if "代码" in df.columns and "名称" in df.columns:
            return dict(zip(df["代码"], df["名称"]))
    except Exception as exc:
        logger.warning("读取股票名称失败: %s", exc)
    return {}


def _resolve_pick_date(data: Dict[str, pd.DataFrame]) -> pd.Timestamp:
    all_dates = sorted({d for df in data.values() for d in df.index})
    if not all_dates:
        raise ValueError("数据为空，无法确定选股日期")
    return all_dates[-1]


def _market_of_code(code: str) -> str:
    code = str(code).zfill(6)
    if code.startswith(("300", "301")):
        return "gem"
    if code.startswith(("688", "689")):
        return "star"
    if code.startswith(("4", "8", "920")):
        return "bse"
    return "main"


def _filter_data_by_markets(
    data: Dict[str, pd.DataFrame],
    markets: list[str] | None,
) -> Dict[str, pd.DataFrame]:
    selected = set(markets or _ALL_MARKETS)
    if not selected or selected == _ALL_MARKETS:
        return data
    return {code: df for code, df in data.items() if _market_of_code(code) in selected}


def run(
    config_path: Optional[str] = None,
    output_dir: Optional[str] = None,
    pick_date: Optional[str] = None,
    strategy_id: Optional[str] = None,
    stop_event: threading.Event | None = None,
) -> CandidateRun:
    cfg = normalize_strategy_config(load_config(config_path))
    g_cfg = cfg.get("global", {})
    active_strategy = strategy_id or cfg.get("active_strategy") or "b1"
    strategy = get_strategy(active_strategy)
    strategy_cfg = cfg["strategies"].get(active_strategy, {})

    data_dir = _resolve_project_path(g_cfg.get("data_dir", "data/raw"))
    out_dir = _resolve_project_path(output_dir or g_cfg.get("output_dir", "data/candidates"))
    stock_list_file = g_cfg.get("stock_list_file", "data/stocklist.csv")
    adjust = g_cfg.get("adjust", "qfq")
    top_m = int(g_cfg.get("top_m", 3000))
    n_turn_days = int(g_cfg.get("n_turnover_days", 43))
    markets = list(g_cfg.get("markets", list(_ALL_MARKETS)) or list(_ALL_MARKETS))

    logger.info("=== 步骤 1/4  加载本地标准 OHLCV 数据 ===")
    provider = LocalCsvProvider(str(data_dir), adjust=adjust, n_turnover_days=n_turn_days, stop_event=stop_event)
    data = provider.load()
    if not data:
        logger.error("未加载到任何数据，请先运行数据拉取步骤")
        return CandidateRun(run_date=datetime.now().strftime("%Y-%m-%d"), pick_date="", meta={"strategy": active_strategy})

    raise_if_cancelled(stop_event)
    data = _filter_data_by_markets(data, markets)
    logger.info("板块过滤后剩余 %d 只，markets=%s", len(data), markets)
    if not data:
        logger.error("板块过滤后无可用股票，请调整 markets 配置")
        return CandidateRun(run_date=datetime.now().strftime("%Y-%m-%d"), pick_date="", meta={"strategy": active_strategy})

    logger.info("=== 步骤 2/4  确定选股基准日期 ===")
    pd_ts = pd.Timestamp(pick_date) if pick_date else _resolve_pick_date(data)
    logger.info("选股基准日期: %s", pd_ts.strftime("%Y-%m-%d"))

    logger.info("=== 步骤 3/4  流动性过滤（top %d）===", top_m)
    pool = build_top_turnover_pool(data, top_m, pd_ts, stop_event=stop_event)

    logger.info("=== 步骤 4/4  运行策略：%s ===", strategy.meta.name)
    names = _load_stock_names(stock_list_file)
    context = StrategyContext(
        pick_date=pd_ts,
        names=names,
        pool=pool,
        markets=markets,
        cancel_requested=(lambda: bool(stop_event and stop_event.is_set())),
    )
    candidates = strategy.select(data, strategy_cfg, context)
    for candidate in candidates:
        candidate.extra.setdefault("market", _market_of_code(candidate.code))

    scanned = len([code for code in data if pool is None or code in pool])
    run_result = CandidateRun(
        run_date=datetime.now().strftime("%Y-%m-%d"),
        pick_date=str(pd_ts.date()),
        candidates=candidates,
        meta={
            "total_in_cache": len(data),
            "pool_size": len(pool) if pool else len(data),
            "scanned": scanned,
            "selected": len(candidates),
            "strategy": active_strategy,
            "strategy_name": strategy.meta.name,
            "provider": "local_csv",
            "config": {
                "global": {
                    "top_m": top_m,
                    "n_turnover_days": n_turn_days,
                    "markets": markets,
                    "adjust": adjust,
                },
                "strategy": strategy_cfg,
            },
        },
    )

    save_candidates(run_result, out_dir)

    logger.info("=" * 60)
    logger.info("选股完成！策略: %s | 日期: %s", strategy.meta.name, pd_ts.strftime("%Y-%m-%d"))
    logger.info("扫描 %d 只 | 命中 %d 只", scanned, len(candidates))
    logger.info("=" * 60)

    if candidates:
        print(f"\n{'排名':>4}  {'策略':>14}  {'代码':>8}  {'名称':>8}  {'收盘':>8}  {'Score':>10}")
        print("-" * 66)
        for i, candidate in enumerate(candidates, 1):
            print(
                f"{i:>4}  {candidate.strategy:>14}  {candidate.code:>8}  "
                f"{candidate.name:>8}  {candidate.close:>8.2f}  {candidate.score:>10.4f}"
            )
    else:
        print("\n当前日期无符合条件的股票。")

    return run_result


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

    parser = argparse.ArgumentParser(description="运行量化策略初选")
    parser.add_argument("--config", default=None, help="rules_preselect.yaml 路径")
    parser.add_argument("--output-dir", default=None, help="候选结果输出目录")
    parser.add_argument("--pick-date", default=None, help="选股日期 YYYY-MM-DD")
    parser.add_argument("--strategy-id", default=None, help="策略 ID，默认读取 active_strategy")
    args = parser.parse_args()

    run(config_path=args.config, output_dir=args.output_dir, pick_date=args.pick_date, strategy_id=args.strategy_id)
