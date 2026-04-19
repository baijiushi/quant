#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
量化选股策略 - 回测程序
用于验证选股策略的历史表现，计算胜率等指标
"""

import logging
import os
import sys
from datetime import datetime, timedelta

import pandas as pd

from config.config import STRATEGY_CONFIG
from data.data_fetcher import AStockDataFetcher
from strategy.oversold_buy import OversoldBuyStrategy
from backtest.backtester import Backtester


def setup_logging():
    """配置日志"""
    os.makedirs("output", exist_ok=True)
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("output/backtest.log", encoding='utf-8')
        ]
    )
    return logging.getLogger(__name__)


def save_backtest_results(results, output_dir="output"):
    """保存回测结果"""
    os.makedirs(output_dir, exist_ok=True)
    
    # 保存汇总结果
    if isinstance(results, dict) and 'total_selected' in results:
        # 单周期结果
        filename = "回测结果.csv"
        if results['details']:
            df = pd.DataFrame(results['details'])
            df.to_csv(os.path.join(output_dir, filename), index=False, encoding='utf-8-sig')
    else:
        # 多周期结果
        for period, result in results.items():
            if result['details']:
                filename = f"回测结果_{period}.csv"
                df = pd.DataFrame(result['details'])
                df.to_csv(os.path.join(output_dir, filename), index=False, encoding='utf-8-sig')


def main():
    """主函数"""
    logger = setup_logging()
    
    print("=" * 80)
    print("        量化选股策略 - 回测程序")
    print("=" * 80)
    print("\n回测说明:")
    print("  1. 选择一个历史日期，模拟在该日期运行选股策略")
    print("  2. 统计选出的股票在之后N天的涨跌情况")
    print("  3. 计算胜率、平均收益等指标")
    print("\n" + "=" * 80)
    
    # 初始化数据获取器
    logger.info("正在初始化数据获取器...")
    data_fetcher = AStockDataFetcher()
    
    # 获取股票列表
    logger.info("正在获取A股股票列表...")
    stock_list = data_fetcher.get_stock_list()
    
    if stock_list.empty:
        logger.error("获取股票列表失败，程序退出")
        return
    
    symbols = stock_list['代码'].tolist()
    logger.info(f"共获取到{len(symbols)}只股票")
    
    # 选择分析模式
    print("\n请选择运行模式:")
    print("  1. 测试模式 - 只分析前100只股票（快速测试）")
    print("  2. 全量模式 - 分析所有股票（耗时较长）")
    
    try:
        choice = input("\n请输入选择 (1 或 2，默认为1): ").strip()
        if choice == "2":
            test_symbols = symbols
            logger.info("已选择全量模式")
        else:
            test_symbols = symbols[:100]
            logger.info(f"已选择测试模式，将分析{len(test_symbols)}只股票")
    except:
        test_symbols = symbols[:100]
        logger.info(f"默认使用测试模式，将分析{len(test_symbols)}只股票")
    
    # 选择回测周期
    print("\n请选择回测周期:")
    print("  1. 一周 (5个交易日)")
    print("  2. 一个月 (21个交易日)")
    print("  3. 两个月 (42个交易日)")
    print("  4. 全部周期（1周/1月/2月）")
    
    try:
        period_choice = input("\n请输入选择 (1/2/3/4，默认为4): ").strip()
        
        period_map = {
            '1': ['1w'],
            '2': ['1m'],
            '3': ['2m'],
            '4': ['1w', '1m', '2m']
        }
        selected_periods = period_map.get(period_choice, ['1w', '1m', '2m'])
    except:
        selected_periods = ['1w', '1m', '2m']
    
    # 获取历史数据
    logger.info("正在获取股票历史数据，请稍候...")
    
    # 获取足够的历史数据（至少需要6个月用于回测）
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=240)).strftime("%Y%m%d")
    
    stock_data = data_fetcher.get_multiple_stocks_history(
        symbols=test_symbols,
        start_date=start_date,
        end_date=end_date
    )
    
    logger.info(f"成功获取{len(stock_data)}只股票的历史数据")
    
    if not stock_data:
        logger.error("未获取到任何股票数据，程序退出")
        return
    
    # 初始化策略和回测器
    logger.info("正在初始化选股策略...")
    strategy = OversoldBuyStrategy(config=STRATEGY_CONFIG)
    backtester = Backtester(strategy)
    
    # 选择回测日期
    print("\n请选择回测日期:")
    print("  1. 昨天")
    print("  2. 一周前")
    print("  3. 一个月前")
    print("  4. 两个月前")
    print("  5. 自定义日期")
    
    try:
        date_choice = input("\n请输入选择 (1/2/3/4/5，默认为3): ").strip()
        
        date_offset = {
            '1': 1,
            '2': 7,
            '3': 30,
            '4': 60,
            '5': None
        }
        
        offset = date_offset.get(date_choice, 30)
        
        if offset is None:
            # 自定义日期
            custom_date = input("请输入日期 (格式: YYYYMMDD): ").strip()
            backtest_date = custom_date
        else:
            backtest_date = (datetime.now() - timedelta(days=offset)).strftime("%Y-%m-%d")
    except:
        backtest_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    
    print(f"\n回测日期: {backtest_date}")
    print(f"回测周期: {', '.join(selected_periods)}")
    print("\n" + "=" * 80)
    
    # 运行回测
    logger.info("开始运行回测...")
    
    if len(selected_periods) == 1:
        # 单周期回测
        period_days = {'1w': 5, '1m': 21, '2m': 42}
        days = period_days.get(selected_periods[0], 5)
        backtester.holding_period = days
        
        results = backtester.run_backtest(stock_data, backtest_date)
        results['holding_period'] = selected_periods[0]
    else:
        # 多周期回测
        results = backtester.run_multi_period_backtest(
            stock_data, 
            end_date=backtest_date,
            periods=selected_periods
        )
    
    # 生成报告
    report = backtester.generate_backtest_report(results)
    print("\n" + report)
    
    # 保存结果
    save_backtest_results(results)
    
    # 保存报告
    report_file = os.path.join("output", "回测报告.txt")
    with open(report_file, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f"\n回测报告已保存至: {report_file}")
    
    print("\n" + "=" * 80)
    print("回测完成！")
    print("=" * 80)


if __name__ == "__main__":
    main()