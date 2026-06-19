"""OpenBB-style local strategy registry."""
from __future__ import annotations

from typing import Dict

from strategies.base import BaseStrategy, StrategyMeta
from strategies.b1.strategy import B1Strategy
from strategies.volume_new_high.strategy import VolumeNewHighStrategy


_STRATEGIES: Dict[str, BaseStrategy] = {
    "b1": B1Strategy(),
    "volume_new_high": VolumeNewHighStrategy(),
}


def get_strategy(strategy_id: str) -> BaseStrategy:
    try:
        return _STRATEGIES[strategy_id]
    except KeyError as exc:
        available = ", ".join(sorted(_STRATEGIES))
        raise ValueError(f"unknown strategy_id: {strategy_id}; available: {available}") from exc


def list_strategies() -> list[StrategyMeta]:
    return [strategy.meta for strategy in _STRATEGIES.values()]


def default_strategy_configs() -> dict:
    return {strategy.meta.id: dict(strategy.meta.default_config) for strategy in _STRATEGIES.values()}

