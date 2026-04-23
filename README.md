# A股量化选股策略系统

基于KDJ和MACD指标的超跌买入选股策略，适用于A股市场。

## 选股策略

超跌买入策略：寻找市场中被过度抛售的股票，预期在超卖区域出现反弹机会。

### 选股条件

1. KDJ指标J值小于10（超卖区域）
2. MACD金叉或即将金叉（DIF上穿DEA或即将上穿）
3. 前2个月跌幅超过15%（超跌）

## 项目结构

量化选股策略/
- main.py                 # 主程序入口（选股程序）
- backtest_main.py        # 回测程序入口
- requirements.txt        # Python依赖包列表
- .gitignore             # Git忽略文件配置
- README.md              # 项目说明文档

config/                # 配置模块
- __init__.py
- config.py            # 策略参数配置

data/                  # 数据获取模块
- __init__.py
- data_fetcher.py      # 股票数据获取器

indicators/            # 技术指标模块
- __init__.py
- kdj.py               # KDJ指标计算
- macd.py              # MACD指标计算

strategy/              # 选股策略模块
- __init__.py
- oversold_buy.py      # 超跌买入策略

backtest/              # 回测模块
- __init__.py
- backtester.py        # 回测器

data/cache/            # 数据缓存目录（自动创建）

output/                # 输出目录（自动创建）

## 依赖包

tushare>=1.4.25
pandas>=1.5.0
numpy>=1.23.0

## 安装和运行

1. 安装依赖：
pip install -r requirements.txt

2. 配置 TUShare Token：

优先推荐在项目根目录的 `.env.local` 中填写：
`TUSHARE_TOKEN=你的token`

如果你更想用系统环境变量，也可以在 Windows PowerShell 中执行：
`setx TUSHARE_TOKEN "你的token"`

3. 运行选股程序：
python main.py

4. 运行回测程序：
python backtest_main.py

## 数据存储位置

- 股票列表：`data/stocklist.csv`
- 个股历史数据：`data/raw/<股票代码>_qfq.csv`
- 选股候选：`data/candidates/`
- 输出结果：`output/`

当前项目已改为使用 **TUShare** 获取股票列表和历史日线数据，并按单股票 CSV 文件保存，便于直接查看和管理。

## 项目规划

后续任务路线整理在：

- [`项目任务表.md`](E:\百九十\Desktop\量化选股策略\oversell\项目任务表.md)

## 风险提示

本策略仅供参考，不构成投资建议。股市有风险，投资需谨慎。
