#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
量化选股策略 - 主程序入口
超跌买入选股策略：KDJ J值为负 + MACD金叉 + 2个月跌幅>20%
"""

import logging
import os
import sys
from datetime import datetime

import pandas as pd

from config.config import STRATEGY_CONFIG, OUTPUT_CONFIG
from data.data_fetcher import AStockDataFetcher
from strategy.oversold_buy import OversoldBuyStrategy


def setup_logging():
    """配置日志"""
    # 创建输出目录
    os.makedirs("output", exist_ok=True)
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(OUTPUT_CONFIG["log_file"], encoding='utf-8')
        ]
    )
    return logging.getLogger(__name__)


def save_results(selected_stocks, all_results, output_dir="output"):
    """
    保存选股结果
    
    Args:
        selected_stocks: 选中的股票列表
        all_results: 所有分析结果
        output_dir: 输出目录
    """
    # 创建输出目录
    os.makedirs(output_dir, exist_ok=True)
    
    # 保存选中的股票
    if selected_stocks:
        df_selected = pd.DataFrame(selected_stocks)
        selected_file = os.path.join(output_dir, "选股结果.csv")
        df_selected.to_csv(selected_file, index=False, encoding='utf-8-sig')
        print(f"\n选股结果已保存至: {selected_file}")
    
    # 保存所有分析结果
    if all_results:
        df_all = pd.DataFrame(all_results)
        all_file = os.path.join(output_dir, "全部分析结果.csv")
        df_all.to_csv(all_file, index=False, encoding='utf-8-sig')
        print(f"全部分析结果已保存至: {all_file}")


def main():
    """主函数"""
    # 设置日志
    logger = setup_logging()
    
    print("=" * 80)
    print("        量化选股策略 - 超跌买入策略")
    print("=" * 80)
    print("\n选股条件:")
    print("  1. KDJ指标J值为负数（超卖区域）")
    print("  2. MACD金叉或即将金叉")
    print("  3. 前2个月跌幅超过20%（超跌）")
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
    
    # 获取所有股票代码
    symbols = stock_list['代码'].tolist()
    logger.info(f"共获取到{len(symbols)}只股票")
    
    # 询问用户选择模式
    print("\n请选择运行模式:")
    print("  1. 测试模式 - 只分析前100只股票（快速测试）")
    print("  2. 全量模式 - 分析所有股票（耗时较长）")
    
    try:
        choice = input("\n请输入选择 (1 或 2，默认为1): ").strip()
        if choice == "2":
            # 全量模式
            test_symbols = symbols
            logger.info("已选择全量模式")
        else:
            # 测试模式
            test_symbols = symbols[:100]
            logger.info(f"已选择测试模式，将分析{len(test_symbols)}只股票")
    except:
        # 默认测试模式
        test_symbols = symbols[:100]
        logger.info(f"默认使用测试模式，将分析{len(test_symbols)}只股票")
    
    # 获取历史数据
    logger.info("正在获取股票历史数据，请稍候...")
    
    # 计算日期范围（获取足够的历史数据用于计算2个月跌幅）
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - pd.Timedelta(days=180)).strftime("%Y%m%d")
    
    stock_data = data_fetcher.get_multiple_stocks_history(
        symbols=test_symbols,
        start_date=start_date,
        end_date=end_date
    )
    
    logger.info(f"成功获取{len(stock_data)}只股票的历史数据")
    
    if not stock_data:
        logger.error("未获取到任何股票数据，程序退出")
        return
    
    # 初始化选股策略
    logger.info("正在初始化选股策略...")
    strategy = OversoldBuyStrategy(config=STRATEGY_CONFIG)
    
    # 运行选股策略
    logger.info("开始运行选股策略...")
    selected_stocks, all_results = strategy.run(stock_data, verbose=True)
    
    # 生成报告
    report = strategy.generate_report(selected_stocks)
    print("\n" + report)
    
    # 保存结果
    save_results(selected_stocks, all_results)
    
    # 保存报告到文件
    report_file = os.path.join("output", "选股报告.txt")
    with open(report_file, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f"选股报告已保存至: {report_file}")
    
    print("\n" + "=" * 80)
    print("程序运行完成！")
    print("=" * 80)


if __name__ == "__main__":
    main()