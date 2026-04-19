# 回测模块
import pandas as pd
import numpy as np
import logging
from datetime import datetime, timedelta
import sys
import os

# 添加项目根目录到路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from indicators.kdj import KDJ
from indicators.macd import MACD

logger = logging.getLogger(__name__)


class Backtester:
    """
    策略回测类
    
    用于评估选股策略的历史表现，计算胜率、收益率等指标
    """
    
    def __init__(self, strategy, holding_period=5):
        """
        初始化回测器
        
        Args:
            strategy: 选股策略对象
            holding_period: 持仓周期（交易日），默认5天（一周）
        """
        self.strategy = strategy
        self.holding_period = holding_period
        self.results = []
        
    def run_backtest(self, stock_data_dict, backtest_end_date, lookback_days=5):
        """
        运行回测
        
        回测逻辑：
        1. 在backtest_end_date那天运行选股策略
        2. 找出符合策略条件的股票
        3. 计算这些股票在未来holding_period天的收益率
        4. 统计胜率和平均收益
        
        Args:
            stock_data_dict: 股票数据字典
            backtest_end_date: 回测截止日期（模拟在这个日期选股）
            lookback_days: 用于策略判断的数据窗口期
            
        Returns:
            dict: 回测结果
        """
        backtest_date = pd.to_datetime(backtest_end_date)
        results = []
        
        for symbol, df in stock_data_dict.items():
            try:
                # 确保数据包含回测日期
                if df.empty:
                    continue
                    
                # 找到回测日期或之前最近的交易日
                available_dates = df.index[df.index <= backtest_date]
                if len(available_dates) == 0:
                    continue
                    
                # 获取回测日期的数据
                test_date = available_dates[-1]
                test_idx = df.index.get_loc(test_date)
                
                # 确保有足够的历史数据和未来数据
                min_history = max(self.strategy.decline_period, 30)
                if test_idx < min_history:
                    continue
                if test_idx + self.holding_period >= len(df):
                    continue
                
                # 截取到回测日期的数据（用于策略判断）
                df_history = df.iloc[:test_idx + 1].copy()
                
                # 计算技术指标
                df_with_indicators = self.strategy.kdj.calculate(df_history)
                df_with_indicators = self.strategy.macd.calculate(df_with_indicators)
                
                # 检查是否符合选股条件
                is_oversold = self.strategy.is_oversold(df_with_indicators)
                has_negative_j = self.strategy.has_negative_j(df_with_indicators)
                has_golden_cross = self.strategy.has_macd_golden_cross(df_with_indicators)
                
                # 如果符合选股条件
                if is_oversold and has_negative_j and has_golden_cross:
                    # 获取买入价格（回测日期收盘价）
                    buy_price = df.iloc[test_date]['close']
                    
                    # 获取卖出价格（holding_period天后的收盘价）
                    sell_date_idx = test_idx + self.holding_period
                    sell_price = df.iloc[sell_date_idx]['close']
                    
                    # 计算收益率
                    return_rate = (sell_price - buy_price) / buy_price
                    
                    # 获取期间最高价和最低价
                    holding_df = df.iloc[test_idx:test_idx + self.holding_period + 1]
                    max_price = holding_df['high'].max()
                    min_price = holding_df['low'].min()
                    max_profit = (max_price - buy_price) / buy_price
                    max_drawdown = (min_price - buy_price) / buy_price
                    
                    results.append({
                        'symbol': symbol,
                        'buy_date': test_date,
                        'buy_price': buy_price,
                        'sell_date': df.index[sell_date_idx],
                        'sell_price': sell_price,
                        'return_rate': return_rate,
                        'return_pct': f"{return_rate * 100:.2f}%",
                        'max_profit': max_profit,
                        'max_profit_pct': f"{max_profit * 100:.2f}%",
                        'max_drawdown': max_drawdown,
                        'max_drawdown_pct': f"{max_drawdown * 100:.2f}%",
                        'is_win': return_rate > 0
                    })
                    
            except Exception as e:
                continue
        
        # 计算统计指标
        if results:
            win_count = sum(1 for r in results if r['is_win'])
            total_count = len(results)
            win_rate = win_count / total_count
            avg_return = np.mean([r['return_rate'] for r in results])
            avg_max_profit = np.mean([r['max_profit'] for r in results])
            avg_max_drawdown = np.mean([r['max_drawdown'] for r in results])
            
            summary = {
                'backtest_date': backtest_end_date,
                'holding_period': self.holding_period,
                'total_selected': total_count,
                'win_count': win_count,
                'lose_count': total_count - win_count,
                'win_rate': win_rate,
                'win_rate_pct': f"{win_rate * 100:.2f}%",
                'avg_return': avg_return,
                'avg_return_pct': f"{avg_return * 100:.2f}%",
                'avg_max_profit': avg_max_profit,
                'avg_max_profit_pct': f"{avg_max_profit * 100:.2f}%",
                'avg_max_drawdown': avg_max_drawdown,
                'avg_max_drawdown_pct': f"{avg_max_drawdown * 100:.2f}%",
                'details': results
            }
        else:
            summary = {
                'backtest_date': backtest_end_date,
                'holding_period': self.holding_period,
                'total_selected': 0,
                'win_count': 0,
                'lose_count': 0,
                'win_rate': 0,
                'win_rate_pct': "0.00%",
                'avg_return': 0,
                'avg_return_pct': "0.00%",
                'avg_max_profit': 0,
                'avg_max_profit_pct': "0.00%",
                'avg_max_drawdown': 0,
                'avg_max_drawdown_pct': "0.00%",
                'details': []
            }
        
        return summary
    
    def run_multi_period_backtest(self, stock_data_dict, end_date=None, periods=None):
        """
        运行多周期回测
        
        Args:
            stock_data_dict: 股票数据字典
            end_date: 结束日期，默认为今天
            periods: 回测周期列表，如 ['1w', '1m', '2m']
            
        Returns:
            dict: 各周期回测结果
        """
        if end_date is None:
            end_date = datetime.now()
        
        if periods is None:
            periods = ['1w', '1m', '2m']
        
        # 周期映射到天数
        period_days = {
            '1w': 5,      # 1周 = 5个交易日
            '2w': 10,     # 2周 = 10个交易日
            '1m': 21,     # 1月 ≈ 21个交易日
            '2m': 42,     # 2月 ≈ 42个交易日
            '3m': 63,     # 3月 ≈ 63个交易日
        }
        
        all_results = {}
        
        for period in periods:
            days = period_days.get(period, 5)
            logger.info(f"正在回测 {period} ({days}个交易日) 持仓周期...")
            
            # 更新持仓周期
            self.holding_period = days
            
            # 运行回测
            result = self.run_backtest(stock_data_dict, end_date)
            all_results[period] = result
            
            logger.info(f"{period} 回测完成: 选出{result['total_selected']}只，胜率{result['win_rate_pct']}")
        
        return all_results
    
    def generate_backtest_report(self, results):
        """
        生成回测报告
        
        Args:
            results: 回测结果（单周期或多周期）
            
        Returns:
            str: 格式化的报告文本
        """
        report = []
        report.append("=" * 80)
        report.append("                    回测报告")
        report.append("=" * 80)
        report.append(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        report.append("\n" + "-" * 80)
        
        # 判断是单周期还是多周期结果
        if 'total_selected' in results:
            # 单周期结果
            report.append(self._format_single_period_report(results))
        else:
            # 多周期结果
            report.append("                    各周期回测汇总")
            report.append("-" * 80)
            
            # 汇总表格
            report.append(f"\n{'周期':<10} {'选出股票':<12} {'盈利':<10} {'亏损':<10} {'胜率':<12} {'平均收益':<12}")
            report.append("-" * 80)
            
            for period, result in results.items():
                report.append(
                    f"{period:<10} "
                    f"{result['total_selected']:<12} "
                    f"{result['win_count']:<10} "
                    f"{result['lose_count']:<10} "
                    f"{result['win_rate_pct']:<12} "
                    f"{result['avg_return_pct']:<12}"
                )
            
            report.append("-" * 80)
            
            # 详细结果
            for period, result in results.items():
                report.append(f"\n\n{'='*40} {period} 持仓周期详细结果 {'='*40}")
                report.append(self._format_single_period_report(result))
        
        report.append("\n" + "=" * 80)
        report.append("风险提示：回测结果不代表未来表现，仅供参考")
        report.append("=" * 80)
        
        return "\n".join(report)
    
    def _format_single_period_report(self, result):
        """
        格式化单周期报告
        """
        report = []
        
        report.append(f"\n回测日期: {result['backtest_date']}")
        report.append(f"持仓周期: {result['holding_period']} 个交易日")
        report.append(f"\n【汇总统计】")
        report.append(f"  选出股票数: {result['total_selected']}")
        report.append(f"  盈利股票数: {result['win_count']}")
        report.append(f"  亏损股票数: {result['lose_count']}")
        report.append(f"  胜率: {result['win_rate_pct']}")
        report.append(f"  平均收益率: {result['avg_return_pct']}")
        report.append(f"  平均最大盈利: {result['avg_max_profit_pct']}")
        report.append(f"  平均最大回撤: {result['avg_max_drawdown_pct']}")
        
        if result['details']:
            report.append(f"\n【个股明细】")
            report.append("-" * 80)
            report.append(f"{'股票代码':<12} {'买入价':<10} {'卖出价':<10} {'收益率':<12} {'最大盈利':<12} {'最大回撤':<12} {'结果':<6}")
            report.append("-" * 80)
            
            for detail in result['details']:
                win_lose = "盈利" if detail['is_win'] else "亏损"
                report.append(
                    f"{detail['symbol']:<12} "
                    f"{detail['buy_price']:<10.2f} "
                    f"{detail['sell_price']:<10.2f} "
                    f"{detail['return_pct']:<12} "
                    f"{detail['max_profit_pct']:<12} "
                    f"{detail['max_drawdown_pct']:<12} "
                    f"{win_lose:<6}"
                )
        
        return "\n".join(report)