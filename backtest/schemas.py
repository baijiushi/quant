"""Models shared by the local backtest service and API layer."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List


@dataclass(frozen=True)
class BacktestRequest:
    strategy_id: str
    start_date: str
    end_date: str
    holding_days: int = 5
    holding_periods: List[int] = field(default_factory=list)
    config: Dict[str, Any] = field(default_factory=dict)

    def resolved_holding_periods(self) -> List[int]:
        periods = self.holding_periods or [self.holding_days]
        return sorted({int(value) for value in periods})


@dataclass
class BacktestTrade:
    signal_date: str
    signal_rank: int
    code: str
    name: str
    strategy_id: str
    strategy_score: float
    signal_close: float
    entry_date: str | None = None
    entry_open: float | None = None
    exit_date: str | None = None
    exit_close: float | None = None
    holding_days: int = 0
    final_return_pct: float | None = None
    max_gain_pct: float | None = None
    max_drawdown_pct: float | None = None
    status: str = "pending"
    note: str = ""
    daily_returns: List[Dict[str, Any]] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)
    rank: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class BacktestResult:
    backtest_id: str
    generated_at: str
    request: Dict[str, Any]
    metrics: Dict[str, Any]
    horizon_stats: List[Dict[str, Any]] = field(default_factory=list)
    daily_stats: List[Dict[str, Any]] = field(default_factory=list)
    stock_ranking: List[Dict[str, Any]] = field(default_factory=list)
    trades: List[BacktestTrade] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["trades"] = [trade.to_dict() for trade in self.trades]
        return payload
