# 超跌买入选股策略
import pandas as pd
import numpy as np
import logging
from datetime import datetime
import sys
import os

# 添加项目根目录到路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from indicators.kdj import KDJ
from indicators.macd import MACD

logger = logging.getLogger(__name__)


class OversoldBuyStrategy:
    """
    超跌买入选股策略
    
    选股条件：
    1. KDJ指标J值为负数（超卖区域）
    2. MACD金叉或即将金叉
    3. 前2个月跌幅超过20%（超跌）
    """
    
    def __init__(self, config=None):
        """
        初始化策略
        
        Args:
            config: 策略配置参数字典
        """
        if config is None:
            # 默认配置
            config = {
                "kdj_period": 9,
                "kdj_signal": 3,
                "macd_fast": 12,
                "macd_slow": 26,
                "macd_signal": 9,
                "decline_period": 42,  # 约2个月的交易日
                "decline_threshold": -0.20,  # -20%
            }
        
        self.config = config
        
        # 初始化指标计算器
        self.kdj = KDJ(
            period=config.get("kdj_period", 9),
            signal=config.get("kdj_signal", 3)
        )
        self.macd = MACD(
            fast=config.get("macd_fast", 12),
            slow=config.get("macd_slow", 26),
            signal=config.get("macd_signal", 9)
        )
        
        # 策略参数
        self.decline_period = config.get("decline_period", 42)
        self.decline_threshold = config.get("decline_threshold", -0.20)
    
    def calculate_decline(self, df):
        """
        计算跌幅
        
        Args:
            df: DataFrame，需包含'close'列
            
        Returns:
            float: 跌幅百分比（如-0.25表示跌幅25%）
        """
        if df.empty or len(df) < self.decline_period:
            return 0
        
        # 获取当前收盘价和N天前的收盘价
        current_price = df['close'].iloc[-1]
        past_price = df['close'].iloc[-self.decline_period]
        
        # 计算跌幅
        decline = (current_price - past_price) / past_price
        
        return decline
    
    def is_oversold(self, df):
        """
        判断是否超跌
        
        条件：前2个月跌幅超过阈值
        
        Args:
            df: DataFrame
            
        Returns:
            bool: 是否超跌
        """
        decline = self.calculate_decline(df)
        return decline <= self.decline_threshold
    
    def has_negative_j(self, df):
        """
        判断J值是否为负数
        
        Args:
            df: DataFrame，需包含KDJ指标
            
        Returns:
            bool: J值是否为负数
        """
        if df.empty or 'J' not in df.columns:
            return False
        
        latest_j = df['J'].iloc[-1]
        return latest_j < 0
    
    def has_macd_golden_cross(self, df, window=5):
        """
        判断是否有MACD金叉或即将金叉
        
        Args:
            df: DataFrame，需包含MACD指标
            window: 检查窗口期
            
        Returns:
            bool: 是否有金叉或即将金叉
        """
        return MACD.check_golden_cross(df, window)
    
    def analyze_stock(self, df, symbol=""):
        """
        分析单只股票是否符合选股条件
        
        Args:
            df: 股票历史数据
            symbol: 股票代码
            
        Returns:
            dict: 分析结果
        """
        if df.empty or len(df) < max(self.decline_period, 30):
            return None
        
        # 计算技术指标
        df_with_kdj = self.kdj.calculate(df)
        df_with_macd = self.macd.calculate(df_with_kdj)
        
        # 检查各个条件
        is_oversold = self.is_oversold(df_with_macd)
        has_negative_j = self.has_negative_j(df_with_macd)
        has_golden_cross = self.has_macd_golden_cross(df_with_macd)
        
        # 计算跌幅
        decline = self.calculate_decline(df_with_macd)
        
        # 获取最新指标值
        latest = df_with_macd.iloc[-1]
        
        result = {
            'symbol': symbol,
            'date': df_with_macd.index[-1],
            'close': latest['close'],
            'decline': decline,
            'decline_pct': f"{decline * 100:.2f}%",
            'K': latest.get('K', 0),
            'D': latest.get('D', 0),
            'J': latest.get('J', 0),
            'DIF': latest.get('DIF', 0),
            'DEA': latest.get('DEA', 0),
            'MACD': latest.get('MACD', 0),
            'is_oversold': is_oversold,
            'has_negative_j': has_negative_j,
            'has_golden_cross': has_golden_cross,
            'selected': is_oversold and has_negative_j and has_golden_cross
        }
        
        return result
    
    def run(self, stock_data_dict, verbose=True):
        """
        运行选股策略
        
        Args:
            stock_data_dict: 字典，键为股票代码，值为DataFrame
            verbose: 是否打印详细信息
            
        Returns:
            list: 选中的股票列表
        """
        selected_stocks = []
        all_results = []
        
        total = len(stock_data_dict)
        
        for i, (symbol, df) in enumerate(stock_data_dict.items(), 1):
            if verbose and i % 50 == 0:
                logger.info(f"正在分析第{i}/{total}只股票: {symbol}")
            
            try:
                result = self.analyze_stock(df, symbol)
                if result:
                    all_results.append(result)
                    if result['selected']:
                        selected_stocks.append(result)
            except Exception as e:
                logger.error(f"分析股票{symbol}时出错: {e}")
        
        # 按跌幅排序（跌幅最大的排在前面）
        selected_stocks.sort(key=lambda x: x['decline'])
        
        if verbose:
            logger.info(f"\n选股完成！")
            logger.info(f"总共分析: {total}只股票")
            logger.info(f"符合条件: {len(selected_stocks)}只股票")
        
        return selected_stocks, all_results
    
    def generate_report(self, selected_stocks):
        """
        生成选股报告
        
        Args:
            selected_stocks: 选中的股票列表
            
        Returns:
            str: 格式化的报告文本
        """
        if not selected_stocks:
            return "没有找到符合条件的股票"
        
        report = []
        report.append("=" * 80)
        report.append("超跌买入选股报告")
        report.append(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        report.append("=" * 80)
        report.append(f"\n选股条件:")
        report.append(f"  1. KDJ指标J值为负数")
        report.append(f"  2. MACD金叉或即将金叉")
        report.append(f"  3. 前2个月跌幅超过{abs(self.decline_threshold * 100):.0f}%")
        report.append(f"\n共选出 {len(selected_stocks)} 只股票:")
        report.append("-" * 80)
        
        for i, stock in enumerate(selected_stocks, 1):
            report.append(f"\n{i}. 股票代码: {stock['symbol']}")
            report.append(f"   最新收盘价: {stock['close']:.2f}")
            report.append(f"   2个月跌幅: {stock['decline_pct']}")
            report.append(f"   KDJ指标 - K: {stock['K']:.2f}, D: {stock['D']:.2f}, J: {stock['J']:.2f}")
            report.append(f"   MACD指标 - DIF: {stock['DIF']:.4f}, DEA: {stock['DEA']:.4f}")
            report.append(f"   数据日期: {stock['date'].strftime('%Y-%m-%d') if hasattr(stock['date'], 'strftime') else stock['date']}")
        
        report.append("\n" + "=" * 80)
        report.append("风险提示:")
        report.append("  本策略仅供参考，不构成投资建议。股市有风险，投资需谨慎。")
        report.append("=" * 80)
        
        return "\n".join(report)