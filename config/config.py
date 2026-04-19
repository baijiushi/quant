# 配置文件

# 数据获取配置
DATA_CONFIG = {
    "market": "A股",  # 市场类型
    "start_date": "20240101",  # 默认开始日期
    "end_date": "20260419",  # 默认结束日期
}

# 选股策略参数
STRATEGY_CONFIG = {
    # KDJ参数
    "kdj_period": 9,  # KDJ周期
    "kdj_signal": 3,  # KDJ平滑周期
    
    # MACD参数
    "macd_fast": 12,  # MACD快线周期
    "macd_slow": 26,  # MACD慢线周期
    "macd_signal": 9,  # MACD信号线周期
    
    # 超跌条件
    "decline_period": 42,  # 跌幅计算周期（交易日，约2个月）
    "decline_threshold": -0.20,  # 跌幅阈值（-20%）
    
    # KDJ条件
    "j_negative": True,  # J值为负数
    
    # MACD条件
    "macd_golden_cross": True,  # MACD金叉
}

# 输出配置
OUTPUT_CONFIG = {
    "result_file": "output/选股结果.csv",  # 结果输出文件
    "log_file": "output/strategy.log",  # 日志文件
}