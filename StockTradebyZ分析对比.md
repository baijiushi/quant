# StockTradebyZ 项目分析与对比

> 分析日期：2026-04-22

---

## 一、StockTradebyZ 项目概览

**项目定位**：面向 A 股的**半自动选股系统**，核心亮点是"量化初选 + AI 图表复评"两阶段漏斗。

### 完整流程（run_all.py）

```
步骤 1  pipeline/fetch_kline.py          拉取日线 K 线数据（Tushare）
步骤 2  pipeline/cli.py preselect        量化初选，生成候选列表 JSON
步骤 3  dashboard/export_kline_charts.py 导出候选股 K 线 PNG
步骤 4  agent/gemini_review.py           Gemini 看图打分
步骤 5  run_all.py 打印推荐结果           读取 suggestion.json 输出
```

---

## 二、代码架构详解

```
StockTradebyZ/
├── run_all.py                   一键全流程入口
├── requirements.txt
│
├── config/                      YAML 配置层（参数与代码完全分离）
│   ├── fetch_kline.yaml         数据拉取配置（Tushare token、日期范围等）
│   ├── rules_preselect.yaml     量化初选参数（B1/砖型图阈值）
│   ├── gemini_review.yaml       AI 评审参数（模型、评分门槛）
│   └── dashboard.yaml           看盘界面配置
│
├── pipeline/                    数据与选股核心
│   ├── fetch_kline.py           Tushare 日线下载，存为 data/raw/*.csv
│   ├── schemas.py               数据结构定义（Candidate / CandidateRun dataclass）
│   ├── Selector.py              选股器框架：B1Selector、BrickChartSelector
│   │                            含 Numba 加速的 KDJ / 砖型图 / 成交量核心循环
│   ├── pipeline_core.py         基础设施：MarketDataPreparer、TopTurnoverPoolBuilder
│   │                            多进程/线程并行预计算
│   ├── select_stock.py          业务入口：加载配置 → 加载数据 → 运行 Selector → 返回候选
│   ├── cli.py                   命令行包装，接收参数后调用 select_stock
│   ├── io.py                    文件读写（候选列表 JSON 序列化/反序列化）
│   ├── pipeline_io.py           pipeline 级别 I/O 工具
│   └── stocklist.csv            基础股票名称映射表
│
├── dashboard/                   可视化层
│   ├── app.py                   Streamlit 看盘界面
│   ├── export_kline_charts.py   批量生成候选股 K 线 PNG（供 AI 阅图）
│   └── components/charts.py     Plotly K 线图组件
│
└── agent/                       AI 复评层
    ├── base_reviewer.py         BaseReviewer 抽象基类
    ├── gemini_review.py         Gemini 实现：读图 → 打分 → 输出 JSON
    └── prompt.md                评分提示词（5 维度：趋势/位置/量价/风险/综合）
```

### 关键设计模式

| 模式 | 体现 |
|------|------|
| 数据类（dataclass）| `Candidate`、`CandidateRun` 严格定义候选结构，避免裸 dict 传递 |
| 配置与代码分离 | 所有数值参数写在 YAML，代码零硬编码 |
| Numba JIT 加速 | KDJ 递推、砖型图核心、成交量循环均用 `@njit` 加速，大盘扫描毫秒级 |
| 多进程并行 | `ProcessPoolExecutor` 并行预计算所有股票特征 |
| 两阶段漏斗 | 量化规则粗筛（快、无成本）→ AI 精筛（慢、有成本），避免 API 浪费 |

---

## 三、量化初选策略详解

### 3.1 B1 策略（KDJ + 知行均线多头排列）

**核心逻辑**：

```
条件 1  KDJ J 值低分位（J < 15 或 J 处于历史 10% 分位以下）→ 超卖
条件 2  日线知行均线（14/28/57/114）多头排列 → 趋势向上
条件 3  周线均线多头排列（5/10/20）→ 中期趋势确认
（可选）流动性过滤：仅对成交额前 top_m 只股票运行
```

**与本项目的关联**：B1 策略的"J 值低分位"与本项目的"J 值为负"逻辑同源，都是 KDJ 超卖区域捕捉，但 B1 额外加了**多头排列趋势确认**，选股条件更严格。

### 3.2 砖型图策略（BrickChartSelector）

**核心逻辑**：

```
条件 1  砖型图连续绿柱后出现红柱（今日红柱 ≥ 0.5 × 昨日绿柱绝对值）
条件 2  绿柱前至少有 1 根连续绿柱（确认反转）
条件 3  当日涨幅 < 20%（避免追高）
条件 4  知行线 + 周线多头排列
```

砖型图是独创形态指标，用来捕捉**动能切换点**（空头能量衰竭，多头开始接管）。

### 3.3 流动性过滤池

用 `TopTurnoverPoolBuilder` 对所有股票做**滚动 43 日成交额排名**，只对前 5000 只流动性好的股票运行初选，避免在垃圾股上浪费计算。

---

## 四、AI 图表复评（Gemini）

Gemini 收到候选股 K 线 PNG 后，按以下 **5 个维度**打 1-5 分：

| 维度 | 说明 |
|------|------|
| 趋势结构（Trend Structure）| 均线多头/死叉/空头 |
| 价格位置结构（Price Position）| 低位突破 / 高位风险 |
| 量价关系（Volume-Price）| 放量突破 / 缩量回调 |
| 追高风险（Chase Risk）| 距离低点涨幅、压力位 |
| 综合研判（Overall）| 综合以上给出 1-5 总分 |

总分 ≥ `suggest_min_score`（默认 4.0）的股票进入最终推荐列表，输出到 `suggestion.json`。

---

## 五、与本项目的对比

### 5.1 核心差异对比表

| 维度 | 本项目（量化选股策略）| StockTradebyZ |
|------|----------------------|---------------|
| **数据来源** | AkShare（多接口备用）| Tushare（需 Token） |
| **缓存策略** | 增量更新，单文件按股票存储 | 全量 CSV，每次拉取覆盖 |
| **选股策略** | 超跌买入（J<0 + MACD金叉 + 2月跌幅>20%）| B1（KDJ低分位 + 均线多头）/ 砖型图 |
| **趋势判断** | MACD 金叉（DIF 上穿 DEA）| 知行均线（14/28/57/114）多头排列 |
| **位置判断** | 2个月跌幅 > 20% | J 值历史分位 < 10% |
| **趋势确认** | 无周线确认 | 周线均线多头排列 |
| **计算性能** | Python 纯向量化 | Numba JIT 加速 |
| **并行处理** | ThreadPoolExecutor（线程池）| ProcessPoolExecutor（进程池）|
| **AI 复评** | 无 | Gemini 看图打分（5维度）|
| **可视化** | 无 | Streamlit 看盘界面 + Plotly K 线图 |
| **配置管理** | config.py（Python dict）| YAML 文件（完全外部化）|
| **数据结构** | 裸 dict / DataFrame | 严格 dataclass（Candidate）|
| **回测支持** | 有 backtester.py | 无内置回测 |
| **输出形式** | CSV + TXT 报告 | JSON + K 线 PNG + 可视化 |

### 5.2 策略逻辑上的本质差异

**本项目——超跌反转策略**

```
逻辑前提：股票已经大幅下跌（超跌），底部出现反转信号
核心哲学：买入恐慌后的反弹，追求短期超额收益
适合行情：熊市尾部、调整接近尾声时效果最好
风险点  ：可能买入"价值陷阱"（跌了还会继续跌）
```

**StockTradebyZ——趋势跟随策略**

```
逻辑前提：股票已经从底部启动，均线形成多头排列
核心哲学：买入已经确认趋势的股票，追求中期持续上涨
适合行情：牛市初中期、板块轮动启动阶段
风险点  ：可能买在趋势的中后段，向上空间有限
```

### 5.3 各自优势

**本项目的优势：**
- 有完整的回测框架，可量化验证策略有效性
- 数据获取免费，无需 Tushare Token
- 增量缓存节省重复请求
- 选股时机更早（在底部未启动时就介入）

**StockTradebyZ 的优势：**
- AI 图表复评大幅降低人工看图负担
- Numba 加速使全市场扫描更快
- YAML 配置让参数调整无需改代码
- dataclass 数据结构更规范、类型安全
- Streamlit 看盘界面可视化友好
- 两阶段漏斗（量化粗筛 + AI 精筛）互补性强

---

## 六、可以借鉴的改进方向

以下是可以从 StockTradebyZ 引入到本项目的设计：

1. **YAML 配置化**：将 `config.py` 中的 Python dict 改为 YAML，让非技术用户也能调参

2. **加入均线多头排列条件**：在现有 KDJ + MACD 基础上，增加知行线（短/中/长/超长）多头排列作为趋势确认，减少假信号

3. **加入周线确认**：在日线信号触发后，检查周线均线是否也处于多头状态，提高信号可靠性

4. **流动性过滤**：参考 `TopTurnoverPoolBuilder`，按成交额排名只扫描前 N 只流动性好的股票

5. **dataclass 数据结构**：将选股结果从裸 dict 改为 `dataclass`，类型更安全，IDE 提示更友好

6. **AI 图表复评集成**：选股完成后导出 K 线图，接入大模型做二次人工智能复核（可选功能）

---

*本文档由 GitHub Copilot 自动生成，基于对两个项目源码的静态分析。*
