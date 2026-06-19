# A股量化选股策略系统

当前项目主线是 `TUShare -> data/raw -> pipeline -> FastAPI -> Vue`。

## 功能

- `run_all.py` 保留为命令行入口。
- `backend/app.py` 提供本地 FastAPI 服务。
- `web/` 提供 Vue 3 + Vite + TypeScript 控制台。
- 多策略架构支持 `b1` 和 `volume_new_high`，策略通过统一注册表和标准 OHLCV 数据调用。
- B1 策略支持 KDJ、日线均线多头、周线确认、MACD、成交量过滤、板块过滤。
- 缩量新高策略实现 `-corr(HIGH, VOLUME, 10) * rank(stddev(HIGH, 10))`，并支持新高窗口、缩量阈值和最低评分参数。
- 数据模式支持 `existing`、`incremental`、`refresh`、`cache-only`。
- DeepSeek AI 评分支持赛道景气度分析和候选股“超景气价值投机”评分。

## 安装

Python 依赖：

```bash
pip install -r requirements.txt
```

前端依赖：

```bash
cd web
npm install
```

## Token

在项目根目录的 `.env.local` 中填写：

```env
TUSHARE_TOKEN=你的token
DEEPSEEK_API_KEY=你的DeepSeek API Key
```

`.env.local` 已加入 `.gitignore`，不会提交到 GitHub。

## 命令行运行

交互式选择数据模式：

```bash
python run_all.py --no-dashboard
```

直接使用本地数据：

```bash
python run_all.py --data-mode existing --no-dashboard
```

指定策略运行：

```bash
python run_all.py --data-mode existing --strategy-id b1 --no-dashboard
python run_all.py --data-mode existing --strategy-id volume_new_high --no-dashboard
```

增量更新：

```bash
python run_all.py --data-mode incremental --no-dashboard
```

强制重拉：

```bash
python run_all.py --data-mode refresh --no-dashboard
```

仅使用缓存：

```bash
python run_all.py --data-mode cache-only --no-dashboard
```

## 网页控制台

一键开发启动：

```bash
python start_web.py
```

Windows 可以直接双击：

```text
start_console.bat
```

停止后台服务：

```text
stop_console.bat
```

开发模式会同时启动：

- 后端：http://127.0.0.1:8000
- 前端：http://127.0.0.1:5173

也可以手动启动后端：

```bash
uvicorn backend.app:app --reload
```

手动启动前端：

```bash
cd web
npm run dev
```

访问：

```text
http://127.0.0.1:5173
```

构建后只启动后端：

```bash
cd web
npm run build
cd ..
python start_web.py --prod
```

此时访问：

```text
http://127.0.0.1:8000
```

## DeepSeek AI 评分

AI 评分配置位于 `config/ai_scoring.yaml`。评分结果会写入 `data/ai_scoring/`，该目录已加入 `.gitignore`。

赛道景气度输入默认读取：

```text
data/news_inputs/
```

可把 Wind、Bloomberg、高盛、摩根士丹利、金十数据等来源中你有权限使用的文本、报告摘要、CSV 或 JSON 放入该目录。`config/ai_scoring.yaml` 也支持配置公开 `source_urls`。付费或登录源不在第一版里硬抓，避免不稳定和合规问题。

命令行运行：

```bash
python -m ai_scoring.run_ai_scoring --strategy-id b1 --max-candidates 20
```

Windows 脚本：

```text
scripts\run_ai_scoring.bat --strategy-id volume_new_high
```

评分口径：

```text
最终分数 = ((行业景气度 + 业务纯度 + 估值水位 + 龙头 + 辨识度) - 风险扣分 * 0.2) * 流动性系数 / 5
```

行业景气度为 0 时，系统要求 AI 给出 `avoid`，即便其他项高分也不作为买入标的。

## 浏览器自动化测试

安装 Playwright 浏览器后运行：

```bash
cd web
..\scripts\install_playwright.bat
cd ..
scripts\test_browser.bat
```

## 配置文件

- `config/fetch_data.yaml`：数据抓取、限频、重试、多线程配置。
- `config/rules_preselect.yaml`：全局参数、当前激活策略、各策略参数。
- `data/stocklist.csv`：股票列表缓存。
- `data/raw/`：个股日线 CSV。
- `data/candidates/`：候选股结果，包含全局 latest 和按策略区分的 latest。
- `data/failures/`：抓取失败报告。

## API

- `GET /api/strategies`：查看已注册策略和默认参数。
- `GET /api/config` / `PUT /api/config`：读取或保存全局配置与策略配置。
- `POST /api/runs`：启动任务，可传 `strategy_id`。
- `POST /api/runs/{run_id}/cancel`：终止正在运行的任务。
- `GET /api/candidates/latest?strategy_id=b1`：读取指定策略最新结果。
- `GET /api/ai/sector-scores/latest` / `POST /api/ai/sector-scores/refresh`：读取或更新赛道景气度评分。
- `GET /api/ai/candidate-scores/latest` / `POST /api/ai/candidate-scores/score`：读取或生成候选股 AI 评分。
- `POST /api/backtests` / `GET /api/backtests/{id}`：回测接口已预留，当前返回未实现。

## 风险提示

本项目仅用于研究与选股，不构成投资建议。
