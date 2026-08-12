"""Persistent cache for strategy indicator precomputation."""
from __future__ import annotations

import hashlib
import gzip
import json
import logging
import pickle
import threading
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

import pandas as pd

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "data" / "cache" / "backtest_indicators"
CACHE_VERSION = 2
MAX_MEMORY_ENTRIES = 2
MAX_DISK_ENTRIES_PER_STRATEGY = 2

_memory_cache: OrderedDict[str, Dict[str, pd.DataFrame]] = OrderedDict()
_cache_lock = threading.RLock()


def build_cache_key(payload: dict[str, Any]) -> str:
    # 缓存版本参与哈希；指标定义变化时只需递增版本即可整体失效旧缓存。
    canonical = json.dumps(
        {"cache_version": CACHE_VERSION, **payload},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def _cache_path(strategy_id: str, cache_key: str) -> Path:
    safe_strategy = "".join(char for char in strategy_id if char.isalnum() or char in {"-", "_"})
    return CACHE_DIR / f"{safe_strategy}_{cache_key}.pkl.gz"


def _remember(cache_key: str, prepared: Dict[str, pd.DataFrame]) -> None:
    # 内存层采用小容量 LRU，避免多次调参时反复解压最近使用的指标数据。
    _memory_cache[cache_key] = prepared
    _memory_cache.move_to_end(cache_key)
    while len(_memory_cache) > MAX_MEMORY_ENTRIES:
        _memory_cache.popitem(last=False)


def load_prepared_cache(strategy_id: str, cache_key: str) -> tuple[Dict[str, pd.DataFrame] | None, str]:
    with _cache_lock:
        prepared = _memory_cache.get(cache_key)
        if prepared is not None:
            _memory_cache.move_to_end(cache_key)
            return prepared, "memory"

        path = _cache_path(strategy_id, cache_key)
        if not path.exists():
            return None, "miss"
        try:
            with gzip.open(path, "rb") as handle:
                payload = pickle.load(handle)
            if payload.get("cache_version") != CACHE_VERSION or payload.get("cache_key") != cache_key:
                return None, "miss"
            prepared = payload.get("prepared")
            if not isinstance(prepared, dict):
                raise ValueError("缓存内容缺少指标数据")
            _remember(cache_key, prepared)
            return prepared, "disk"
        except Exception as exc:  # noqa: BLE001
            logger.warning("回测指标缓存读取失败，将重新计算：%s", exc)
            path.unlink(missing_ok=True)
            return None, "miss"


def save_prepared_cache(
    strategy_id: str,
    cache_key: str,
    prepared: Dict[str, pd.DataFrame],
    metadata: dict[str, Any],
) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(strategy_id, cache_key)
    temporary = path.with_suffix(".tmp")
    payload = {
        "cache_version": CACHE_VERSION,
        "cache_key": cache_key,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "metadata": metadata,
        "prepared": prepared,
    }
    with _cache_lock:
        try:
            # 先写临时文件再原子替换，进程被中断时不会留下“看似存在但内容不完整”的缓存。
            with gzip.open(temporary, "wb", compresslevel=3) as handle:
                pickle.dump(payload, handle, protocol=pickle.HIGHEST_PROTOCOL)
            temporary.replace(path)
            _remember(cache_key, prepared)
            _prune_disk_cache(strategy_id, keep=path)
        finally:
            temporary.unlink(missing_ok=True)
    return path


def _prune_disk_cache(strategy_id: str, keep: Path) -> None:
    pattern = f"{strategy_id}_*.pkl.gz"
    files = sorted(CACHE_DIR.glob(pattern), key=lambda item: item.stat().st_mtime, reverse=True)
    retained = 0
    for path in files:
        if path == keep or retained < MAX_DISK_ENTRIES_PER_STRATEGY:
            retained += 1
            continue
        path.unlink(missing_ok=True)


def clear_memory_cache() -> None:
    with _cache_lock:
        _memory_cache.clear()
