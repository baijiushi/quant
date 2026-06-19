"""Data provider layer for standardized OHLCV data."""
from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
import threading
from typing import Dict, Optional

import pandas as pd

from pipeline.pipeline_core import load_price_data

STANDARD_PRICE_COLUMNS = ["open", "high", "low", "close", "volume", "amount", "turnover", "turnover_n"]
logger = logging.getLogger(__name__)

_PRICE_CACHE_LOCK = threading.Lock()
_PRICE_CACHE: dict[tuple[object, ...], Dict[str, pd.DataFrame]] = {}
_PRICE_CACHE_MAX_ENTRIES = 3


def _data_dir_signature(data_dir: str, adjust: str, symbols: Optional[list[str]]) -> tuple[object, ...]:
    data_path = Path(data_dir).resolve()
    wanted = set(symbols or [])
    count = 0
    latest_mtime_ns = 0
    total_size = 0
    for path in data_path.glob(f"*_{adjust}.csv"):
        code = path.stem[: -(len(adjust) + 1)]
        if wanted and code not in wanted:
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        count += 1
        latest_mtime_ns = max(latest_mtime_ns, stat.st_mtime_ns)
        total_size += stat.st_size
    symbol_key = tuple(sorted(wanted)) if wanted else None
    return (str(data_path), adjust, symbol_key, count, latest_mtime_ns, total_size)


def clear_price_cache() -> None:
    """Clear process-local OHLCV cache, mainly useful after manual data maintenance."""
    with _PRICE_CACHE_LOCK:
        _PRICE_CACHE.clear()


@dataclass(frozen=True)
class LocalCsvProvider:
    """Read standardized A-share OHLCV data from local CSV cache."""
    data_dir: str
    adjust: str = "qfq"
    n_turnover_days: int = 43
    max_workers: int = 8
    stop_event: threading.Event | None = None

    id: str = "local_csv"

    def load(self, symbols: Optional[list[str]] = None) -> Dict[str, pd.DataFrame]:
        signature = _data_dir_signature(self.data_dir, self.adjust, symbols)
        cache_key = signature + (int(self.n_turnover_days),)
        with _PRICE_CACHE_LOCK:
            cached = _PRICE_CACHE.get(cache_key)
        if cached is not None:
            logger.info("复用进程内 OHLCV 缓存：%d 只，data_dir=%s", len(cached), self.data_dir)
            return dict(cached)

        data = load_price_data(
            self.data_dir,
            adjust=self.adjust,
            symbols=symbols,
            n_turnover_days=self.n_turnover_days,
            max_workers=self.max_workers,
            stop_event=self.stop_event,
        )
        with _PRICE_CACHE_LOCK:
            _PRICE_CACHE[cache_key] = data
            while len(_PRICE_CACHE) > _PRICE_CACHE_MAX_ENTRIES:
                oldest_key = next(iter(_PRICE_CACHE))
                _PRICE_CACHE.pop(oldest_key, None)
        logger.info("写入进程内 OHLCV 缓存：%d 只，data_dir=%s", len(data), self.data_dir)
        return dict(data)


@dataclass(frozen=True)
class TushareProvider:
    """Placeholder provider for future direct TUShare standardized reads."""
    id: str = "tushare"


@dataclass(frozen=True)
class OpenBBProvider:
    """Placeholder provider for future macro/overseas supplemental data."""
    id: str = "openbb"
