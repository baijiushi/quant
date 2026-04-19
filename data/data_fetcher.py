# A股数据获取模块
import akshare as ak
import pandas as pd
from datetime import datetime, timedelta
import time
import logging

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class AStockDataFetcher:
    """A股数据获取类"""
    
    def __init__(self):
        """初始化数据获取器"""
        self.stock_list = None
        
    def get_stock_list(self):
        """
        获取A股股票列表
        
        Returns:
            pandas.DataFrame: 股票列表数据
        """
        try:
            logger.info("正在获取A股股票列表...")
            # 获取沪深A股实时行情数据
            self.stock_list = ak.stock_zh_a_spot_em()
            logger.info(f"成功获取{len(self.stock_list)}只股票信息")
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
            # 使用akshare获取个股历史数据
            df = ak.stock_zh_a_hist(
                symbol=symbol, 
                period="daily", 
                start_date=start_date, 
                end_date=end_date, 
                adjust=adjust
            )
            
            if df is not None and not df.empty:
                # 重命名列以统一格式
                df = df.rename(columns={
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
                    '换手率': 'turnover'
                })
                # 转换日期格式
                df['date'] = pd.to_datetime(df['date'])
                # 设置日期为索引
                df = df.set_index('date')
                # 按日期排序
                df = df.sort_index()
                
                logger.info(f"成功获取股票{symbol}的{len(df)}条历史数据")
                return df
            else:
                logger.warning(f"股票{symbol}未获取到历史数据")
                return pd.DataFrame()
                
        except Exception as e:
            logger.error(f"获取股票{symbol}历史数据失败: {e}")
            return pd.DataFrame()
    
    def get_multiple_stocks_history(self, symbols, start_date=None, end_date=None, adjust="qfq"):
        """
        批量获取多只股票的历史数据
        
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
        
        for i, symbol in enumerate(symbols, 1):
            logger.info(f"正在获取第{i}/{total}只股票: {symbol}")
            df = self.get_stock_history(symbol, start_date, end_date, adjust)
            if not df.empty:
                stock_data[symbol] = df
            # 避免请求过快被限制
            if i % 10 == 0:
                time.sleep(1)
                
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