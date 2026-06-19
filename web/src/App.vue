<script setup lang="ts">
import * as echarts from "echarts";
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";

type DataMode = "existing" | "incremental" | "refresh" | "cache-only";
type Market = "main" | "gem" | "star" | "bse";

interface ConfigPayload {
  data_mode: DataMode;
  fetch: Record<string, number | string | boolean>;
  active_strategy: string;
  global: {
    data_dir?: string;
    stock_list_file?: string;
    output_dir?: string;
    adjust?: string;
    top_m?: number;
    n_turnover_days?: number;
    markets?: Market[];
  };
  strategies: Record<string, Record<string, number | string | boolean>>;
}

interface StrategyInfo {
  id: string;
  name: string;
  description: string;
  default_config: Record<string, number | string | boolean>;
}

interface RunStatus {
  run_id: string;
  status: "queued" | "running" | "cancelling" | "success" | "failed" | "cancelled";
  stage: string;
  started_at?: string | null;
  finished_at?: string | null;
  error?: string | null;
  logs: string[];
  result?: CandidateRun | null;
}

interface Candidate {
  code: string;
  name: string;
  date: string;
  strategy: string;
  close: number;
  turnover_n: number;
  score: number;
  extra?: Record<string, number | string | boolean>;
}

interface CandidateRun {
  run_date: string;
  pick_date: string;
  candidates: Candidate[];
  meta: Record<string, unknown>;
}

interface KlineRow {
  date: string;
  open: number;
  close: number;
  high: number;
  low: number;
  volume?: number;
  amount?: number;
}

interface SectorAIScore {
  sector: string;
  score: number;
  opportunity_type?: string;
  catalysts?: string[] | string;
  evidence?: string[] | string;
  risk_notes?: string[] | string;
}

interface CandidateAIScore {
  code: string;
  name: string;
  industry?: string;
  final_score: number;
  decision: "buy" | "watch" | "avoid" | string;
  dimension_scores?: Record<string, number>;
  risk_events?: string[] | string;
  rationale?: string;
  evidence_gaps?: string[] | string;
}

const marketOptions: Array<{ value: Market; label: string }> = [
  { value: "main", label: "主板" },
  { value: "gem", label: "创业板" },
  { value: "star", label: "科创板" },
  { value: "bse", label: "北交所" }
];

const config = ref<ConfigPayload | null>(null);
const strategies = ref<StrategyInfo[]>([]);
const pickDate = ref("");
const runStatus = ref<RunStatus | null>(null);
const latest = ref<CandidateRun | null>(null);
const failures = ref<Record<string, unknown> | null>(null);
const selectedCode = ref("");
const klineRows = ref<KlineRow[]>([]);
const chartEl = ref<HTMLDivElement | null>(null);
const loading = ref(false);
const message = ref("");
const bootLoading = ref(true);
const bootError = ref("");
const aiSectorScores = ref<Record<string, unknown> | null>(null);
const aiCandidateScores = ref<Record<string, unknown> | null>(null);
const aiLoading = ref(false);
const aiError = ref("");
let pollTimer: number | null = null;
let chartResizeObserver: ResizeObserver | null = null;

const fallbackStrategies: StrategyInfo[] = [
  {
    id: "b1",
    name: "B1 战法",
    description: "KDJ 超卖 + 日线/周线多头排列，可选 MACD 与成交量过滤。",
    default_config: {
      kdj_period: 9,
      j_threshold: 10,
      zx_m1: 14,
      zx_m2: 28,
      zx_m3: 57,
      zx_m4: 114,
      require_weekly_ma_bull: true,
      wma_short: 5,
      wma_mid: 10,
      wma_long: 20,
      require_macd_bull: true,
      macd_fast: 12,
      macd_slow: 26,
      macd_signal: 9,
      require_volume_ratio: false,
      volume_ma_window: 20,
      min_volume_ratio: 1.2
    }
  },
  {
    id: "volume_new_high",
    name: "缩量新高",
    description: "缩量创阶段新高，并使用高价-成交量相关性与波动率截面排序打分。",
    default_config: {
      corr_window: 10,
      stddev_window: 10,
      new_high_window: 60,
      volume_ma_window: 20,
      max_volume_ratio: 0.85,
      min_score: 0
    }
  }
];

const candidates = computed(() => latest.value?.candidates ?? []);
const selectedCandidate = computed(() => candidates.value.find((item) => item.code === selectedCode.value));
const isRunning = computed(() => ["queued", "running", "cancelling"].includes(runStatus.value?.status ?? ""));
const isCancelling = computed(() => runStatus.value?.status === "cancelling");
const runLogs = computed(() => runStatus.value?.logs ?? []);
const activeStrategy = computed(() => config.value?.active_strategy ?? "b1");
const activeStrategyInfo = computed(() => strategies.value.find((item) => item.id === activeStrategy.value));
const sectorScoreRows = computed(() => (aiSectorScores.value?.sectors ?? []) as SectorAIScore[]);
const candidateScoreRows = computed(() => (aiCandidateScores.value?.scores ?? []) as CandidateAIScore[]);

async function api<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...init
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || response.statusText);
  }
  return response.json() as Promise<T>;
}

function normalizeConfig(payload: ConfigPayload): ConfigPayload {
  payload.global = payload.global ?? {};
  payload.global.markets = payload.global.markets?.length
    ? payload.global.markets
    : ["main", "gem", "star", "bse"];
  payload.strategies = payload.strategies ?? {};
  for (const item of strategies.value) {
    payload.strategies[item.id] = {
      ...item.default_config,
      ...(payload.strategies[item.id] ?? {})
    };
  }
  payload.active_strategy = payload.active_strategy || strategies.value[0]?.id || "b1";
  return payload;
}

async function loadConfig() {
  const payload = await api<ConfigPayload>("/api/config");
  config.value = normalizeConfig(payload);
}

async function loadStrategies() {
  try {
    const payload = await api<{ strategies: StrategyInfo[] }>("/api/strategies");
    strategies.value = payload.strategies.length ? payload.strategies : fallbackStrategies;
  } catch {
    strategies.value = fallbackStrategies;
  }
}

async function loadLatest() {
  try {
    const strategy = config.value?.active_strategy;
    const suffix = strategy ? `?strategy_id=${encodeURIComponent(strategy)}` : "";
    latest.value = await api<CandidateRun>(`/api/candidates/latest${suffix}`);
    if (!selectedCode.value && candidates.value.length) {
      selectedCode.value = candidates.value[0].code;
    }
  } catch {
    latest.value = null;
  }
}

async function loadFailures() {
  try {
    failures.value = await api<Record<string, unknown>>("/api/failures/latest");
  } catch {
    failures.value = {
      failed_count: 0,
      empty_count: 0,
      failed_symbols: [],
      empty_symbols: [],
      note: "暂无失败报告或后端暂时无法读取失败报告。"
    };
  }
}

async function loadAiScores() {
  const strategy = config.value?.active_strategy;
  const suffix = strategy ? `?strategy_id=${encodeURIComponent(strategy)}` : "";
  try {
    aiSectorScores.value = await api<Record<string, unknown>>("/api/ai/sector-scores/latest");
  } catch {
    aiSectorScores.value = { generated_at: null, sectors: [] };
  }
  try {
    aiCandidateScores.value = await api<Record<string, unknown>>(`/api/ai/candidate-scores/latest${suffix}`);
  } catch {
    aiCandidateScores.value = { generated_at: null, scores: [] };
  }
}

async function refreshSectorScores() {
  aiLoading.value = true;
  aiError.value = "";
  try {
    aiSectorScores.value = await api<Record<string, unknown>>("/api/ai/sector-scores/refresh", {
      method: "POST",
      body: JSON.stringify({})
    });
    message.value = "赛道景气度已更新";
  } catch (error) {
    aiError.value = error instanceof Error ? error.message : String(error);
  } finally {
    aiLoading.value = false;
  }
}

async function scoreCandidatesWithAi() {
  aiLoading.value = true;
  aiError.value = "";
  try {
    aiCandidateScores.value = await api<Record<string, unknown>>("/api/ai/candidate-scores/score", {
      method: "POST",
      body: JSON.stringify({
        strategy_id: config.value?.active_strategy,
        max_candidates: 20
      })
    });
    message.value = "候选股 AI 评分已完成";
  } catch (error) {
    aiError.value = error instanceof Error ? error.message : String(error);
  } finally {
    aiLoading.value = false;
  }
}

function clearPollTimer() {
  if (pollTimer !== null) {
    window.clearInterval(pollTimer);
    pollTimer = null;
  }
}

function clearDisplayedResults() {
  latest.value = null;
  failures.value = null;
  selectedCode.value = "";
  klineRows.value = [];
}

async function saveConfig() {
  if (!config.value) return;
  loading.value = true;
  try {
    config.value = await api<ConfigPayload>("/api/config", {
      method: "PUT",
      body: JSON.stringify(config.value)
    });
    message.value = "配置已保存";
  } finally {
    loading.value = false;
  }
}

async function startRun() {
  if (!config.value) return;
  clearPollTimer();
  loading.value = true;
  message.value = "";
  clearDisplayedResults();
  runStatus.value = {
    run_id: "-",
    status: "queued",
    stage: "提交任务",
    logs: ["正在提交任务..."]
  };
  try {
    runStatus.value = await api<RunStatus>("/api/runs", {
      method: "POST",
      body: JSON.stringify({
        data_mode: config.value.data_mode,
        pick_date: pickDate.value || null,
        strategy_id: config.value.active_strategy,
        config: config.value
      })
    });
    message.value = "任务已提交，正在运行";
    pollRun(runStatus.value.run_id);
  } catch (error) {
    message.value = "任务提交失败";
    runStatus.value = {
      run_id: "-",
      status: "failed",
      stage: "提交失败",
      error: error instanceof Error ? error.message : String(error),
      logs: []
    };
  } finally {
    loading.value = false;
  }
}

async function stopRun() {
  if (!runStatus.value?.run_id || !isRunning.value) return;
  loading.value = true;
  try {
    runStatus.value = await api<RunStatus>(`/api/runs/${runStatus.value.run_id}/cancel`, {
      method: "POST"
    });
    message.value = "已发送终止请求，等待当前步骤安全退出";
  } catch (error) {
    message.value = "终止任务失败";
    if (runStatus.value) {
      runStatus.value.error = error instanceof Error ? error.message : String(error);
    }
  } finally {
    loading.value = false;
  }
}

async function refreshRunStatus(runId: string): Promise<boolean> {
  try {
    runStatus.value = await api<RunStatus>(`/api/runs/${runId}`);
    if (["success", "failed", "cancelled"].includes(runStatus.value.status)) {
      clearPollTimer();
      await loadFailures();
      if (runStatus.value.status === "success") {
        await loadLatest();
      }
      message.value =
        runStatus.value.status === "success"
          ? "运行完成"
          : runStatus.value.status === "cancelled"
            ? "任务已终止"
            : "运行失败";
      return true;
    }
    return false;
  } catch (error) {
    clearPollTimer();
    runStatus.value = {
      run_id: runId,
      status: "failed",
      stage: "后端连接失败",
      error: error instanceof Error ? error.message : String(error),
      logs: runStatus.value?.logs ?? []
    };
    message.value = "后端连接失败或已崩溃";
    return true;
  }
}

function pollRun(runId: string) {
  clearPollTimer();
  void refreshRunStatus(runId);
  pollTimer = window.setInterval(() => {
    void refreshRunStatus(runId);
  }, 1500);
}

async function syncCurrentRun(): Promise<boolean> {
  try {
    const payload = await api<{ run: RunStatus | null }>("/api/runs/current");
    if (!payload.run) {
      runStatus.value = null;
      return false;
    }

    runStatus.value = payload.run;
    if (["queued", "running", "cancelling"].includes(payload.run.status)) {
      clearDisplayedResults();
      message.value = "检测到后台任务正在运行，已恢复任务状态";
      pollRun(payload.run.run_id);
      return true;
    }
    return false;
  } catch {
    return false;
  }
}

async function loadKline(code: string) {
  if (!code) return;
  try {
    const adjust = config.value?.global.adjust ?? "qfq";
    const payload = await api<{ rows: KlineRow[] }>(`/api/stocks/${code}/kline?adjust=${adjust}&limit=220`);
    klineRows.value = payload.rows;
  } catch {
    klineRows.value = [];
  }
}

function renderChart() {
  if (!chartEl.value || !klineRows.value.length) return;
  const chart = echarts.getInstanceByDom(chartEl.value) ?? echarts.init(chartEl.value);
  const dates = klineRows.value.map((row) => row.date);
  const candle = klineRows.value.map((row) => [row.open, row.close, row.low, row.high]);
  const volumeWanShou = klineRows.value.map((row) => (row.volume ?? 0) / 10000);
  const amountYi = klineRows.value.map((row) => (row.amount ?? 0) / 100000);
  chart.resize();
  chart.setOption({
    animation: false,
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "cross" },
      formatter(params: unknown) {
        const items = Array.isArray(params) ? params : [params];
        const first = items[0] as { dataIndex?: number; axisValue?: string } | undefined;
        const index = first?.dataIndex ?? 0;
        const row = klineRows.value[index];
        if (!row) return "";
        return [
          `<strong>${first?.axisValue ?? row.date}</strong>`,
          `开盘价：${Number(row.open).toFixed(2)}`,
          `收盘价：${Number(row.close).toFixed(2)}`,
          `最低价：${Number(row.low).toFixed(2)}`,
          `最高价：${Number(row.high).toFixed(2)}`,
          `成交量：${volumeWanShou[index].toFixed(2)} 万手`,
          `成交额：${amountYi[index].toFixed(2)} 亿元`
        ].join("<br/>");
      }
    },
    grid: [
      { left: 56, right: 28, top: 28, height: 280 },
      { left: 56, right: 28, top: 340, height: 90 }
    ],
    xAxis: [
      { type: "category", data: dates, boundaryGap: true, axisLine: { lineStyle: { color: "#607d8b" } } },
      { type: "category", data: dates, gridIndex: 1, axisLabel: { show: false } }
    ],
    yAxis: [
      {
        name: "价格",
        scale: true,
        axisLine: { lineStyle: { color: "#607d8b" } },
        splitLine: { lineStyle: { color: "#e0e0e0" } }
      },
      {
        name: "成交量(万手)",
        scale: true,
        gridIndex: 1,
        splitLine: { show: false },
        axisLabel: {
          formatter(value: number) {
            return value.toFixed(0);
          }
        }
      }
    ],
    dataZoom: [
      { type: "inside", xAxisIndex: [0, 1], start: 45, end: 100 },
      { show: true, xAxisIndex: [0, 1], start: 45, end: 100, bottom: 8, height: 20 }
    ],
    series: [
      {
        name: "K线",
        type: "candlestick",
        data: candle,
        itemStyle: {
          color: "#c94f3d",
          color0: "#208c71",
          borderColor: "#c94f3d",
          borderColor0: "#208c71"
        }
      },
      {
        name: "成交量",
        type: "bar",
        xAxisIndex: 1,
        yAxisIndex: 1,
        data: volumeWanShou,
        itemStyle: { color: "#607d8b" }
      }
    ]
  });
}

function resizeChart() {
  if (!chartEl.value) return;
  echarts.getInstanceByDom(chartEl.value)?.resize();
}

function toggleMarket(value: Market) {
  if (!config.value) return;
  const markets = new Set(config.value.global.markets ?? []);
  if (markets.has(value)) {
    markets.delete(value);
  } else {
    markets.add(value);
  }
  config.value.global.markets = Array.from(markets);
}

function marketLabel(value: unknown): string {
  const found = marketOptions.find((item) => item.value === value);
  return found?.label ?? String(value ?? "-");
}

function strategyName(value: unknown): string {
  const found = strategies.value.find((item) => item.id === value);
  return found?.name ?? String(value ?? "-");
}

function extraNumber(item: Candidate, key: string): number {
  const value = Number(item.extra?.[key] ?? 0);
  return Number.isFinite(value) ? value : 0;
}

function factorLabel(): string {
  return activeStrategy.value === "volume_new_high" ? "相关系数" : "J";
}

function factorValue(item: Candidate): string {
  if (item.strategy === "volume_new_high") {
    return extraNumber(item, "high_volume_corr").toFixed(3);
  }
  return extraNumber(item, "J").toFixed(1);
}

function turnoverYi(value: unknown): string {
  const numeric = Number(value ?? 0);
  if (!Number.isFinite(numeric)) return "0.00";
  return (numeric / 100000).toFixed(2);
}

function aiListText(value: unknown): string {
  if (Array.isArray(value)) return value.join("；");
  return String(value ?? "-");
}

function decisionLabel(value: unknown): string {
  if (value === "buy") return "可关注买入";
  if (value === "watch") return "观察";
  if (value === "avoid") return "回避";
  return String(value ?? "-");
}

watch(selectedCode, async (code) => {
  if (code) {
    await loadKline(code);
    await nextTick();
    renderChart();
  }
});

watch(klineRows, async () => {
  await nextTick();
  renderChart();
});

onMounted(async () => {
  try {
    bootLoading.value = true;
    bootError.value = "";
    await loadStrategies();
    await loadConfig();
    const restoredActiveRun = await syncCurrentRun();
    if (!restoredActiveRun) {
      await loadLatest();
    }
    await loadFailures();
    if (selectedCode.value) {
      await loadKline(selectedCode.value);
    }
    await loadAiScores();
  } catch (error) {
    bootError.value = error instanceof Error ? error.message : String(error);
  } finally {
    bootLoading.value = false;
    await nextTick();
    if (chartEl.value) {
      chartResizeObserver = new ResizeObserver(() => resizeChart());
      chartResizeObserver.observe(chartEl.value);
    }
    window.addEventListener("resize", resizeChart);
  }
});

onUnmounted(() => {
  clearPollTimer();
  chartResizeObserver?.disconnect();
  chartResizeObserver = null;
  window.removeEventListener("resize", resizeChart);
});
</script>

<template>
  <main class="shell">
    <section class="hero">
      <div>
        <p class="eyebrow">oversell local console</p>
        <h1>多策略量化选股控制台</h1>
      </div>
      <div class="hero-stats">
        <span>候选 {{ candidates.length }}</span>
        <span>模式 {{ config?.data_mode ?? "-" }}</span>
        <span>策略 {{ activeStrategyInfo?.name ?? activeStrategy }}</span>
        <span>日期 {{ latest?.pick_date ?? "-" }}</span>
      </div>
    </section>

    <section v-if="bootLoading" class="panel boot-panel">
      正在加载控制台配置，请稍候...
    </section>

    <section v-else-if="bootError" class="panel boot-panel">
      <h2>控制台加载失败</h2>
      <p class="error">{{ bootError }}</p>
      <p class="hint">
        请确认后端仍在运行，并访问 /api/config 是否能返回配置。这个错误现在会显示在页面里，不会再只剩空白区域。
      </p>
    </section>

    <section v-if="config" class="layout">
      <aside class="panel controls">
        <div class="panel-title">
          <h2>运行参数</h2>
          <button :disabled="loading" @click="saveConfig">保存</button>
        </div>

        <label class="field">
          <span>选股策略</span>
          <select v-model="config.active_strategy" @change="loadLatest">
            <option v-for="item in strategies" :key="item.id" :value="item.id">
              {{ item.name }}
            </option>
          </select>
          <small class="hint">{{ activeStrategyInfo?.description }}</small>
        </label>

        <label class="field">
          <span>数据模式</span>
          <select v-model="config.data_mode">
            <option value="existing">直接使用本地数据</option>
            <option value="incremental">增量更新</option>
            <option value="refresh">强制重新拉取</option>
            <option value="cache-only">仅使用本地缓存</option>
          </select>
          <small class="hint">调参数反复试策略时建议用“直接使用本地数据”，避免每次先更新行情。</small>
        </label>

        <label class="field">
          <span>选股日期</span>
          <input v-model="pickDate" type="date" />
          <small class="hint">留空表示使用本地缓存中的最新交易日；指定日期会按 YYYY-MM-DD 传给后端。</small>
        </label>

        <div class="field">
          <span>板块</span>
          <div class="chips">
            <button
              v-for="item in marketOptions"
              :key="item.value"
              :class="{ active: config.global.markets?.includes(item.value) }"
              type="button"
              @click="toggleMarket(item.value)"
            >
              {{ item.label }}
            </button>
          </div>
        </div>

        <div class="grid-2">
          <label class="field">
            <span>流动性 Top M（按成交额）</span>
            <input v-model.number="config.global.top_m" type="number" min="0" />
            <small class="hint">先按滚动成交额选前 M 只再跑策略。0 表示不过滤；常用 1000-3000。</small>
          </label>
          <label class="field">
            <span>成交额窗口（日）</span>
            <input v-model.number="config.global.n_turnover_days" type="number" min="1" />
            <small class="hint">用于计算滚动成交额，越大越偏长期流动性。示例：20、43、60。</small>
          </label>
        </div>

        <template v-if="config.active_strategy === 'b1'">
          <div class="section-title">B1：KDJ / 均线</div>
          <div class="strategy-note">
            B1 先找 KDJ 的 J 值低位，再要求日线均线多头排列；可选周线确认、MACD 多头和成交量放大。
            参数越严格，候选越少；调试时建议一次只改一个条件。
          </div>
          <div class="grid-2">
            <label class="field">
              <span>KDJ 周期</span>
              <input v-model.number="config.strategies.b1.kdj_period" type="number" min="1" />
              <small class="hint">计算 K/D/J 的窗口。常用 9；越大越平滑，信号更慢。</small>
            </label>
            <label class="field">
              <span>J 阈值</span>
              <input v-model.number="config.strategies.b1.j_threshold" type="number" step="0.5" />
              <small class="hint">J 小于该值才入选。示例：10 更严格，20 更宽松。</small>
            </label>
          </div>
          <div class="grid-4">
            <label class="field compact-field">
              <span>日线 MA1</span>
              <input v-model.number="config.strategies.b1.zx_m1" type="number" min="1" />
            </label>
            <label class="field compact-field">
              <span>日线 MA2</span>
              <input v-model.number="config.strategies.b1.zx_m2" type="number" min="1" />
            </label>
            <label class="field compact-field">
              <span>日线 MA3</span>
              <input v-model.number="config.strategies.b1.zx_m3" type="number" min="1" />
            </label>
            <label class="field compact-field">
              <span>日线 MA4</span>
              <input v-model.number="config.strategies.b1.zx_m4" type="number" min="1" />
            </label>
          </div>
          <small class="hint block-hint">日线多头条件为 MA1 &gt; MA2 &gt; MA3 &gt; MA4。默认 14/28/57/114；周期越长越偏中线。</small>

          <label class="switch">
            <input v-model="config.strategies.b1.require_weekly_ma_bull" type="checkbox" />
            <span>启用周线多头确认</span>
          </label>
          <div class="grid-3">
            <label class="field compact-field">
              <span>周线短均线</span>
              <input v-model.number="config.strategies.b1.wma_short" type="number" min="1" />
            </label>
            <label class="field compact-field">
              <span>周线中均线</span>
              <input v-model.number="config.strategies.b1.wma_mid" type="number" min="1" />
            </label>
            <label class="field compact-field">
              <span>周线长均线</span>
              <input v-model.number="config.strategies.b1.wma_long" type="number" min="1" />
            </label>
          </div>
          <small class="hint block-hint">周线确认能过滤弱趋势股票，但会减少候选。默认 5/10/20 周。</small>

          <label class="switch">
            <input v-model="config.strategies.b1.require_macd_bull" type="checkbox" />
            <span>启用 MACD 多头</span>
          </label>
          <div class="grid-3">
            <label class="field compact-field">
              <span>快线 EMA</span>
              <input v-model.number="config.strategies.b1.macd_fast" type="number" min="1" />
            </label>
            <label class="field compact-field">
              <span>慢线 EMA</span>
              <input v-model.number="config.strategies.b1.macd_slow" type="number" min="1" />
            </label>
            <label class="field compact-field">
              <span>信号线</span>
              <input v-model.number="config.strategies.b1.macd_signal" type="number" min="1" />
            </label>
          </div>
          <small class="hint block-hint">默认 12/26/9。启用后要求 DIF &gt; DEA 且柱体为正，适合过滤下跌反抽。</small>

          <label class="switch">
            <input v-model="config.strategies.b1.require_volume_ratio" type="checkbox" />
            <span>启用成交量过滤</span>
          </label>
          <div class="grid-2">
            <label class="field">
              <span>均量窗口</span>
              <input v-model.number="config.strategies.b1.volume_ma_window" type="number" min="1" />
              <small class="hint">量比基准窗口。常用 10、20、30。</small>
            </label>
            <label class="field">
              <span>最小量比</span>
              <input v-model.number="config.strategies.b1.min_volume_ratio" type="number" step="0.1" />
              <small class="hint">要求当日成交量 / 均量不低于该值。示例：1.2 表示放量 20%。</small>
            </label>
          </div>
        </template>

        <template v-if="config.active_strategy === 'volume_new_high'">
          <div class="section-title">缩量新高 / 波动率过滤</div>
          <div class="strategy-note">
            这个策略寻找“价格创阶段新高但成交量没有同步放大”的股票，并用
            -corr(最高价, 成交量) × 波动率截面排名做评分。更适合观察缩量突破或控盘迹象。
          </div>
          <div class="grid-2">
            <label class="field">
              <span>相关系数窗口</span>
              <input v-model.number="config.strategies.volume_new_high.corr_window" type="number" min="2" />
              <small class="hint">计算最高价和成交量相关性的天数。默认 10；越大越稳定。</small>
            </label>
            <label class="field">
              <span>波动率窗口</span>
              <input v-model.number="config.strategies.volume_new_high.stddev_window" type="number" min="2" />
              <small class="hint">计算最高价标准差的窗口，并做全市场排名。默认 10。</small>
            </label>
            <label class="field">
              <span>新高窗口</span>
              <input v-model.number="config.strategies.volume_new_high.new_high_window" type="number" min="5" />
              <small class="hint">要求最高价创近 N 日新高。示例：60 表示约 3 个月。</small>
            </label>
            <label class="field">
              <span>均量窗口</span>
              <input v-model.number="config.strategies.volume_new_high.volume_ma_window" type="number" min="1" />
              <small class="hint">计算缩量比例的均量窗口。默认 20。</small>
            </label>
            <label class="field">
              <span>最大量比</span>
              <input v-model.number="config.strategies.volume_new_high.max_volume_ratio" type="number" step="0.05" />
              <small class="hint">量比小于等于该值才认为缩量。0.85 表示低于均量 15%。</small>
            </label>
            <label class="field">
              <span>最低评分</span>
              <input v-model.number="config.strategies.volume_new_high.min_score" type="number" step="0.01" />
              <small class="hint">过滤评分太低的股票。默认 0；提高后候选更少但更集中。</small>
            </label>
          </div>
        </template>

        <button class="run-button" :disabled="loading || isRunning" @click="startRun">
          {{ isRunning ? "运行中" : "开始运行" }}
        </button>
        <p v-if="message" class="message">{{ message }}</p>
      </aside>

      <section class="main-stack">
        <div class="panel status">
        <div class="panel-title">
          <h2>任务状态</h2>
          <div class="status-actions">
            <span class="stage-badge">{{ runStatus?.stage ?? "空闲" }}</span>
            <button
              v-if="runStatus && isRunning"
              class="danger-button"
              :disabled="loading || isCancelling"
              @click="stopRun"
            >
              {{ isCancelling ? "终止中" : "终止任务" }}
            </button>
          </div>
        </div>
          <div v-if="runStatus" class="status-row">
            <strong>状态：{{ runStatus.status }}</strong>
            <span>任务：{{ runStatus.run_id }}</span>
            <span>开始：{{ runStatus.started_at ?? "-" }}</span>
            <span>结束：{{ runStatus.finished_at ?? "-" }}</span>
          </div>
          <pre v-if="runLogs.length" class="terminal">{{ runLogs.join("\n") }}</pre>
          <p v-if="runStatus?.error" class="error">{{ runStatus.error }}</p>
          <p v-if="!runStatus">尚未启动本轮任务。</p>
        </div>

        <div class="panel">
          <div class="panel-title">
            <h2>候选股票</h2>
            <button :disabled="isRunning" @click="loadLatest">
              {{ isRunning ? "运行中" : "刷新结果" }}
            </button>
          </div>
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>代码</th>
                  <th>名称</th>
                  <th>收盘</th>
                  <th>评分</th>
                  <th>{{ factorLabel() }}</th>
                  <th>滚动成交额(亿元)</th>
                  <th>量比</th>
                  <th>策略</th>
                  <th>板块</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="item in candidates"
                  :key="item.code"
                  :class="{ selected: item.code === selectedCode }"
                  @click="selectedCode = item.code"
                >
                  <td>{{ item.code }}</td>
                  <td>{{ item.name }}</td>
                  <td>{{ item.close?.toFixed(2) }}</td>
                  <td>{{ item.score?.toFixed(4) }}</td>
                  <td>{{ factorValue(item) }}</td>
                  <td>{{ turnoverYi(item.turnover_n) }}</td>
                  <td>{{ Number(item.extra?.volume_ratio ?? 0).toFixed(2) }}</td>
                  <td>{{ strategyName(item.strategy) }}</td>
                  <td>{{ marketLabel(item.extra?.market) }}</td>
                </tr>
                <tr v-if="!candidates.length">
                  <td colspan="9" class="empty-cell">
                    {{ isRunning ? "任务运行中，旧候选已清空，等待新结果..." : "暂无候选结果" }}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <div class="panel ai-panel">
          <div class="panel-title">
            <h2>DeepSeek AI 评分</h2>
            <div class="ai-actions">
              <button :disabled="aiLoading" @click="refreshSectorScores">
                更新赛道景气度
              </button>
              <button :disabled="aiLoading || !candidates.length" @click="scoreCandidatesWithAi">
                评分当前候选
              </button>
            </div>
          </div>
          <p v-if="aiError" class="error">{{ aiError }}</p>

          <div class="ai-grid">
            <section>
              <h3>赛道景气度</h3>
              <p class="hint">更新时间：{{ aiSectorScores?.generated_at ?? "-" }}</p>
              <div class="mini-table">
                <table>
                  <thead>
                    <tr>
                      <th>赛道</th>
                      <th>分数</th>
                      <th>机会类型</th>
                      <th>催化</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="item in sectorScoreRows" :key="item.sector">
                      <td>{{ item.sector }}</td>
                      <td>{{ Number(item.score ?? 0).toFixed(1) }}</td>
                      <td>{{ item.opportunity_type ?? "-" }}</td>
                      <td>{{ aiListText(item.catalysts) }}</td>
                    </tr>
                    <tr v-if="!sectorScoreRows.length">
                      <td colspan="4" class="empty-cell">暂无赛道评分</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </section>

            <section>
              <h3>候选股评分</h3>
              <p class="hint">更新时间：{{ aiCandidateScores?.generated_at ?? "-" }}</p>
              <div class="mini-table">
                <table>
                  <thead>
                    <tr>
                      <th>代码</th>
                      <th>名称</th>
                      <th>行业</th>
                      <th>分数</th>
                      <th>结论</th>
                      <th>理由</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="item in candidateScoreRows" :key="item.code">
                      <td>{{ item.code }}</td>
                      <td>{{ item.name }}</td>
                      <td>{{ item.industry ?? "-" }}</td>
                      <td>{{ Number(item.final_score ?? 0).toFixed(1) }}</td>
                      <td>{{ decisionLabel(item.decision) }}</td>
                      <td>{{ item.rationale ?? aiListText(item.evidence_gaps) }}</td>
                    </tr>
                    <tr v-if="!candidateScoreRows.length">
                      <td colspan="6" class="empty-cell">暂无个股 AI 评分</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </section>
          </div>
        </div>

        <div class="panel chart-panel">
          <h2>{{ selectedCandidate?.name ?? "单股图表" }} {{ selectedCode }}</h2>
          <div ref="chartEl" class="chart"></div>
        </div>

        <div class="panel failures">
          <h2>失败报告</h2>
          <p>
            失败 {{ failures?.failed_count ?? 0 }} 只，空数据 {{ failures?.empty_count ?? 0 }} 只。
          </p>
          <pre>{{ JSON.stringify(failures, null, 2) }}</pre>
        </div>
      </section>
    </section>
  </main>
</template>
