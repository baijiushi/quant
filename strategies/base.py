"""Shared strategy interfaces."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, Optional, Protocol, Set

import pandas as pd

from pipeline.schemas import Candidate


@dataclass(frozen=True)
class StrategyMeta:
    id: str
    name: str
    description: str
    default_config: dict


@dataclass
class StrategyContext:
    pick_date: pd.Timestamp
    names: Dict[str, str]
    pool: Optional[Set[str]] = None
    markets: list[str] = field(default_factory=list)
    cancel_requested: Optional[Callable[[], bool]] = None


class BaseStrategy(Protocol):
    meta: StrategyMeta

    def warmup_bars(self, cfg: dict) -> int:
        ...

    def prepare_all(self, data: Dict[str, pd.DataFrame], cfg: dict) -> Dict[str, pd.DataFrame]:
        ...

    def select(
        self,
        data: Dict[str, pd.DataFrame],
        cfg: dict,
        context: StrategyContext,
    ) -> list[Candidate]:
        ...
