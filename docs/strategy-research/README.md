# 可选策略研究索引

本目录用于决定下一批策略是否值得进入工作台。它不是收益承诺，也不是已经完成的策略清单。每份说明都区分了论文依据、项目可用数据、实现步骤和必须完成的回测。

## 建议顺序

| 优先级 | 方案 | 当前可行性 | 主要原因 |
| --- | --- | --- | --- |
| A | [价格动量多因子](01_价格动量多因子.md) | 可直接实现 | 现有 SQLite 日线 OHLCV 足够，能复用 52 周新高与回测缓存 |
| A | [低波动与流动性过滤](04_低波动与流动性.md) | 可直接实现大部分 | 波动率和成交额已有；市场 Beta 需补指数日线 |
| B | [质量价值动量](02_质量价值动量.md) | 需扩展数据层 | 需要 `daily_basic`、财务指标和公告日期的时点快照 |
| B | [Piotroski F-Score 价值策略](03_Piotroski_FScore价值.md) | 需扩展数据层 | 需要资产负债表、利润表、现金流量表及报告期对齐 |
| B | [行业相对强度与景气共振](05_行业相对强度与景气共振.md) | 部分可做 | 可复用 AI 景气研究，但需稳定的行业分类和行业指数历史 |

## 推荐第一项

先实现“价格动量多因子”，而不是立刻把五套策略都写入生产代码。它能验证策略框架是否真正支持：

1. 选股日截面排名，而不是把不同日期混在一起排名。
2. 指标缓存随参数和数据版本正确失效。
3. 多持有期回测、交易成本、次日成交和涨跌停约束。
4. 因子分层收益、Rank IC、换手率和最大回撤，而不只看胜率。

完成这一条基线后，再接财务因子，定位问题会容易很多。

## 统一研究纪律

- 所有财务数据按实际公告日生效；不能用报告期结束日提前知道财报。
- 选股日收盘后形成信号，默认下一交易日开盘成交，并处理停牌、一字板和涨跌停无法成交。
- 退市、ST、上市不足观察期的股票必须明确处理，股票池要保留历史成分，避免幸存者偏差。
- 每个因子先做单因子检验，再做组合；训练期、验证期和样本外区间分开。
- 参数越多越容易过拟合。优先使用有经济含义、低相关且数据稳定的少量因子。

## 主要资料

- Jegadeesh 与 Titman，[Returns to Buying Winners and Selling Losers](https://doi.org/10.1111/j.1540-6261.1993.tb04702.x)
- George 与 Hwang，[The 52-Week High and Momentum Investing](https://doi.org/10.1111/j.1540-6261.2004.00695.x)
- Fama 与 French，[A Five-Factor Asset Pricing Model](https://doi.org/10.1016/j.jfineco.2014.10.010)
- Hou、Xue 与 Zhang，[Digesting Anomalies: An Investment Approach](https://doi.org/10.1093/rfs/hhu068)
- Novy-Marx，[The Other Side of Value: The Gross Profitability Premium](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1598056)
- Piotroski，[Value Investing: The Use of Historical Financial Statement Information](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=249455)

