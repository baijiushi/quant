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
