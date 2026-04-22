# MACD指标计算模块
import pandas as pd
import numpy as np


class MACD:
    """MACD指标计算类"""
    
    def __init__(self, fast=12, slow=26, signal=9):
        """
        初始化MACD计算器
        
        Args:
            fast: 快线周期，默认12
            slow: 慢线周期，默认26
            signal: 信号线周期，默认9
        """
        self.fast = fast
        self.slow = slow
        self.signal = signal
    
    def calculate(self, df):
        """
        计算MACD指标
        
        Args:
            df: DataFrame，需包含'close'列
            
        Returns:
            DataFrame: 添加了'DIF', 'DEA', 'MACD'列的数据
        """
        if df.empty:
            return df
        
        # 复制数据避免修改原数据
        result = df.copy()
        
        # 获取收盘价
        close = result['close'].astype(float)
        
        # 计算EMA
        # EMA = 2/(N+1) * 收盘价 + (N-1)/(N+1) * 前一日EMA
        ema_fast = close.ewm(span=self.fast, adjust=False).mean()
        ema_slow = close.ewm(span=self.slow, adjust=False).mean()
        
        # 计算DIF (差离值)
        # DIF = EMA(12) - EMA(26)
        DIF = ema_fast - ema_slow
        
        # 计算DEA (讯号线)
        # DEA = DIF的9日EMA
        DEA = DIF.ewm(span=self.signal, adjust=False).mean()
        
        # 计算MACD柱状图
        # MACD = 2 * (DIF - DEA)
        MACD_hist = 2 * (DIF - DEA)
        
        # 添加到结果数据框
        result['DIF'] = DIF
        result['DEA'] = DEA
        result['MACD'] = MACD_hist
        
        return result
    
    @staticmethod
    def check_golden_cross(df, window=5):
        """
        检查MACD金叉（严格模式：DIF在近N日内真实上穿DEA）

        金叉定义：DIF从下方上穿DEA（前一日 DIF < DEA，当日 DIF >= DEA）
        
        Args:
            df: DataFrame，需包含'DIF', 'DEA'列
            window: 检查窗口期，默认5天
            
        Returns:
            bool: 近window日内是否出现真实金叉
        """
        if df.empty or 'DIF' not in df.columns or 'DEA' not in df.columns:
            return False
        
        recent_data = df.tail(window + 1)  # 多取一行以便判断前一日
        
        if len(recent_data) < 2:
            return False
        
        DIF = recent_data['DIF'].values
        DEA = recent_data['DEA'].values
        
        # 严格金叉：前一日 DIF < DEA，当日 DIF >= DEA
        for i in range(1, len(recent_data)):
            if DIF[i - 1] < DEA[i - 1] and DIF[i] >= DEA[i]:
                return True
        
        return False
    
    @staticmethod
    def get_golden_cross_stocks(stock_data_dict, window=5):
        """
        筛选MACD金叉的股票
        
        Args:
            stock_data_dict: 字典，键为股票代码，值为包含MACD指标的DataFrame
            window: 检查窗口期
            
        Returns:
            list: MACD金叉的股票代码列表
        """
        golden_cross_stocks = []
        
        for symbol, df in stock_data_dict.items():
            if MACD.check_golden_cross(df, window):
                # 获取最新数据
                latest = df.iloc[-1]
                golden_cross_stocks.append({
                    'symbol': symbol,
                    'DIF': latest['DIF'],
                    'DEA': latest['DEA'],
                    'MACD': latest['MACD']
                })
        
        return golden_cross_stocks