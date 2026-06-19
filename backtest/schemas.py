"""Backtest API schemas reserved for future implementation."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class BacktestRequest:
    strategy_id: str
    start_date: str
    end_date: str
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BacktestResult:
    backtest_id: str
    status: str
    metrics: Dict[str, Any] = field(default_factory=dict)

