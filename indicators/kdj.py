# KDJ指标计算模块
import pandas as pd
import numpy as np


class KDJ:
    """KDJ随机指标计算类"""
    
    def __init__(self, period=9, signal=3):
        """
        初始化KDJ计算器
        
        Args:
            period: KDJ的周期，默认9
            signal: 平滑周期，默认3
        """
        self.period = period
        self.signal = signal
    
    def calculate(self, df):
        """
        计算KDJ指标
        
        Args:
            df: DataFrame，需包含'high', 'low', 'close'列
            
        Returns:
            DataFrame: 添加了'K', 'D', 'J'列的数据
        """
        if df.empty:
            return df
        
        # 复制数据避免修改原数据
        result = df.copy()
        
        # 获取最高价、最低价、收盘价
        high = result['high']
        low = result['low']
        close = result['close']
        
        # 计算N日内的最低价和最高价
        low_min = low.rolling(window=self.period, min_periods=1).min()
        high_max = high.rolling(window=self.period, min_periods=1).max()
        
        # 计算RSV (Raw Stochastic Value)
        # RSV = (收盘价 - N日最低价) / (N日最高价 - N日最低价) * 100
        rsv = (close - low_min) / (high_max - low_min) * 100
        # 处理除零情况
        rsv = rsv.fillna(50)
        
        # 计算K值: 当日K值 = 2/3 * 前一日K值 + 1/3 * 当日RSV
        K = pd.Series(index=result.index, dtype=float)
        K.iloc[0] = 50  # 第一个K值初始化为50
        for i in range(1, len(result)):
            K.iloc[i] = (2/3) * K.iloc[i-1] + (1/3) * rsv.iloc[i]
        
        # 计算D值: 当日D值 = 2/3 * 前一日D值 + 1/3 * 当日K值
        D = pd.Series(index=result.index, dtype=float)
        D.iloc[0] = 50  # 第一个D值初始化为50
        for i in range(1, len(result)):
            D.iloc[i] = (2/3) * D.iloc[i-1] + (1/3) * K.iloc[i]
        
        # 计算J值: J = 3 * K - 2 * D
        J = 3 * K - 2 * D
        
        # 添加到结果数据框
        result['K'] = K
        result['D'] = D
        result['J'] = J
        
        return result
    
    @staticmethod
    def get_negative_j_stocks(stock_data_dict):
        """
        筛选J值为负数的股票
        
        Args:
            stock_data_dict: 字典，键为股票代码，值为包含KDJ指标的DataFrame
            
        Returns:
            list: J值为负数的股票代码列表
        """
        negative_j_stocks = []
        
        for symbol, df in stock_data_dict.items():
            if df.empty or 'J' not in df.columns:
                continue
            
            # 获取最新的J值
            latest_j = df['J'].iloc[-1]
            
            # 判断J值是否为负数
            if latest_j < 0:
                negative_j_stocks.append({
                    'symbol': symbol,
                    'J': latest_j
                })
        
        return negative_j_stocks