"""
pipeline/schemas.py
候选股票的数据结构定义（纯 dataclass，无第三方依赖）。
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


@dataclass
class Candidate:
    """单只候选股票的结构化信息。"""
    code: str               # 股票代码，如 "000001"
    name: str               # 股票名称
    date: str               # 选股日期，ISO 格式 "YYYY-MM-DD"
    strategy: str           # 来源策略，如 "b1"
    close: float            # 选股日收盘价
    turnover_n: float       # 滚动成交额（流动性代理）

    # KDJ 指标
    J: float
    K: float
    D: float

    # 知行均线
    ma14: float
    ma28: float
    ma57: float
    ma114: float

    # 条件命中情况
    zx_aligned: bool        # 知行均线是否多头排列
    weekly_aligned: bool    # 周线均线是否多头排列

    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if not d["extra"]:
            d.pop("extra")
        return d


@dataclass
class CandidateRun:
    """一次完整初选运行的结果，写入 candidates_YYYY-MM-DD.json。"""
    run_date: str                          # 运行日期（ISO）
    pick_date: str                         # 选股基准日期（ISO）
    candidates: List[Candidate] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_date": self.run_date,
            "pick_date": self.pick_date,
            "candidates": [c.to_dict() for c in self.candidates],
            "meta": self.meta,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CandidateRun":
        fields = Candidate.__dataclass_fields__
        candidates = [
            Candidate(**{k: v for k, v in c.items() if k in fields})
            for c in d.get("candidates", [])
        ]
        return cls(
            run_date=d.get("run_date", ""),
            pick_date=d.get("pick_date", ""),
            candidates=candidates,
            meta=d.get("meta", {}),
        )
