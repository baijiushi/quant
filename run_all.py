"""
run_all.py
~~~~~~~~~~
量化选股完整流程一键入口

步骤 1  pipeline/fetch_data.py    — 增量拉取 / 更新 K 线数据
步骤 2  pipeline/select_stock.py  — B1 量化初选，生成候选列表 JSON
步骤 3  streamlit run dashboard/app.py — 启动看盘界面

用法：
    python run_all.py                        # 完整流程（增量更新数据 + 选股 + 启动看板）
    python run_all.py --skip-fetch           # 跳过数据下载
    python run_all.py --start-from 2         # 从第 2 步开始
    python run_all.py --no-dashboard         # 选股后不启动看板（仅输出 JSON）
    python run_all.py --pick-date 2026-04-22 # 指定选股日期
"""
from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from pathlib import Path

ROOT   = Path(__file__).resolve().parent
PYTHON = sys.executable

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# =============================================================================
# 工具函数
# =============================================================================

def _run(step_name: str, cmd: list[str]) -> None:
    """运行子进程，失败时终止整个流程。"""
    print(f"\n{'=' * 60}")
    print(f"[步骤] {step_name}")
    print(f"  命令: {' '.join(cmd)}")
    print(f"{'=' * 60}")
    result = subprocess.run(cmd, cwd=str(ROOT))
    if result.returncode != 0:
        print(f"\n[ERROR] 步骤「{step_name}」返回非零退出码 {result.returncode}，流程已中止。")
        sys.exit(result.returncode)


def _print_summary() -> None:
    """读取最新候选文件，打印选股摘要。"""
    import json
    latest_file = ROOT / "data" / "candidates" / "candidates_latest.json"
    if not latest_file.exists():
        print("[WARN] 找不到候选文件，无法打印摘要。")
        return

    with open(latest_file, encoding="utf-8") as f:
        data = json.load(f)

    candidates = data.get("candidates", [])
    pick_date  = data.get("pick_date",  "未知")
    meta       = data.get("meta",       {})

    print(f"\n{'=' * 60}")
    print(f"  选股日期：{pick_date}")
    print(f"  扫描数量：{meta.get('scanned', '-')} 只")
    print(f"  命中数量：{len(candidates)} 只")
    print(f"{'=' * 60}")

    if not candidates:
        print("  暂无符合条件的股票。")
        return

    header = f"{'排名':>4}  {'代码':>8}  {'名称':>8}  {'收盘':>8}  {'J值':>6}  {'均线排列':>6}  {'周线确认':>6}"
    print(header)
    print("-" * len(header))
    for i, c in enumerate(candidates, 1):
        zx  = "Y" if c.get("zx_aligned")     else "N"
        wma = "Y" if c.get("weekly_aligned")  else "N"
        print(f"{i:>4}  {c['code']:>8}  {c.get('name', c['code']):>8}  "
              f"{c.get('close', 0):>8.2f}  {c.get('J', 0):>6.1f}  "
              f"{zx:>6}  {wma:>6}")


def _choose_data_mode(args: argparse.Namespace) -> str:
    """确定本次运行的数据模式。"""
    if getattr(args, "data_mode", None):
        return args.data_mode
    if args.skip_fetch:
        return "existing"
    if args.use_cache_only:
        return "cache-only"

    print(f"\n{'=' * 60}")
    print("[数据模式] 请选择本次运行方式")
    print("  1. 直接使用当前本地数据（不拉新数据）")
    print("  2. 增量更新后再运行（推荐）")
    print("  3. 强制重新拉取全部数据")
    print("  4. 仅使用本地缓存文件运行 fetch（不走网络）")
    print(f"{'=' * 60}")
    try:
        choice = input("请输入选择 (1/2/3/4，默认为2): ").strip()
    except Exception:
        choice = "2"

    return {
        "1": "existing",
        "2": "incremental",
        "3": "refresh",
        "4": "cache-only",
    }.get(choice, "incremental")


# =============================================================================
# 主逻辑
# =============================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="量化选股完整流程一键入口",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "--skip-fetch", action="store_true",
        help="跳过数据下载步骤（已有最新数据时使用）",
    )
    parser.add_argument(
        "--start-from", type=int, default=1, choices=[1, 2, 3],
        help="从第 N 步开始执行（1=拉取数据, 2=选股, 3=看板）",
    )
    parser.add_argument(
        "--no-dashboard", action="store_true",
        help="选股完成后不启动看盘界面",
    )
    parser.add_argument(
        "--pick-date", default=None,
        help="选股基准日期，格式 YYYY-MM-DD（默认最新交易日）",
    )
    parser.add_argument(
        "--use-cache-only", action="store_true",
        help="数据拉取时仅使用本地缓存，不调用网络接口",
    )
    parser.add_argument(
        "--data-mode",
        choices=["existing", "incremental", "refresh", "cache-only"],
        default=None,
        help="数据模式：existing=直接用现有数据，incremental=增量更新，refresh=强制重拉，cache-only=仅用本地缓存",
    )
    args = parser.parse_args()

    data_mode = _choose_data_mode(args)
    start = args.start_from
    if data_mode == "existing" and start == 1:
        start = 2

    # ── 步骤 1：拉取 / 更新数据 ───────────────────────────────────────────────
    if start <= 1:
        fetch_cmd = [PYTHON, "pipeline/fetch_data.py"]
        if data_mode == "cache-only":
            fetch_cmd.append("--use-cache-only")
        elif data_mode == "refresh":
            fetch_cmd.append("--force-refresh")
        _run("步骤 1 / 3  拉取 K 线数据", fetch_cmd)

    # ── 步骤 2：量化初选 ──────────────────────────────────────────────────────
    if start <= 2:
        preselect_cmd = [PYTHON, "pipeline/cli.py", "preselect"]
        if args.pick_date:
            preselect_cmd += ["--pick-date", args.pick_date]
        _run("步骤 2 / 3  B1 量化初选", preselect_cmd)
        _print_summary()

    # ── 步骤 3：启动看盘界面 ──────────────────────────────────────────────────
    if start <= 3 and not args.no_dashboard:
        print(f"\n{'=' * 60}")
        print("[步骤 3 / 3] 启动 Streamlit 看盘界面")
        print("  访问地址：http://localhost:8501")
        print("  按 Ctrl+C 退出看盘界面")
        print(f"{'=' * 60}")
        subprocess.run(
            [PYTHON, "-m", "streamlit", "run", "dashboard/app.py"],
            cwd=str(ROOT),
        )
    elif args.no_dashboard:
        print("\n[INFO] --no-dashboard 已设置，跳过看盘界面启动。")
        print("  可手动运行：streamlit run dashboard/app.py")

    print(f"\n{'=' * 60}")
    print("全部流程执行完毕！")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
