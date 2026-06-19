"""Shared schema objects for strategy runs."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List


@dataclass
class Candidate:
    """Generic candidate emitted by any stock-selection strategy."""
    code: str
    name: str
    date: str
    strategy: str
    close: float
    turnover_n: float
    score: float = 0.0
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if not d["extra"]:
            d.pop("extra")
        return d


@dataclass
class CandidateRun:
    """A complete strategy run result."""
    run_date: str
    pick_date: str
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
        candidates: List[Candidate] = []
        for item in d.get("candidates", []):
            payload = {k: v for k, v in item.items() if k in fields}
            extra = dict(item.get("extra") or {})
            # Backward compatibility for old B1 candidate JSON files.
            for key in ["J", "K", "D", "ma14", "ma28", "ma57", "ma114", "zx_aligned", "weekly_aligned"]:
                if key in item and key not in extra:
                    extra[key] = item[key]
            payload.setdefault("score", float(extra.get("score", item.get("J", 0.0)) or 0.0))
            payload["extra"] = extra
            candidates.append(Candidate(**payload))
        return cls(
            run_date=d.get("run_date", ""),
            pick_date=d.get("pick_date", ""),
            candidates=candidates,
            meta=d.get("meta", {}),
        )
