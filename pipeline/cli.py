"""
pipeline/cli.py
命令行入口，封装 pipeline 各步骤的调用。

用法：
    python pipeline/cli.py preselect
    python pipeline/cli.py preselect --config config/rules_preselect.yaml
    python pipeline/cli.py preselect --pick-date 2026-04-22
    python pipeline/cli.py preselect --output-dir data/candidates
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# 确保项目根目录和 pipeline 目录都在路径中
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "pipeline"))


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )


def _cmd_preselect(args: argparse.Namespace) -> None:
    from select_stock import run  # noqa: PLC0415
    run(
        config_path=args.config,
        output_dir=args.output_dir,
        pick_date=args.pick_date,
        strategy_id=args.strategy_id,
    )


def main() -> None:
    _setup_logging()

    parser = argparse.ArgumentParser(
        prog="pipeline/cli.py",
        description="量化选股 Pipeline CLI",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ── preselect 子命令 ──────────────────────────────────────────────────────
    p_pre = sub.add_parser("preselect", help="运行量化初选，生成候选股列表")
    p_pre.add_argument("--config",     default=None, help="rules_preselect.yaml 路径")
    p_pre.add_argument("--output-dir", default=None, help="候选结果输出目录")
    p_pre.add_argument("--pick-date",  default=None, help="选股基准日期，格式 YYYY-MM-DD")
    p_pre.add_argument("--strategy-id", default=None, help="策略 ID：b1 或 volume_new_high")
    p_pre.set_defaults(func=_cmd_preselect)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
