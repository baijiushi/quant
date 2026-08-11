import { expect, test } from "@playwright/test";

test("console page renders candidates and chart controls", async ({ page }) => {
  await page.goto("/");

  await expect(page.getByRole("heading", { name: "多策略量化选股控制台" })).toBeVisible();
  await expect(page.getByText("运行参数")).toBeVisible();
  await expect(page.getByText("任务状态")).toBeVisible();
  await expect(page.getByText("候选股票")).toBeVisible();
  await expect(page.getByText("DeepSeek AI 评分")).toBeVisible();

  await expect(page.getByLabel("选股策略")).toBeVisible();
  await page.getByLabel("数据模式").selectOption("existing");
  await expect(page.getByRole("button", { name: "开始运行" })).toBeEnabled();

  const rows = page.locator("tbody tr").filter({ has: page.locator("td") });
  await expect(rows.first()).toBeVisible();
  await expect(page.getByText("滚动成交额(亿元)")).toBeVisible();
  await expect(page.locator(".chart")).toBeVisible();
});

test("console restores active run after reopening the page", async ({ page }) => {
  const activeRun = {
    run_id: "active123",
    status: "running",
    stage: "策略筛选",
    started_at: "2026-05-17T10:00:00",
    finished_at: null,
    error: null,
    logs: ["10:00:01 启动 pipeline，data_mode=incremental", "10:00:10 B1选股进度 250/2000"],
    result: null
  };

  await page.route("**/api/runs/current", async (route) => {
    await route.fulfill({ json: { run: activeRun } });
  });
  await page.route("**/api/runs/active123", async (route) => {
    await route.fulfill({ json: activeRun });
  });

  await page.goto("/");

  await expect(page.locator(".status .stage-badge")).toHaveText("策略筛选");
  await expect(page.getByText("任务：active123")).toBeVisible();
  await expect(page.getByText("检测到后台任务正在运行，已恢复任务状态")).toBeVisible();
  await expect(page.getByText("B1选股进度 250/2000")).toBeVisible();
  await expect(page.getByRole("button", { name: "运行中" }).first()).toBeDisabled();
});

test("console switches to volume new-high strategy parameters", async ({ page }) => {
  await page.goto("/");

  await page.getByLabel("选股策略").selectOption("volume_new_high");
  await expect(page.getByText("缩量新高 / 波动率过滤")).toBeVisible();
  await expect(page.getByLabel("相关系数窗口")).toBeVisible();
  await expect(page.getByLabel("波动率窗口")).toBeVisible();
  await expect(page.getByLabel("最大量比")).toBeVisible();
  await expect(page.getByRole("columnheader", { name: "相关系数" })).toBeVisible();
});

test("console can request run cancellation", async ({ page }) => {
  const runningRun = {
    run_id: "cancel123",
    status: "running",
    stage: "数据更新",
    started_at: "2026-06-03T10:00:00",
    finished_at: null,
    error: null,
    logs: ["10:00:01 启动 pipeline，data_mode=incremental"],
    result: null
  };
  const cancellingRun = {
    ...runningRun,
    status: "cancelling",
    stage: "正在终止",
    logs: [...runningRun.logs, "10:00:05 收到终止请求，等待当前步骤安全退出"]
  };
  let currentRun = runningRun;
  let cancelRequested = false;

  await page.route("**/api/runs/current", async (route) => {
    await route.fulfill({ json: { run: currentRun } });
  });
  await page.route("**/api/runs/cancel123/cancel", async (route) => {
    cancelRequested = true;
    currentRun = cancellingRun;
    await route.fulfill({ json: cancellingRun });
  });
  await page.route("**/api/runs/cancel123", async (route) => {
    await route.fulfill({ json: currentRun });
  });
  await page.goto("/");

  await expect(page.getByRole("button", { name: "终止任务" })).toBeVisible();
  await page.getByRole("button", { name: "终止任务" }).click();
  expect(cancelRequested).toBeTruthy();
  await expect(page.locator(".status .stage-badge")).toHaveText("正在终止");
  await expect(page.getByText("已发送终止请求，等待当前步骤安全退出")).toBeVisible();
});

test("console can save and delete a research evidence note", async ({ page }) => {
  await page.goto("/");

  await page.getByLabel("素材标题").fill("浏览器回归测试素材");
  await page.getByLabel("研究摘要与原始要点").fill("这是自动化测试素材，会在本测试结束前删除。");
  await page.getByRole("button", { name: "保存研究素材" }).click();

  await expect(page.getByText("研究素材已保存，下次更新赛道评分会自动纳入。")).toBeVisible();
  await expect(page.getByText("浏览器回归测试素材")).toBeVisible();

  await page.getByRole("button", { name: "删除" }).click();
  await expect(page.getByText("浏览器回归测试素材")).not.toBeVisible();
});

test("backtest route runs a daily replay and shows ranked returns", async ({ page }) => {
  const running = {
    backtest_id: "backtest123",
    strategy_id: "b1",
    start_date: "2026-07-01",
    end_date: "2026-07-10",
    holding_days: 3,
    status: "running",
    stage: "逐日选股",
    progress: 64,
    processed_days: 4,
    total_days: 8,
    started_at: "2026-07-11T10:00:00",
    finished_at: null,
    error: null,
    logs: ["10:00:01 读取回测行情", "10:00:04 逐日回测 4/8：选出 2 只"]
  };
  const completed = {
    ...running,
    status: "success",
    stage: "回测完成",
    progress: 100,
    processed_days: 8,
    finished_at: "2026-07-11T10:00:08",
    logs: [...running.logs, "10:00:08 回测完成，共 2 条信号"]
  };
  const result = {
    backtest_id: "backtest123",
    generated_at: "2026-07-11T10:00:08",
    request: { strategy_id: "b1", strategy_name: "B1 战法", start_date: "2026-07-01", end_date: "2026-07-10", holding_days: 3 },
    metrics: {
      signal_count: 2, completed_count: 2, win_count: 1, loss_count: 1,
      win_rate_pct: 50, average_return_pct: 4.25, median_return_pct: 4.25, profit_loss_ratio: 2.4
    },
    horizon_stats: [
      { day: 1, sample_count: 2, win_rate_pct: 50, average_return_pct: 1.2, median_return_pct: 1.2 },
      { day: 2, sample_count: 2, win_rate_pct: 50, average_return_pct: 2.4, median_return_pct: 2.4 },
      { day: 3, sample_count: 2, win_rate_pct: 50, average_return_pct: 4.25, median_return_pct: 4.25 }
    ],
    daily_stats: [{ signal_date: "2026-07-01", selected_count: 2, completed_count: 2, win_rate_pct: 50, average_return_pct: 4.25 }],
    stock_ranking: [
      { rank: 1, code: "000001", name: "平安银行", trade_count: 1, win_rate_pct: 100, average_return_pct: 12, best_return_pct: 12, worst_return_pct: 12 }
    ],
    trades: [
      {
        rank: 1, signal_rank: 1, signal_date: "2026-07-01", code: "000001", name: "平安银行",
        strategy_score: 8.6, signal_close: 10, entry_date: "2026-07-02", entry_open: 10,
        exit_date: "2026-07-06", exit_close: 11.2, final_return_pct: 12, max_gain_pct: 14,
        max_drawdown_pct: -2, status: "completed", note: "按下一交易日开盘买入",
        daily_returns: [
          { day: 1, date: "2026-07-02", close: 10.4, return_pct: 4, carried: false },
          { day: 2, date: "2026-07-03", close: 10.8, return_pct: 8, carried: false },
          { day: 3, date: "2026-07-06", close: 11.2, return_pct: 12, carried: false }
        ]
      },
      {
        rank: 2, signal_rank: 2, signal_date: "2026-07-01", code: "000002", name: "万科A",
        strategy_score: 7.1, signal_close: 8, entry_date: "2026-07-02", entry_open: 8,
        exit_date: "2026-07-06", exit_close: 7.72, final_return_pct: -3.5, max_gain_pct: 1,
        max_drawdown_pct: -6, status: "completed", note: "按下一交易日开盘买入",
        daily_returns: [
          { day: 1, date: "2026-07-02", close: 7.92, return_pct: -1, carried: false },
          { day: 2, date: "2026-07-03", close: 7.84, return_pct: -2, carried: false },
          { day: 3, date: "2026-07-06", close: 7.72, return_pct: -3.5, carried: false }
        ]
      }
    ],
    meta: { assumptions: ["下一市场交易日开盘价买入"] }
  };

  await page.route("**/api/backtests/current", (route) => route.fulfill({ json: { backtest: null } }));
  await page.route("**/api/backtests/meta", (route) => route.fulfill({ json: { first_date: "2025-01-01", latest_date: "2026-07-10", suggested_start_date: "2026-05-01" } }));
  await page.route("**/api/backtests/backtest123/result", (route) => route.fulfill({ json: result }));
  await page.route("**/api/backtests/backtest123", (route) => route.fulfill({ json: completed }));
  await page.route("**/api/backtests", async (route) => {
    expect(route.request().method()).toBe("POST");
    const payload = route.request().postDataJSON();
    expect(payload.holding_days).toBe(3);
    await route.fulfill({ json: running });
  });

  await page.goto("/backtest");
  await expect(page.getByRole("heading", { name: "逐日选股回测" })).toBeVisible();
  await page.getByLabel("开始日期").fill("2026-07-01");
  await page.getByLabel("结束日期").fill("2026-07-10");
  await page.getByLabel("持有交易日 X").fill("3");
  await page.getByRole("button", { name: "开始逐日回测" }).click();

  await expect(page.getByText("回测完成，共 2 条信号")).toBeVisible();
  await expect(page.getByText("+12.00%").first()).toBeVisible();
  await expect(page.getByRole("columnheader", { name: "D3" })).toBeVisible();
  await expect(page.getByText("平安银行").first()).toBeVisible();
  await page.getByRole("button", { name: "每日汇总" }).click();
  await expect(page.getByRole("columnheader", { name: "选出数量" })).toBeVisible();
  await page.getByRole("button", { name: "个股排行" }).click();
  await expect(page.getByRole("columnheader", { name: "出现次数" })).toBeVisible();
});
