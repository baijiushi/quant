"""
pipeline/fetch_data.py
数据拉取模块（Pipeline 版本）

复用 data/data_fetcher.py 的增量缓存逻辑，
通过 YAML 配置驱动，支持全量 / 增量 / 仅本地三种模式。

用法：
    python pipeline/fetch_data.py
    python pipeline/fetch_data.py --config config/fetch_data.yaml
    python pipeline/fetch_data.py --use-cache-only
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from data.data_fetcher import AStockDataFetcher  # noqa: E402

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG = _ROOT / "config" / "fetch_data.yaml"


def load_config(config_path: str | None = None) -> dict:
    path = Path(config_path) if config_path else _DEFAULT_CONFIG
    if not path.exists():
        raise FileNotFoundError(f"找不到配置文件: {path}")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def run(
    config_path: str | None = None,
    symbols: list[str] | None = None,
    use_cache_only: bool = False,
) -> None:
    """
    拉取 / 更新股票历史数据。

    Args:
        config_path:    YAML 配置文件路径（None 使用默认）
        symbols:        指定股票代码列表，None 则拉取全部 A 股
        use_cache_only: 仅使用本地缓存，不调用任何网络接口
    """
    cfg = load_config(config_path)
    data_cfg = cfg.get("data", {})

    adjust       = data_cfg.get("adjust",       "qfq")
    history_days = data_cfg.get("history_days", 300)

    end_date   = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=history_days)).strftime("%Y%m%d")

    fetcher = AStockDataFetcher()

    if symbols is None:
        stock_list = fetcher.get_stock_list()
        if stock_list.empty:
            logger.error("获取股票列表失败，中止数据拉取")
            return
        symbols = stock_list["代码"].tolist()

    logger.info("开始拉取 %d 只股票数据（%s ~ %s）...", len(symbols), start_date, end_date)

    fetcher.get_multiple_stocks_history(
        symbols=symbols,
        start_date=start_date,
        end_date=end_date,
        adjust=adjust,
        use_cache_only=use_cache_only,
    )

    logger.info("数据拉取完成")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )
    parser = argparse.ArgumentParser(description="拉取 / 更新 A 股日线数据")
    parser.add_argument("--config", default=None, help="YAML 配置文件路径")
    parser.add_argument("--symbols", nargs="+", default=None, help="指定股票代码列表")
    parser.add_argument("--use-cache-only", action="store_true",
                        help="仅使用本地缓存，不调用网络接口")
    args = parser.parse_args()

    run(config_path=args.config, symbols=args.symbols, use_cache_only=args.use_cache_only)
