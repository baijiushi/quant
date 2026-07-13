from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock

import pandas as pd
import tushare as ts

from pipeline.cancellation import RunCancelledError, raise_if_cancelled
from storage.database import (
    price_codes,
    price_data_signature,
    rescale_qfq_history,
    upsert_price_batch,
    upsert_stocks,
)

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DATA_ROOT = _PROJECT_ROOT / "data"
_DEFAULT_RAW_DIR = _DATA_ROOT / "raw"
_DEFAULT_STOCK_LIST_FILE = _DATA_ROOT / "stocklist.csv"
_DEFAULT_FAILURE_DIR = _DATA_ROOT / "failures"
_DEFAULT_ENV_FILE = _PROJECT_ROOT / ".env.local"


def _load_local_env_file(env_file: Path = _DEFAULT_ENV_FILE) -> None:
    """从项目根目录的 .env.local 读取本地密钥。"""
    if not env_file.exists():
        return

    try:
        for raw_line in env_file.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and value and key not in os.environ:
                os.environ[key] = value
    except Exception as exc:
        logger.warning("读取 %s 失败: %s", env_file, exc)


class AStockDataFetcher:
    """基于 TUShare 的 A 股日线抓取器，只保留当前 pipeline 主线所需能力。"""

    def __init__(
        self,
        raw_dir: Path | None = None,
        stock_list_file: Path | None = None,
        failure_dir: Path | None = None,
        rate_limit_per_minute: int = 195,
        request_interval_seconds: float = 0.30,
        max_retries: int = 3,
        second_pass_enabled: bool = True,
        second_pass_sleep_seconds: int = 8,
        max_workers: int = 6,
        stop_event: threading.Event | None = None,
    ) -> None:
        self.project_root = _PROJECT_ROOT
        self.raw_dir = raw_dir or _DEFAULT_RAW_DIR
        self.stock_list_file = stock_list_file or _DEFAULT_STOCK_LIST_FILE
        self.failure_dir = failure_dir or _DEFAULT_FAILURE_DIR

        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.stock_list_file.parent.mkdir(parents=True, exist_ok=True)
        self.failure_dir.mkdir(parents=True, exist_ok=True)

        self.rate_limit_per_minute = rate_limit_per_minute
        self.request_interval_seconds = request_interval_seconds
        self.max_retries = max_retries
        self.second_pass_enabled = second_pass_enabled
        self.second_pass_sleep_seconds = second_pass_sleep_seconds
        self.max_workers = max_workers
        self.stop_event = stop_event

        self.pro = None
        self.stock_list = None
        self._request_times = deque()
        self._request_lock = Lock()
        self._client_lock = Lock()
        self._state_lock = Lock()
        self.failed_symbols: dict[str, dict[str, str]] = {}
        self.empty_symbols: set[str] = set()
        self._db_updates: dict[str, pd.DataFrame] = {}

    def _check_cancelled(self) -> None:
        raise_if_cancelled(self.stop_event)

    def _ensure_client(self):
        self._check_cancelled()
        if self.pro is not None:
            return self.pro

        with self._client_lock:
            if self.pro is not None:
                return self.pro

            _load_local_env_file()
            token = os.environ.get("TUSHARE_TOKEN", "").strip()
            if not token:
                raise ValueError("未检测到 TUSHARE_TOKEN，请在项目根目录 .env.local 中填写。")

            os.environ["NO_PROXY"] = "api.waditu.com,.waditu.com,waditu.com"
            os.environ["no_proxy"] = os.environ["NO_PROXY"]
            ts.set_token(token)
            self.pro = ts.pro_api()
            logger.info("TUShare 客户端初始化成功")
            return self.pro

    @staticmethod
    def _to_ts_code(symbol: str) -> str:
        symbol = str(symbol).zfill(6)
        if symbol.startswith(("4", "8", "92")):
            return f"{symbol}.BJ"
        if symbol.startswith(("60", "68")):
            return f"{symbol}.SH"
        return f"{symbol}.SZ"

    def _history_file(self, symbol: str, adjust: str = "qfq") -> Path:
        suffix = adjust or "bfq"
        return self.raw_dir / f"{str(symbol).zfill(6)}_{suffix}.csv"

    def clear_history_cache(self, symbols: list[str], adjust: str = "qfq") -> int:
        deleted = 0
        for symbol in symbols:
            self._check_cancelled()
            fpath = self._history_file(symbol, adjust)
            if fpath.exists():
                try:
                    fpath.unlink()
                    deleted += 1
                except Exception as exc:
                    logger.warning("删除缓存失败 %s: %s", fpath, exc)
        return deleted

    def _reset_run_state(self) -> None:
        with self._state_lock:
            self.failed_symbols = {}
            self.empty_symbols = set()
            self._db_updates = {}

    def _record_db_update(self, symbol: str, frame: pd.DataFrame) -> None:
        if frame is None or frame.empty:
            return
        code = str(symbol).zfill(6)
        with self._state_lock:
            existing = self._db_updates.get(code)
            if existing is None or existing.empty:
                self._db_updates[code] = frame.copy()
            else:
                merged = pd.concat([existing, frame])
                self._db_updates[code] = merged[~merged.index.duplicated(keep="last")].sort_index()

    def _record_failed_symbol(self, symbol: str, stage: str, error_message: str | Exception) -> None:
        code = str(symbol).zfill(6)
        with self._state_lock:
            self.failed_symbols[code] = {
                "symbol": code,
                "stage": stage,
                "error": str(error_message),
                "recorded_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }

    def _clear_failed_symbol(self, symbol: str) -> None:
        with self._state_lock:
            self.failed_symbols.pop(str(symbol).zfill(6), None)

    def _record_empty_symbol(self, symbol: str) -> None:
        with self._state_lock:
            self.empty_symbols.add(str(symbol).zfill(6))

    def _clear_empty_symbol(self, symbol: str) -> None:
        with self._state_lock:
            self.empty_symbols.discard(str(symbol).zfill(6))

    def _save_failure_reports(self, total_symbols: int) -> None:
        with self._state_lock:
            failed_symbols = list(self.failed_symbols.values())
            empty_symbols = sorted(self.empty_symbols)

        summary = {
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "total_symbols": total_symbols,
            "failed_count": len(failed_symbols),
            "empty_count": len(empty_symbols),
            "failed_symbols": failed_symbols,
            "empty_symbols": empty_symbols,
        }

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        latest_json = self.failure_dir / "failed_symbols_latest.json"
        dated_json = self.failure_dir / f"failed_symbols_{timestamp}.json"
        latest_csv = self.failure_dir / "failed_symbols_latest.csv"

        payload = json.dumps(summary, ensure_ascii=False, indent=2)
        latest_json.write_text(payload, encoding="utf-8")
        dated_json.write_text(payload, encoding="utf-8")

        rows = failed_symbols
        pd.DataFrame(rows or [{"symbol": "", "stage": "", "error": "", "recorded_at": ""}]).to_csv(
            latest_csv,
            index=False,
            encoding="utf-8-sig",
        )

    def _throttle(self) -> None:
        with self._request_lock:
            self._check_cancelled()
            now = time.monotonic()
            while self._request_times and now - self._request_times[0] >= 60:
                self._request_times.popleft()

            if len(self._request_times) >= self.rate_limit_per_minute:
                wait_seconds = 60 - (now - self._request_times[0]) + 0.2
                if wait_seconds > 0:
                    logger.info("接近 TUShare 频率上限，等待 %.1f 秒...", wait_seconds)
                    self._sleep_with_cancel(wait_seconds)
                    now = time.monotonic()
                    while self._request_times and now - self._request_times[0] >= 60:
                        self._request_times.popleft()

            if self._request_times:
                delta = now - self._request_times[-1]
                if delta < self.request_interval_seconds:
                    self._sleep_with_cancel(self.request_interval_seconds - delta)
                    now = time.monotonic()

            self._request_times.append(now)

    def _sleep_with_cancel(self, seconds: float) -> None:
        remaining = max(0.0, seconds)
        while remaining > 0:
            self._check_cancelled()
            step = min(0.5, remaining)
            time.sleep(step)
            remaining -= step

    def _call_tushare(self, func, *args, **kwargs):
        last_error = None
        for attempt in range(1, self.max_retries + 1):
            try:
                self._check_cancelled()
                self._throttle()
                return func(*args, **kwargs)
            except Exception as exc:
                if isinstance(exc, RunCancelledError):
                    raise
                last_error = exc
                wait_seconds = min(30, 3 * (2 ** (attempt - 1)))
                logger.warning(
                    "TUShare 调用失败，第 %d/%d 次重试，%ds 后继续: %s",
                    attempt,
                    self.max_retries,
                    wait_seconds,
                    exc,
                )
                self._sleep_with_cancel(wait_seconds)
        raise last_error

    @staticmethod
    def _normalize_history_dataframe(df: pd.DataFrame | None) -> pd.DataFrame:
        if df is None or df.empty:
            return pd.DataFrame()

        result = df.copy()
        rename_map = {
            "trade_date": "date",
            "日期": "date",
            "开盘": "open",
            "收盘": "close",
            "最高": "high",
            "最低": "low",
            "成交量": "volume",
            "成交额": "amount",
            "涨跌幅": "pct_chg",
            "涨跌额": "change",
            "换手率": "turnover",
            "vol": "volume",
        }
        result = result.rename(columns=rename_map)

        required = ["date", "open", "close", "high", "low"]
        if not all(col in result.columns for col in required):
            return pd.DataFrame()

        result["date"] = pd.to_datetime(result["date"], errors="coerce")
        for col in ["open", "close", "high", "low", "volume", "amount", "pct_chg", "change", "turnover"]:
            if col in result.columns:
                result[col] = pd.to_numeric(result[col], errors="coerce")
            else:
                result[col] = 0.0

        result = result.dropna(subset=["date", "open", "close", "high", "low"])
        result = result.set_index("date").sort_index()
        return result

    def _load_history_cache(self, symbol: str, adjust: str = "qfq") -> pd.DataFrame:
        fpath = self._history_file(symbol, adjust)
        if not fpath.exists():
            return pd.DataFrame()

        try:
            return self._normalize_history_dataframe(pd.read_csv(fpath))
        except Exception as exc:
            logger.warning("读取历史缓存失败 %s: %s", fpath, exc)
            return pd.DataFrame()

    def _save_history_cache(self, symbol: str, adjust: str, df: pd.DataFrame) -> None:
        if df.empty:
            return
        try:
            df.reset_index().to_csv(self._history_file(symbol, adjust), index=False, encoding="utf-8-sig")
        except Exception as exc:
            logger.warning("保存历史缓存失败 %s: %s", symbol, exc)

    def get_stock_list(self) -> pd.DataFrame:
        self._check_cancelled()
        if self.stock_list is not None and not self.stock_list.empty:
            return self.stock_list

        if self.stock_list_file.exists():
            try:
                cached = pd.read_csv(self.stock_list_file, dtype={"代码": str, "ts_code": str})
                if not cached.empty and {"代码", "名称"}.issubset(cached.columns):
                    self.stock_list = cached
                    upsert_stocks(cached)
                    logger.info("从本地股票列表缓存读取成功，共 %d 只", len(cached))
                    return cached
            except Exception as exc:
                logger.warning("读取股票列表缓存失败: %s", exc)

        try:
            pro = self._ensure_client()
            stock_info = self._call_tushare(
                pro.stock_basic,
                exchange="",
                list_status="L",
                fields="ts_code,symbol,name,market,list_date",
            )
            if stock_info is None or stock_info.empty:
                raise ValueError("TUShare 返回的股票列表为空")

            stock_info = stock_info.rename(columns={"symbol": "代码", "name": "名称"})
            stock_info["代码"] = stock_info["代码"].astype(str).str.zfill(6)
            self.stock_list = stock_info[["代码", "名称", "ts_code", "market", "list_date"]].copy()
            self.stock_list.to_csv(self.stock_list_file, index=False, encoding="utf-8-sig")
            upsert_stocks(self.stock_list)
            logger.info("股票列表已保存至: %s", self.stock_list_file)
            return self.stock_list
        except Exception as exc:
            if isinstance(exc, RunCancelledError):
                raise
            logger.error("获取股票列表失败: %s", exc)
            return pd.DataFrame()

    def _fetch_history_from_api(self, symbol: str, start_date: str, end_date: str, adjust: str = "qfq") -> pd.DataFrame:
        self._check_cancelled()
        self._ensure_client()
        ts_code = self._to_ts_code(symbol)
        df = self._call_tushare(
            ts.pro_bar,
            ts_code=ts_code,
            adj=adjust or None,
            start_date=start_date,
            end_date=end_date,
            freq="D",
            api=self.pro,
        )
        df = self._normalize_history_dataframe(df)
        if not df.empty:
            logger.info("TUShare 成功获取股票 %s 的 %d 条历史数据", symbol, len(df))
        else:
            logger.warning("股票 %s 在 TUShare 中未返回历史数据", symbol)
        return df

    def get_stock_history(
        self,
        symbol: str,
        start_date: str | None = None,
        end_date: str | None = None,
        adjust: str = "qfq",
        use_cache_only: bool = False,
    ) -> pd.DataFrame:
        self._check_cancelled()
        end_date = end_date or datetime.now().strftime("%Y%m%d")
        start_date = start_date or (datetime.now() - timedelta(days=180)).strftime("%Y%m%d")
        start_dt = pd.to_datetime(start_date)
        end_dt = pd.to_datetime(end_date)

        try:
            self._check_cancelled()
            existing_df = self._load_history_cache(symbol, adjust)

            if not existing_df.empty:
                cached_min_date = existing_df.index.min()
                cached_max_date = existing_df.index.max()
                has_head = cached_min_date <= start_dt + timedelta(days=7)
                has_tail = cached_max_date >= end_dt - timedelta(days=1)

                if has_head and has_tail:
                    result = existing_df[(existing_df.index >= start_dt) & (existing_df.index <= end_dt)]
                    logger.info("命中本地数据：股票 %s，共 %d 条", symbol, len(result))
                    return result

                if use_cache_only:
                    result = existing_df[(existing_df.index >= start_dt) & (existing_df.index <= end_dt)]
                    logger.info("仅使用本地缓存：股票 %s，共 %d 条", symbol, len(result))
                    return result

                parts = [existing_df]

                if not has_head:
                    self._check_cancelled()
                    head_end = (cached_min_date - timedelta(days=1)).strftime("%Y%m%d")
                    logger.info("补抓股票 %s 前段历史：%s ~ %s", symbol, start_date, head_end)
                    try:
                        head_df = self._fetch_history_from_api(symbol, start_date, head_end, adjust)
                        if not head_df.empty:
                            parts.append(head_df)
                            self._record_db_update(symbol, head_df)
                    except Exception as exc:
                        if isinstance(exc, RunCancelledError):
                            raise
                        self._record_failed_symbol(symbol, "head_backfill", exc)
                        logger.warning("补抓股票 %s 前段历史失败，先使用现有缓存", symbol)

                if not has_tail:
                    self._check_cancelled()
                    tail_start = (cached_max_date + timedelta(days=1)).strftime("%Y%m%d")
                    logger.info("增量获取股票 %s：%s ~ %s", symbol, tail_start, end_date)
                    try:
                        tail_df = self._fetch_history_from_api(symbol, tail_start, end_date, adjust)
                        if not tail_df.empty:
                            parts.append(tail_df)
                            self._record_db_update(symbol, tail_df)
                    except Exception as exc:
                        if isinstance(exc, RunCancelledError):
                            raise
                        self._record_failed_symbol(symbol, "tail_backfill", exc)
                        logger.warning("增量获取 %s 失败，先使用现有缓存", symbol)

                merged = pd.concat(parts)
                merged = merged[~merged.index.duplicated(keep="last")].sort_index()
                if len(parts) > 1:
                    self._save_history_cache(symbol, adjust, merged)
                self._clear_failed_symbol(symbol)
                self._clear_empty_symbol(symbol)
                return merged[(merged.index >= start_dt) & (merged.index <= end_dt)]

            if use_cache_only:
                logger.warning("股票 %s 无本地缓存，且已禁用网络获取", symbol)
                return pd.DataFrame()

            logger.info("全量获取股票 %s：%s ~ %s", symbol, start_date, end_date)
            try:
                new_df = self._fetch_history_from_api(symbol, start_date, end_date, adjust)
            except Exception as exc:
                if isinstance(exc, RunCancelledError):
                    raise
                self._record_failed_symbol(symbol, "full_fetch", exc)
                return pd.DataFrame()

            if new_df.empty:
                self._record_empty_symbol(symbol)
                return pd.DataFrame()

            self._save_history_cache(symbol, adjust, new_df)
            self._record_db_update(symbol, new_df)
            self._clear_failed_symbol(symbol)
            self._clear_empty_symbol(symbol)
            return new_df
        except Exception as exc:
            if isinstance(exc, RunCancelledError):
                raise
            self._record_failed_symbol(symbol, "get_stock_history", exc)
            logger.error("获取股票 %s 历史数据失败: %s", symbol, exc)
            return pd.DataFrame()

    def bulk_incremental_update(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
        adjust: str = "qfq",
    ) -> list[str]:
        """Update the whole market by trade date and return codes needing full history.

        TUShare explicitly recommends querying ``daily`` by trade date. Existing
        qfq history is rebased only when the latest adjustment factor changes.
        """
        self._check_cancelled()
        requested = {str(symbol).zfill(6) for symbol in symbols}
        existing_codes = price_codes(adjust)
        missing_codes = sorted(requested - existing_codes)
        _, latest_date, _ = price_data_signature(adjust)
        if not latest_date or adjust not in {"qfq", "hfq", "bfq"}:
            logger.info("SQLite 尚无可用基准行情，回退到单票历史抓取")
            return sorted(requested)

        cached_latest = pd.Timestamp(latest_date)
        update_start = max(cached_latest + timedelta(days=1), pd.Timestamp(start_date))
        update_end = pd.Timestamp(end_date)
        if update_start > update_end:
            logger.info("全市场行情已是最新，无需调用日线接口")
            return missing_codes

        pro = self._ensure_client()
        calendar = self._call_tushare(
            pro.trade_cal,
            exchange="",
            start_date=update_start.strftime("%Y%m%d"),
            end_date=update_end.strftime("%Y%m%d"),
            is_open="1",
            fields="cal_date,is_open",
        )
        if calendar is None or calendar.empty:
            logger.info("更新区间没有交易日：%s ~ %s", update_start.date(), update_end.date())
            return missing_codes

        trade_dates = sorted(str(value) for value in calendar["cal_date"].tolist())
        logger.info("启用 TUShare 全市场快速增量：%d 个交易日，只需按日期批量请求", len(trade_dates))

        previous_factors: dict[str, float] = {}
        if adjust == "qfq":
            previous = self._call_tushare(pro.adj_factor, trade_date=cached_latest.strftime("%Y%m%d"))
            if previous is not None and not previous.empty:
                previous_factors = dict(zip(previous["ts_code"].str[:6], previous["adj_factor"].astype(float)))

        daily_batches: list[tuple[str, pd.DataFrame, pd.DataFrame | None]] = []
        latest_factors: dict[str, float] = {}
        for index, trade_date in enumerate(trade_dates, 1):
            self._check_cancelled()
            daily = self._call_tushare(
                pro.daily,
                trade_date=trade_date,
                fields="ts_code,trade_date,open,high,low,close,change,pct_chg,vol,amount",
            )
            if daily is None or daily.empty:
                logger.info("交易日 %s 暂无日线数据，可能尚未收盘入库", trade_date)
                continue
            daily = daily[daily["ts_code"].str[:6].isin(requested)].copy()
            factors: pd.DataFrame | None = None
            if adjust in {"qfq", "hfq"}:
                factors = self._call_tushare(pro.adj_factor, trade_date=trade_date)
                if factors is None or factors.empty:
                    raise ValueError(f"TUShare 未返回 {trade_date} 复权因子")
                factors = factors[["ts_code", "adj_factor"]].copy()
                latest_factors = dict(zip(factors["ts_code"].str[:6], factors["adj_factor"].astype(float)))
            daily_batches.append((trade_date, daily, factors))
            logger.info("全市场增量抓取进度 %d/%d：%s，共 %d 条", index, len(trade_dates), trade_date, len(daily))

        if not daily_batches:
            return missing_codes

        if adjust == "qfq" and previous_factors and latest_factors:
            ratios = {
                code: previous_factors[code] / latest_factors[code]
                for code in existing_codes
                if code in previous_factors and code in latest_factors and latest_factors[code]
            }
            rescale_qfq_history(ratios, cached_latest.strftime("%Y-%m-%d"))

        updates: dict[str, pd.DataFrame] = {}
        for _, daily, factors in daily_batches:
            if factors is not None:
                daily = daily.merge(factors, on="ts_code", how="left")
            daily["code"] = daily["ts_code"].str[:6]
            if adjust == "qfq":
                daily["price_multiplier"] = daily.apply(
                    lambda row: float(row["adj_factor"]) / latest_factors.get(str(row["code"]), float(row["adj_factor"])),
                    axis=1,
                )
            elif adjust == "hfq":
                daily["price_multiplier"] = daily["adj_factor"].astype(float)
            else:
                daily["price_multiplier"] = 1.0
            for column in ["open", "high", "low", "close"]:
                daily[column] = pd.to_numeric(daily[column], errors="coerce") * daily["price_multiplier"]
            for code, rows in daily.groupby("code"):
                frame = self._normalize_history_dataframe(rows.rename(columns={"vol": "volume"}))
                if frame.empty:
                    continue
                existing = updates.get(str(code))
                updates[str(code)] = frame if existing is None else pd.concat([existing, frame]).sort_index()

        logger.info("开始将本轮新增行情同步到 SQLite：%d 只股票", len(updates))
        upsert_price_batch(updates, adjust)
        from pipeline.providers import clear_price_cache

        clear_price_cache()
        logger.info("全市场快速增量完成：新增 %d 个交易日，仍需补历史 %d 只", len(daily_batches), len(missing_codes))
        if not missing_codes:
            self._reset_run_state()
            self._save_failure_reports(len(symbols))
        return missing_codes

    def get_multiple_stocks_history(
        self,
        symbols: list[str],
        start_date: str | None = None,
        end_date: str | None = None,
        adjust: str = "qfq",
        use_cache_only: bool = False,
    ) -> dict[str, pd.DataFrame]:
        self._check_cancelled()
        self._reset_run_state()
        stock_data: dict[str, pd.DataFrame] = {}
        total = len(symbols)

        worker_count = max(1, min(self.max_workers, total))
        logger.info(
            "开始并发抓取 %d 只股票，工作线程 %d，限频 %d/min",
            total,
            worker_count,
            self.rate_limit_per_minute,
        )

        def _fetch_one(symbol: str) -> tuple[str, pd.DataFrame]:
            return str(symbol).zfill(6), self.get_stock_history(symbol, start_date, end_date, adjust, use_cache_only)

        completed = 0
        executor = ThreadPoolExecutor(max_workers=worker_count)
        try:
            futures = {executor.submit(_fetch_one, symbol): str(symbol).zfill(6) for symbol in symbols}
            for future in as_completed(futures):
                self._check_cancelled()
                symbol = futures[future]
                try:
                    code, df = future.result()
                    if not df.empty:
                        stock_data[code] = df
                except Exception as exc:
                    if isinstance(exc, RunCancelledError):
                        raise
                    self._record_failed_symbol(symbol, "parallel_batch_fetch", exc)
                    logger.warning("并发获取股票 %s 数据异常: %s", symbol, exc)
                completed += 1
                if completed % 10 == 0 or completed == total:
                    logger.info("已完成获取 %d/%d 只股票的数据", completed, total)
        except Exception:
            executor.shutdown(wait=False, cancel_futures=True)
            raise
        finally:
            executor.shutdown(wait=True, cancel_futures=True)

        with self._state_lock:
            retry_symbols = sorted(self.failed_symbols.keys())

        if self.second_pass_enabled and retry_symbols and not use_cache_only:
            self._check_cancelled()
            logger.warning(
                "首轮有 %d 只股票抓取失败，%ds 后开始二次补抓...",
                len(retry_symbols),
                self.second_pass_sleep_seconds,
            )
            self._sleep_with_cancel(self.second_pass_sleep_seconds)
            retry_completed = 0
            retry_workers = max(1, min(self.max_workers, len(retry_symbols)))
            executor = ThreadPoolExecutor(max_workers=retry_workers)
            try:
                futures = {executor.submit(_fetch_one, symbol): symbol for symbol in retry_symbols}
                for future in as_completed(futures):
                    self._check_cancelled()
                    symbol = futures[future]
                    try:
                        code, df = future.result()
                        if not df.empty:
                            stock_data[code] = df
                            self._clear_failed_symbol(symbol)
                            self._clear_empty_symbol(symbol)
                    except Exception as exc:
                        if isinstance(exc, RunCancelledError):
                            raise
                        self._record_failed_symbol(symbol, "second_pass_fetch", exc)
                        logger.warning("二次补抓 %s 失败: %s", symbol, exc)
                    retry_completed += 1
                    logger.info("二次补抓进度 %d/%d", retry_completed, len(retry_symbols))
            except Exception:
                executor.shutdown(wait=False, cancel_futures=True)
                raise
            finally:
                executor.shutdown(wait=True, cancel_futures=True)

        self._save_failure_reports(total)
        # Write only rows fetched in this run. Cache hits must not rewrite years of history.
        try:
            with self._state_lock:
                db_updates = dict(self._db_updates)
            logger.info("开始同步本轮新增行情到 SQLite：%d 只股票", len(db_updates))
            upsert_price_batch(db_updates, adjust)
            from pipeline.providers import clear_price_cache

            clear_price_cache()
            logger.info("已同步 %d 只股票的新增日线到 SQLite", len(db_updates))
        except Exception as exc:  # noqa: BLE001
            # CSV remains the compatibility cache, so a DB failure must not discard fetched data.
            logger.warning("同步日线数据到 SQLite 失败，已保留 CSV 缓存: %s", exc)
        logger.info("成功获取 %d/%d 只股票的历史数据", len(stock_data), total)
        with self._state_lock:
            failed_count = len(self.failed_symbols)
        if failed_count:
            logger.warning("仍有 %d 只股票抓取失败，详情见 %s", failed_count, self.failure_dir)
        return stock_data
