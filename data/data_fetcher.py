# A股数据获取模块
import logging
import os
import sys
import time
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock

import pandas as pd
import tushare as ts

# 添加项目根目录到路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.config import DATA_CONFIG
from indicators.kdj import KDJ
from indicators.macd import MACD

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# pandas 2.2+ 兼容补丁，避免 tushare 内部旧式 fillna(method=...) 调用报错
_orig_df_fillna = pd.DataFrame.fillna
_orig_series_fillna = pd.Series.fillna


def _patched_df_fillna(self, value=None, *, method=None, axis=None, inplace=False, limit=None, **kwargs):
    if method is not None:
        if method == "ffill":
            return self.ffill(axis=axis, inplace=inplace, limit=limit)
        if method == "bfill":
            return self.bfill(axis=axis, inplace=inplace, limit=limit)
        raise ValueError(f"Unsupported fillna method: {method}")
    return _orig_df_fillna(self, value, axis=axis, inplace=inplace, limit=limit, **kwargs)


def _patched_series_fillna(self, value=None, *, method=None, axis=None, inplace=False, limit=None, **kwargs):
    if method is not None:
        if method == "ffill":
            return self.ffill(axis=axis, inplace=inplace, limit=limit)
        if method == "bfill":
            return self.bfill(axis=axis, inplace=inplace, limit=limit)
        raise ValueError(f"Unsupported fillna method: {method}")
    return _orig_series_fillna(self, value, axis=axis, inplace=inplace, limit=limit, **kwargs)


pd.DataFrame.fillna = _patched_df_fillna  # type: ignore[method-assign]
pd.Series.fillna = _patched_series_fillna  # type: ignore[method-assign]


def _load_local_env_file():
    """从项目根目录的 .env.local 读取本地密钥，避免提交到 GitHub。"""
    project_root = Path(__file__).resolve().parent.parent
    env_file = project_root / ".env.local"
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
        logger.warning(f"读取 .env.local 失败: {exc}")


class AStockDataFetcher:
    """A股数据获取类（基于 TUShare）"""

    def __init__(self):
        """初始化数据获取器"""
        self.stock_list = None
        self.pro = None

        self.data_root = Path(DATA_CONFIG.get("data_root", "data"))
        self.raw_dir = Path(DATA_CONFIG.get("history_dir", self.data_root / "raw"))
        self.stock_list_cache_file = Path(
            DATA_CONFIG.get("stock_list_file", self.data_root / "stocklist.csv")
        )
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.stock_list_cache_file.parent.mkdir(parents=True, exist_ok=True)

        self.rate_limit_per_minute = int(DATA_CONFIG.get("rate_limit_per_minute", 180))
        self.request_interval = float(DATA_CONFIG.get("request_interval_seconds", 0.35))
        self.max_retries = int(DATA_CONFIG.get("max_retries", 3))
        self.max_workers = int(DATA_CONFIG.get("max_workers", 3))

        self._request_times = deque()
        self._request_lock = Lock()

    def _ensure_client(self):
        """初始化 TUShare 客户端"""
        if self.pro is not None:
            return self.pro

        _load_local_env_file()
        token = os.environ.get("TUSHARE_TOKEN", "").strip()
        if not token:
            raise ValueError(
                "未检测到 TUSHARE_TOKEN。请在项目根目录 .env.local 中填写，或设置系统环境变量。"
            )

        os.environ["NO_PROXY"] = "api.waditu.com,.waditu.com,waditu.com"
        os.environ["no_proxy"] = os.environ["NO_PROXY"]
        ts.set_token(token)
        self.pro = ts.pro_api()
        logger.info("TUShare 客户端初始化成功")
        return self.pro

    @staticmethod
    def _to_ts_code(symbol):
        """6位股票代码转 ts_code"""
        symbol = str(symbol).zfill(6)
        if symbol.startswith(("60", "68", "90")):
            return f"{symbol}.SH"
        if symbol.startswith(("4", "8")):
            return f"{symbol}.BJ"
        return f"{symbol}.SZ"

    def _history_cache_file(self, symbol, adjust):
        """历史数据 CSV 路径"""
        suffix = adjust or "bfq"
        return self.raw_dir / f"{symbol}_{suffix}.csv"

    def clear_history_cache(self, symbols, adjust="qfq"):
        """清理指定股票的历史 CSV 缓存"""
        deleted = 0
        for symbol in symbols:
            cache_file = self._history_cache_file(symbol, adjust)
            if cache_file.exists():
                try:
                    cache_file.unlink()
                    deleted += 1
                except Exception as exc:
                    logger.warning(f"删除缓存失败 {cache_file}: {exc}")
        return deleted

    def _throttle(self):
        """简单限流，避免超过套餐频率"""
        with self._request_lock:
            now = time.monotonic()
            while self._request_times and now - self._request_times[0] >= 60:
                self._request_times.popleft()

            if len(self._request_times) >= self.rate_limit_per_minute:
                wait_seconds = 60 - (now - self._request_times[0]) + 0.2
                if wait_seconds > 0:
                    logger.info(f"接近 TUShare 频率上限，等待 {wait_seconds:.1f} 秒...")
                    time.sleep(wait_seconds)
                    now = time.monotonic()
                    while self._request_times and now - self._request_times[0] >= 60:
                        self._request_times.popleft()

            if self._request_times:
                delta = now - self._request_times[-1]
                if delta < self.request_interval:
                    time.sleep(self.request_interval - delta)
                    now = time.monotonic()

            self._request_times.append(now)

    def _call_tushare(self, func, *args, **kwargs):
        """带重试和限流的 TUShare 调用"""
        last_error = None
        for attempt in range(1, self.max_retries + 1):
            try:
                self._throttle()
                return func(*args, **kwargs)
            except Exception as exc:
                last_error = exc
                wait_seconds = attempt * 3
                logger.warning(f"TUShare 调用失败，第 {attempt}/{self.max_retries} 次重试，{wait_seconds}s 后继续: {exc}")
                time.sleep(wait_seconds)
        raise last_error

    def _load_stock_list_cache(self):
        """读取股票列表缓存"""
        if self.stock_list_cache_file.exists():
            try:
                df = pd.read_csv(
                    self.stock_list_cache_file,
                    dtype={"代码": str, "symbol": str, "ts_code": str},
                )
                if not df.empty and "代码" in df.columns and "名称" in df.columns:
                    logger.info(f"从本地 CSV 读取股票列表成功，共 {len(df)} 只")
                    return df
            except Exception as exc:
                logger.warning(f"读取股票列表缓存失败: {exc}")
        return pd.DataFrame()

    def _save_stock_list_cache(self, df):
        """保存股票列表缓存"""
        try:
            if df is not None and not df.empty:
                df.to_csv(self.stock_list_cache_file, index=False, encoding="utf-8-sig")
                logger.info(f"股票列表已保存至: {self.stock_list_cache_file}")
        except Exception as exc:
            logger.warning(f"保存股票列表缓存失败: {exc}")

    def _load_full_history_cache(self, symbol, adjust):
        """读取单只股票完整历史 CSV"""
        cache_file = self._history_cache_file(symbol, adjust)
        if cache_file.exists():
            try:
                df = pd.read_csv(cache_file)
                df = self._normalize_history_dataframe(df)
                if not df.empty:
                    logger.debug(f"从本地 CSV 读取股票 {symbol} 历史数据，共 {len(df)} 条")
                    return df
            except Exception as exc:
                logger.warning(f"读取股票 {symbol} 历史数据缓存失败: {exc}")
        return pd.DataFrame()

    def _save_full_history_cache(self, symbol, adjust, df):
        """保存单只股票完整历史 CSV"""
        try:
            if df is not None and not df.empty:
                cache_file = self._history_cache_file(symbol, adjust)
                output = df.reset_index()
                output.to_csv(cache_file, index=False, encoding="utf-8-sig")
        except Exception as exc:
            logger.warning(f"保存股票 {symbol} 历史数据缓存失败: {exc}")

    @staticmethod
    def _normalize_history_dataframe(df):
        """统一历史行情字段"""
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
            "amount": "amount",
        }
        result = result.rename(columns=rename_map)

        required_defaults = {
            "volume": 0.0,
            "amount": 0.0,
            "pct_chg": 0.0,
            "change": 0.0,
            "turnover": 0.0,
        }
        for col, default_value in required_defaults.items():
            if col not in result.columns:
                result[col] = default_value

        core_columns = ["date", "open", "close", "high", "low"]
        if not all(col in result.columns for col in core_columns):
            return pd.DataFrame()

        result["date"] = pd.to_datetime(result["date"], errors="coerce")
        numeric_columns = [
            "open",
            "close",
            "high",
            "low",
            "volume",
            "amount",
            "pct_chg",
            "change",
            "turnover",
        ]
        for col in numeric_columns:
            result[col] = pd.to_numeric(result[col], errors="coerce")

        result = result.dropna(subset=["date", "open", "close", "high", "low"])
        result = result.set_index("date").sort_index()
        return result

    def get_stock_list(self):
        """
        获取 A 股股票列表

        Returns:
            pandas.DataFrame: 股票列表数据
        """
        try:
            logger.info("正在获取 A 股股票列表...")

            cached_df = self._load_stock_list_cache()
            if not cached_df.empty:
                self.stock_list = cached_df
                return self.stock_list

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
            self._save_stock_list_cache(self.stock_list)
            logger.info(f"成功从 TUShare 获取 {len(self.stock_list)} 只股票信息")
            return self.stock_list

        except Exception as exc:
            logger.error(f"获取股票列表失败: {exc}")
            return pd.DataFrame()

    def _fetch_history_from_api(self, symbol, start_date, end_date, adjust="qfq"):
        """
        通过 TUShare 获取股票历史数据

        Returns:
            pandas.DataFrame: 获取到的历史数据，失败时返回空 DataFrame
        """
        try:
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
                logger.info(f"TUShare 成功获取股票 {symbol} 的 {len(df)} 条历史数据")
            else:
                logger.warning(f"股票 {symbol} 在 TUShare 中未返回历史数据")
            return df
        except Exception as exc:
            logger.warning(f"TUShare 获取股票 {symbol} 历史数据失败: {exc}")
            return pd.DataFrame()

    def get_stock_history(self, symbol, start_date=None, end_date=None, adjust="qfq", use_cache_only=False):
        """
        获取单只股票的历史数据（支持增量缓存）
        """
        if end_date is None:
            end_date = datetime.now().strftime("%Y%m%d")
        if start_date is None:
            start_date = (datetime.now() - timedelta(days=180)).strftime("%Y%m%d")

        start_dt = pd.to_datetime(start_date)
        end_dt = pd.to_datetime(end_date)

        try:
            existing_df = self._load_full_history_cache(symbol, adjust)

            if not existing_df.empty:
                cached_max_date = existing_df.index.max()
                if cached_max_date >= end_dt - timedelta(days=1):
                    result = existing_df[(existing_df.index >= start_dt) & (existing_df.index <= end_dt)]
                    logger.info(f"命中本地 CSV 缓存：股票 {symbol}，共 {len(result)} 条")
                    return result

                if use_cache_only:
                    result = existing_df[(existing_df.index >= start_dt) & (existing_df.index <= end_dt)]
                    logger.info(f"仅使用本地 CSV 缓存：股票 {symbol}，共 {len(result)} 条")
                    return result

                fetch_start = (cached_max_date + timedelta(days=1)).strftime("%Y%m%d")
                logger.info(f"增量获取股票 {symbol}：{fetch_start} ~ {end_date}")
                new_df = self._fetch_history_from_api(symbol, fetch_start, end_date, adjust)

                if not new_df.empty:
                    merged = pd.concat([existing_df, new_df])
                    merged = merged[~merged.index.duplicated(keep="last")].sort_index()
                    self._save_full_history_cache(symbol, adjust, merged)
                    return merged[(merged.index >= start_dt) & (merged.index <= end_dt)]

                logger.warning(f"增量获取 {symbol} 失败，回退到已有本地 CSV")
                return existing_df[(existing_df.index >= start_dt) & (existing_df.index <= end_dt)]

            if use_cache_only:
                logger.warning(f"股票 {symbol} 无本地 CSV 缓存，且已禁用网络获取")
                return pd.DataFrame()

            logger.info(f"全量获取股票 {symbol}：{start_date} ~ {end_date}")
            new_df = self._fetch_history_from_api(symbol, start_date, end_date, adjust)
            if not new_df.empty:
                self._save_full_history_cache(symbol, adjust, new_df)
            return new_df

        except Exception as exc:
            logger.error(f"获取股票 {symbol} 历史数据失败: {exc}")
            return pd.DataFrame()

    def get_multiple_stocks_history(self, symbols, start_date=None, end_date=None, adjust="qfq", use_cache_only=False):
        """
        批量获取多只股票的历史数据
        """
        stock_data = {}
        total = len(symbols)

        for index, symbol in enumerate(symbols, start=1):
            try:
                df = self.get_stock_history(symbol, start_date, end_date, adjust, use_cache_only=use_cache_only)
                if not df.empty:
                    stock_data[symbol] = df
                if index % 10 == 0 or index == total:
                    logger.info(f"已完成获取 {index}/{total} 只股票的数据")
            except Exception as exc:
                logger.warning(f"获取股票 {symbol} 数据异常: {exc}")

        logger.info(f"成功获取 {len(stock_data)}/{total} 只股票的历史数据")
        return stock_data

    def get_stock_realtime(self, symbol):
        """
        获取股票实时数据（使用最近一个交易日收盘数据近似代替）
        """
        try:
            logger.info(f"正在获取股票 {symbol} 的最新行情...")
            end_date = datetime.now().strftime("%Y%m%d")
            start_date = (datetime.now() - timedelta(days=10)).strftime("%Y%m%d")
            df = self._fetch_history_from_api(symbol, start_date, end_date, adjust="qfq")
            if df.empty:
                return pd.DataFrame()
            latest = df.reset_index().tail(1)
            latest["代码"] = str(symbol).zfill(6)
            return latest
        except Exception as exc:
            logger.error(f"获取股票 {symbol} 最新行情失败: {exc}")
            return pd.DataFrame()

    def calculate_indicators(self, df):
        """
        计算技术指标（KDJ 和 MACD）
        """
        if df.empty:
            return df

        try:
            kdj = KDJ(period=9, signal=3)
            df_with_kdj = kdj.calculate(df)

            macd = MACD(fast=12, slow=26, signal=9)
            df_with_indicators = macd.calculate(df_with_kdj)

            return df_with_indicators
        except Exception as exc:
            logger.error(f"计算技术指标失败: {exc}")
            return df
