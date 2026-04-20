# A股数据获取模块
import akshare as ak
import pandas as pd
from datetime import datetime, timedelta
import time
import logging
import os
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# 添加项目根目录到路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from indicators.kdj import KDJ
from indicators.macd import MACD

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 禁用代理（解决代理连接问题）
os.environ.pop('HTTP_PROXY', None)
os.environ.pop('HTTPS_PROXY', None)
os.environ.pop('http_proxy', None)
os.environ.pop('https_proxy', None)


class AStockDataFetcher:
    """A股数据获取类"""
    
    def __init__(self):
        """初始化数据获取器"""
        self.stock_list = None
        # 你的当前网络环境下东财接口大量失败，因此默认关闭东财数据源
        self.enable_eastmoney_fallback = False
        self.cache_dir = Path("data") / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.stock_list_cache_file = self.cache_dir / "stock_list.csv"

    def _history_cache_file(self, symbol, start_date, end_date, adjust):
        """生成历史数据缓存文件路径"""
        filename = f"{symbol}_{start_date}_{end_date}_{adjust or 'bfq'}.csv"
        return self.cache_dir / filename

    def _load_stock_list_cache(self):
        """读取股票列表缓存"""
        if self.stock_list_cache_file.exists():
            try:
                df = pd.read_csv(self.stock_list_cache_file, dtype={"代码": str})
                if not df.empty and "代码" in df.columns and "名称" in df.columns:
                    logger.info(f"从本地缓存读取股票列表成功，共{len(df)}只")
                    return df
            except Exception as e:
                logger.warning(f"读取股票列表缓存失败: {e}")
        return pd.DataFrame()

    def _save_stock_list_cache(self, df):
        """保存股票列表缓存"""
        try:
            if df is not None and not df.empty:
                df.to_csv(self.stock_list_cache_file, index=False, encoding='utf-8-sig')
        except Exception as e:
            logger.warning(f"保存股票列表缓存失败: {e}")

    def _load_history_cache(self, symbol, start_date, end_date, adjust):
        """读取历史数据缓存"""
        cache_file = self._history_cache_file(symbol, start_date, end_date, adjust)
        if cache_file.exists():
            try:
                df = pd.read_csv(cache_file)
                df = self._normalize_history_dataframe(df)
                if not df.empty:
                    logger.info(f"从本地缓存读取股票{symbol}历史数据成功，共{len(df)}条")
                    return df
            except Exception as e:
                logger.warning(f"读取股票{symbol}历史数据缓存失败: {e}")
        return pd.DataFrame()

    def _save_history_cache(self, symbol, start_date, end_date, adjust, df):
        """保存历史数据缓存"""
        try:
            if df is not None and not df.empty:
                cache_file = self._history_cache_file(symbol, start_date, end_date, adjust)
                df.reset_index().to_csv(cache_file, index=False, encoding='utf-8-sig')
        except Exception as e:
            logger.warning(f"保存股票{symbol}历史数据缓存失败: {e}")

    @staticmethod
    def _normalize_history_dataframe(df):
        """统一不同数据源返回的历史行情字段"""
        if df is None or df.empty:
            return pd.DataFrame()

        result = df.copy()

        rename_map = {
            '日期': 'date',
            '开盘': 'open',
            '收盘': 'close',
            '最高': 'high',
            '最低': 'low',
            '成交量': 'volume',
            '成交额': 'amount',
            '振幅': 'amplitude',
            '涨跌幅': 'pct_chg',
            '涨跌额': 'change',
            '换手率': 'turnover',
        }
        result = result.rename(columns=rename_map)

        if 'date' not in result.columns and result.index.name == 'date':
            result = result.reset_index()
        elif 'date' not in result.columns and 'date' not in result.index.names:
            result = result.reset_index()
            if 'index' in result.columns:
                result = result.rename(columns={'index': 'date'})

        required_defaults = {
            'volume': 0.0,
            'amount': 0.0,
            'amplitude': 0.0,
            'pct_chg': 0.0,
            'change': 0.0,
            'turnover': 0.0,
        }
        for col, default_value in required_defaults.items():
            if col not in result.columns:
                result[col] = default_value

        core_columns = ['date', 'open', 'close', 'high', 'low']
        if not all(col in result.columns for col in core_columns):
            return pd.DataFrame()

        result['date'] = pd.to_datetime(result['date'])
        numeric_columns = ['open', 'close', 'high', 'low', 'volume', 'amount', 'amplitude', 'pct_chg', 'change', 'turnover']
        for col in numeric_columns:
            if col in result.columns:
                result[col] = pd.to_numeric(result[col], errors='coerce')

        result = result.set_index('date').sort_index()
        result = result.dropna(subset=['open', 'close', 'high', 'low'])
        return result
        
    def get_stock_list(self):
        """
        获取A股股票列表
        
        Returns:
            pandas.DataFrame: 股票列表数据
        """
        try:
            logger.info("正在获取A股股票列表...")

            # 优先读取本地缓存
            cached_df = self._load_stock_list_cache()
            if not cached_df.empty:
                self.stock_list = cached_df
                return self.stock_list

            # 方法1: 使用 AkShare 的股票代码名称接口（实测可用，不依赖东财实时行情接口）
            try:
                stock_info = ak.stock_info_a_code_name()
                stock_info = stock_info.rename(columns={"code": "代码", "name": "名称"})
                self.stock_list = stock_info[["代码", "名称"]].copy()
                logger.info(f"方法1成功获取{len(self.stock_list)}只股票信息")
                self._save_stock_list_cache(self.stock_list)
                return self.stock_list
            except Exception as e1:
                logger.warning(f"方法1失败: {e1}")

            # 方法2: 东财实时列表接口（在你的网络环境下可能失败，作为备用）
            try:
                self.stock_list = ak.stock_zh_a_spot_em()
                logger.info(f"方法2成功获取{len(self.stock_list)}只股票信息")
                self._save_stock_list_cache(self.stock_list[["代码", "名称"]].copy())
                return self.stock_list
            except Exception as e2:
                logger.warning(f"方法2失败: {e2}")
            
            # 方法3: 使用备用股票列表（常见的A股）
            logger.info("使用备用股票列表...")
            common_stocks = [
                {'代码': '000001', '名称': '平安银行'},
                {'代码': '000002', '名称': '万科A'},
                {'代码': '000858', '名称': '五粮液'},
                {'代码': '000725', '名称': '京东方A'},
                {'代码': '002415', '名称': '海康威视'},
                {'代码': '600036', '名称': '招商银行'},
                {'代码': '600519', '名称': '贵州茅台'},
                {'代码': '601318', '名称': '中国平安'},
                {'代码': '601398', '名称': '工商银行'},
                {'代码': '000876', '名称': '新希望'},
                {'代码': '002594', '名称': '比亚迪'},
                {'代码': '300750', '名称': '宁德时代'},
                {'代码': '002714', '名称': '牧原股份'},
                {'代码': '300059', '名称': '东方财富'},
                {'代码': '002352', '名称': '顺丰控股'},
                {'代码': '600276', '名称': '恒瑞医药'},
                {'代码': '000568', '名称': '泸州老窖'},
                {'代码': '002304', '名称': '洋河股份'},
                {'代码': '000063', '名称': '中兴通讯'},
                {'代码': '300015', '名称': '爱尔眼科'},
                {'代码': '600887', '名称': '伊利股份'},
                {'代码': '002142', '名称': '宁波银行'},
                {'代码': '600309', '名称': '万华化学'},
                {'代码': '601166', '名称': '兴业银行'},
                {'代码': '002475', '名称': '立讯精密'},
                {'代码': '300124', '名称': '汇川技术'},
                {'代码': '002230', '名称': '科大讯飞'},
                {'代码': '600585', '名称': '海螺水泥'},
                {'代码': '000776', '名称': '广发证券'},
                {'代码': '002572', '名称': '索菲亚'},
            ]
            
            self.stock_list = pd.DataFrame(common_stocks)
            logger.info(f"备用方案成功获取{len(self.stock_list)}只股票信息")
            self._save_stock_list_cache(self.stock_list)
            return self.stock_list
            
        except Exception as e:
            logger.error(f"获取股票列表失败: {e}")
            return pd.DataFrame()
    
    def get_stock_history(self, symbol, start_date=None, end_date=None, adjust="qfq"):
        """
        获取单只股票的历史数据
        
        Args:
            symbol: 股票代码，如"000001"
            start_date: 开始日期，格式"YYYYMMDD"，默认为6个月前
            end_date: 结束日期，格式"YYYYMMDD"，默认为今天
            adjust: 复权类型，"qfq"前复权，"hfq"后复权，""不复权
            
        Returns:
            pandas.DataFrame: 股票历史数据
        """
        # 设置默认日期
        if end_date is None:
            end_date = datetime.now().strftime("%Y%m%d")
        if start_date is None:
            start_date = (datetime.now() - timedelta(days=180)).strftime("%Y%m%d")
            
        try:
            logger.info(f"正在获取股票{symbol}的历史数据...")

            # 优先读取本地缓存
            cached_df = self._load_history_cache(symbol, start_date, end_date, adjust)
            if not cached_df.empty:
                return cached_df

            # 方法1: 腾讯接口（AkShare，绕开东财历史K线接口）
            try:
                tx_symbol = f"sh{symbol}" if symbol.startswith('6') else f"sz{symbol}"
                df = ak.stock_zh_a_hist_tx(
                    symbol=tx_symbol,
                    start_date=start_date,
                    end_date=end_date,
                )
                df = self._normalize_history_dataframe(df)
                if not df.empty:
                    logger.info(f"腾讯接口成功获取股票{symbol}的{len(df)}条历史数据")
                    self._save_history_cache(symbol, start_date, end_date, adjust, df)
                    return df
            except Exception as e1:
                logger.warning(f"腾讯接口获取股票{symbol}历史数据失败: {e1}")

            # 方法2: 新浪日线接口（AkShare，全量数据，再按日期截取）
            try:
                sina_symbol = f"sh{symbol}" if symbol.startswith('6') else f"sz{symbol}"
                df = ak.stock_zh_a_daily(symbol=sina_symbol, adjust=adjust)
                df = self._normalize_history_dataframe(df)
                if not df.empty:
                    start_dt = pd.to_datetime(start_date)
                    end_dt = pd.to_datetime(end_date)
                    df = df[(df.index >= start_dt) & (df.index <= end_dt)]
                    if not df.empty:
                        logger.info(f"新浪接口成功获取股票{symbol}的{len(df)}条历史数据")
                        self._save_history_cache(symbol, start_date, end_date, adjust, df)
                        return df
            except Exception as e2:
                logger.warning(f"新浪接口获取股票{symbol}历史数据失败: {e2}")

            # 方法3: 东财接口（默认关闭，仅在需要时手动开启）
            if self.enable_eastmoney_fallback:
                try:
                    df = ak.stock_zh_a_hist(
                        symbol=symbol,
                        period="daily",
                        start_date=start_date,
                        end_date=end_date,
                        adjust=adjust
                    )
                    df = self._normalize_history_dataframe(df)
                    if not df.empty:
                        logger.info(f"东财接口成功获取股票{symbol}的{len(df)}条历史数据")
                        self._save_history_cache(symbol, start_date, end_date, adjust, df)
                        return df
                except Exception as e3:
                    logger.debug(f"东财接口获取股票{symbol}历史数据失败: {e3}")

            logger.warning(f"股票{symbol}未获取到历史数据")
            return pd.DataFrame()
                
        except Exception as e:
            logger.error(f"获取股票{symbol}历史数据失败: {e}")
            return pd.DataFrame()
    
    def get_multiple_stocks_history(self, symbols, start_date=None, end_date=None, adjust="qfq"):
        """
        批量获取多只股票的历史数据（并行版本）
        
        Args:
            symbols: 股票代码列表
            start_date: 开始日期
            end_date: 结束日期
            adjust: 复权类型
            
        Returns:
            dict: 以股票代码为键，DataFrame为值的字典
        """
        stock_data = {}
        total = len(symbols)
        
        def fetch_single(symbol):
            """获取单只股票数据的辅助函数"""
            try:
                df = self.get_stock_history(symbol, start_date, end_date, adjust)
                if not df.empty:
                    return symbol, df
                return symbol, pd.DataFrame()
            except Exception as e:
                logger.warning(f"获取股票{symbol}数据异常: {e}")
                return symbol, pd.DataFrame()
        
        # 使用线程池并行获取，最大线程数为5（避免请求过快被限制）
        max_workers = min(5, total)
        completed = 0
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # 提交所有任务
            future_to_symbol = {executor.submit(fetch_single, symbol): symbol for symbol in symbols}
            
            # 收集结果
            for future in as_completed(future_to_symbol):
                completed += 1
                try:
                    symbol, df = future.result()
                    if not df.empty:
                        stock_data[symbol] = df
                    if completed % 10 == 0 or completed == total:
                        logger.info(f"已完成获取 {completed}/{total} 只股票的数据")
                except Exception as e:
                    logger.warning(f"处理结果异常: {e}")
                
        logger.info(f"成功获取{len(stock_data)}/{total}只股票的历史数据")
        return stock_data
    
    def get_stock_realtime(self, symbol):
        """
        获取股票实时数据
        
        Args:
            symbol: 股票代码
            
        Returns:
            pandas.DataFrame: 实时数据
        """
        try:
            logger.info(f"正在获取股票{symbol}的实时数据...")
            df = ak.stock_zh_a_spot_em()
            stock_data = df[df['代码'] == symbol]
            return stock_data
        except Exception as e:
            logger.error(f"获取股票{symbol}实时数据失败: {e}")
            return pd.DataFrame()
    
    def calculate_indicators(self, df):
        """
        计算技术指标（KDJ和MACD）
        
        Args:
            df: 股票历史数据
            
        Returns:
            pandas.DataFrame: 包含技术指标的数据
        """
        if df.empty:
            return df
        
        try:
            # 计算KDJ指标
            kdj = KDJ(period=9, signal=3)
            df_with_kdj = kdj.calculate(df)
            
            # 计算MACD指标
            macd = MACD(fast=12, slow=26, signal=9)
            df_with_indicators = macd.calculate(df_with_kdj)
            
            return df_with_indicators
            
        except Exception as e:
            logger.error(f"计算技术指标失败: {e}")
            return df
