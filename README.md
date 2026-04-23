# A股量化选股策略系统

当前项目只保留一条主线：

- 唯一入口：`run_all.py`
- 唯一策略：`B1` 策略
- 唯一数据主线：`TUShare -> data/raw -> pipeline -> dashboard`

## 当前策略

项目当前只保留 `B1` 选股策略，核心条件为：

1. `KDJ` 的 `J` 值处于超卖区
2. 日线知行均线多头排列
3. 周线均线多头排列确认
4. 结合滚动成交额做流动性过滤

策略参数在 [`config/rules_preselect.yaml`](E:\百九十\Desktop\量化选股策略\oversell\config\rules_preselect.yaml) 中维护。

## 项目结构

- [`run_all.py`](E:\百九十\Desktop\量化选股策略\oversell\run_all.py) 一键运行入口
- [`pipeline/fetch_data.py`](E:\百九十\Desktop\量化选股策略\oversell\pipeline\fetch_data.py) 拉取 / 更新日线数据
- [`pipeline/cli.py`](E:\百九十\Desktop\量化选股策略\oversell\pipeline\cli.py) Pipeline 命令行入口
- [`pipeline/select_stock.py`](E:\百九十\Desktop\量化选股策略\oversell\pipeline\select_stock.py) B1 量化初选
- [`pipeline/Selector.py`](E:\百九十\Desktop\量化选股策略\oversell\pipeline\Selector.py) B1 指标与条件判断
- [`dashboard/app.py`](E:\百九十\Desktop\量化选股策略\oversell\dashboard\app.py) Streamlit 看盘界面
- [`data/data_fetcher.py`](E:\百九十\Desktop\量化选股策略\oversell\data\data_fetcher.py) TUShare 数据抓取器

## 安装

```bash
pip install -r requirements.txt
```

## 配置 Token

请在项目根目录的 [`.env.local`](E:\百九十\Desktop\量化选股策略\oversell\.env.local:1) 中填写：

```env
TUSHARE_TOKEN=你的token
```

该文件不会提交到 GitHub。

## 运行方式

完整流程：

```bash
python run_all.py
```

只运行选股，不启动看板：

```bash
python run_all.py --no-dashboard
```

跳过数据更新，直接用本地数据选股：

```bash
python run_all.py --skip-fetch --no-dashboard
```

## 配置文件

- [`config/fetch_data.yaml`](E:\百九十\Desktop\量化选股策略\oversell\config\fetch_data.yaml) 数据拉取配置
- [`config/rules_preselect.yaml`](E:\百九十\Desktop\量化选股策略\oversell\config\rules_preselect.yaml) B1 策略配置
- [`config/dashboard.yaml`](E:\百九十\Desktop\量化选股策略\oversell\config\dashboard.yaml) 看板配置

## 数据目录

所有运行数据都固定落在项目根目录下，不再依赖启动命令时的当前工作目录：

- [`data/raw`](E:\百九十\Desktop\量化选股策略\oversell\data\raw:1) 个股日线数据
- [`data/stocklist.csv`](E:\百九十\Desktop\量化选股策略\oversell\data\stocklist.csv:1) 股票列表
- [`data/candidates`](E:\百九十\Desktop\量化选股策略\oversell\data\candidates:1) 候选结果
- [`data/failures`](E:\百九十\Desktop\量化选股策略\oversell\data\failures:1) 抓取失败记录

## 项目规划

后续任务路线见 [`项目任务表.md`](E:\百九十\Desktop\量化选股策略\oversell\项目任务表.md)。

## 风险提示

本项目仅用于研究与选股，不构成投资建议。
