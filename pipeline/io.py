"""
pipeline/io.py
候选结果的 JSON 序列化 / 反序列化，以及文件管理。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from pipeline.schemas import CandidateRun

logger = logging.getLogger(__name__)


def save_candidates(run: CandidateRun, output_dir: str) -> Path:
    """
    将 CandidateRun 保存为两个文件：
      - candidates_{pick_date}.json  （带日期，便于历史追溯）
      - candidates_latest.json        （固定名称，供 dashboard 读取）

    Returns:
        Path: 带日期的文件路径
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    payload = json.dumps(run.to_dict(), ensure_ascii=False, indent=2, default=str)

    dated_file = out / f"candidates_{run.pick_date}.json"
    dated_file.write_text(payload, encoding="utf-8")

    latest_file = out / "candidates_latest.json"
    latest_file.write_text(payload, encoding="utf-8")

    logger.info("候选结果已保存：%s（共 %d 只）", dated_file, len(run.candidates))
    return dated_file


def load_candidates(json_file: str) -> CandidateRun:
    """从 JSON 文件加载 CandidateRun。"""
    p = Path(json_file)
    if not p.exists():
        raise FileNotFoundError(f"找不到候选文件: {json_file}")
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    return CandidateRun.from_dict(data)
