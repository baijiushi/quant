<script setup lang="ts">
import { Button as TButton, DatePicker as TDatePicker, Drawer as TDrawer } from "tdesign-vue-next";
import { ChartIcon, CloseIcon, FileIcon, PlayIcon, RobotIcon, SettingIcon, TaskIcon } from "tdesign-icons-vue-next";
import { computed, defineAsyncComponent, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import CandidateTable from "./components/CandidateTable.vue";
import MarketBreadthPanel from "./components/MarketBreadthPanel.vue";
import RunStatusPanel from "./components/RunStatusPanel.vue";

const KlineChart = defineAsyncComponent(() => import("./components/KlineChart.vue"));

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

interface MarketBreadthComponent {
  id: string;
  name: string;
  value_pct: number;
  signal: string;
  description: string;
}

interface MarketBreadth {
  available: boolean;
  trade_date?: string | null;
  status: string;
  risk_level: string;
  score?: number | null;
  position_guidance: string;
  summary: string;
  stock_count?: number;
  components: MarketBreadthComponent[];
  methodology?: string;
  disclaimer?: string;
}

interface EntryPlan {
  code: string;
  name: string;
  as_of: string;
  mode: "a_share_daily_proxy";
  framework: string;
  trend: {
    direction: "bullish" | "bearish" | "neutral";
    status: string;
    basis?: string;
    bos_date?: string;
    structure_price?: number | null;
    close?: number | null;
    atr?: number | null;
  };
  interception?: {
    source: string;
    status: string;
    zone_low: number;
    zone_high: number;
    touched_recently: boolean;
    reclaimed: boolean;
  } | null;
  entry: {
    action: "ready" | "wait_confirmation" | "wait_interception" | "avoid_or_reduce" | "skip";
    status: string;
    confirmation?: string;
    trigger_price?: number | null;
    stop_price?: number | null;
    target_1r?: number | null;
    target_2r?: number | null;
    selected_reward_risk?: number;
    selected_target?: number | null;
  };
  position?: {
    account_value: number;
    risk_pct: number;
    risk_budget: number;
    risk_per_share: number;
    suggested_shares: number;
    position_value: number;
    planned_loss: number;
  } | null;
  historical_review: {
    window_bars: number;
    requested_window_bars: number;
    start_date: string;
    end_date: string;
    signal_count: number;
    completed_count: number;
    win_count: number;
    loss_count: number;
    win_rate: number | null;
    completed_profit_loss_ratio: number | null;
    methodology: string;
    signals: Array<{
      signal_date: string;
      entry_price: number;
      stop_price: number;
      target_price: number;
      planned_reward_risk: number;
      risk_pct_of_entry: number;
      target_return_pct: number;
      zone_low?: number | null;
      zone_high?: number | null;
      bos_date?: string | null;
      outcome: "target" | "stopped" | "ambiguous" | "open" | "invalid";
      outcome_label: string;
      exit_date?: string | null;
      realized_r?: number | null;
    }>;
  };
  smt: {
    status: string;
    available: boolean;
    reason: string;
    required_data: string[];
  };
  warnings: string[];
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
  source_refs?: string[] | string;
  risk_deduction?: number;
  liquidity_coefficient?: number;
  confidence?: number;
  dimension_reviews?: Record<string, { comment?: string; source_refs?: string[]; length?: number }>;
  thesis_transmission?: string;
  invalidation_triggers?: string[] | string;
  data_needed?: string[] | string;
  decision_note?: string;
}

interface AIModelInfo {
  model: string;
  thinking_mode: boolean;
  reasoning_effort: string;
  web_search_default: boolean;
  max_search_candidates: number;
}

interface AIScoreJobStatus {
  job_id: string;
  status: "queued" | "running" | "success" | "failed";
  stage: string;
  model: string;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  reasoning: string;
  content_preview: string;
  logs: string[];
  error?: string | null;
  result?: Record<string, unknown> | null;
}

interface CandidateWebResearch {
  enabled?: boolean;
  searched_codes?: string[];
  sources?: Array<{ title: string; url: string }>;
  error?: string;
}

interface ResearchDocument {
  id: number;
  title: string;
  content: string;
  source_url?: string | null;
  source_type: string;
  captured_at: string;
  created_at: string;
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
const marketBreadth = ref<MarketBreadth | null>(null);
const marketBreadthLoading = ref(false);
const marketBreadthError = ref("");
const selectedCode = ref("");
const klineRows = ref<KlineRow[]>([]);
const entryPlan = ref<EntryPlan | null>(null);
const entryPlanLoading = ref(false);
const entryPlanError = ref("");
const entryAccountValue = ref(100000);
const entryRiskPct = ref(0.5);
const entryRewardRisk = ref(1);
const loading = ref(false);
const message = ref("");
const bootLoading = ref(true);
const bootError = ref("");
const aiSectorScores = ref<Record<string, unknown> | null>(null);
const aiCandidateScores = ref<Record<string, unknown> | null>(null);
const aiLoading = ref(false);
const aiError = ref("");
const aiModel = ref<AIModelInfo | null>(null);
const aiScoreJob = ref<AIScoreJobStatus | null>(null);
const aiWebResearch = ref(true);
const aiReasoningEl = ref<HTMLPreElement | null>(null);
const researchDocuments = ref<ResearchDocument[]>([]);
const researchTitle = ref("");
const researchContent = ref("");
const researchUrl = ref("");
const researchSaving = ref(false);
const parameterTab = ref<"basic" | "strategy" | "universe">("basic");
const workspaceTab = ref<"run" | "ai" | "research" | "entry">("run");
const configDrawerOpen = ref(false);
const chartPanelEl = ref<HTMLElement | null>(null);
let pollTimer: number | null = null;
let aiEventSource: EventSource | null = null;
let entryPlanRequest = 0;

const dimensionNames = ["行业景气度", "业务纯度", "估值水位", "细分行业龙头", "市场辨识度"];

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
  },
  {
    id: "high_52w_momentum",
    name: "52周新高动量",
    description: "寻找接近过去 52 周高点且中期动量为正的股票，并使用截面排名评分。",
    default_config: {
      high_lookback_days: 252,
      momentum_lookback_days: 126,
      momentum_skip_days: 20,
      trend_ma_days: 60,
      min_high_proximity: 0.9,
      min_momentum_return: 0,
      require_above_trend_ma: true,
      high_proximity_weight: 0.6,
      momentum_weight: 0.4,
      max_candidates: 30
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
const candidateScoreRows = computed(() => {
  const currentCodes = new Set(candidates.value.map((item) => item.code));
  return ((aiCandidateScores.value?.scores ?? []) as CandidateAIScore[]).filter((item) =>
    currentCodes.has(String(item.code).padStart(6, "0"))
  );
});
const aiJobRunning = computed(() => ["queued", "running"].includes(aiScoreJob.value?.status ?? ""));
const candidateScoreEmptyMessage = computed(() => {
  if (aiJobRunning.value) return "正在生成当前候选的详细评分...";
  if (!candidates.value.length) return "当前没有候选股票，请先运行选股策略。";
  if (aiCandidateScores.value?.status === "stale") {
    return "候选批次已经变化，上一批历史评分已隐藏；请点击“评分当前候选”。";
  }
  return "当前候选尚未进行 AI 评分。";
});
const displayedReasoning = computed(() => {
  const live = aiScoreJob.value?.reasoning;
  return live || String(aiCandidateScores.value?.reasoning_content ?? "");
});
const aiOutputChars = computed(
  () => aiScoreJob.value?.content_preview?.length || displayedReasoning.value.length || 0
);
const candidateWebResearch = computed(
  () => (aiCandidateScores.value?.web_research ?? {}) as CandidateWebResearch
);
const runProgress = computed(() => {
  const stage = runStatus.value?.stage ?? "";
  if (["运行完成", "已终止", "运行失败", "服务重启中断"].includes(stage)) return 100;
  if (stage.includes("保存")) return 92;
  if (stage.includes("策略")) return 75;
  if (stage.includes("指标")) return 68;
  if (stage.includes("流动性")) return 58;
  if (stage.includes("数据库")) return 42;
  if (stage.includes("加载")) return 38;
  if (stage.includes("更新") || stage.includes("数据")) return 20;
  return isRunning.value ? 8 : 0;
});
const dataModeLabel = computed(() => ({
  existing: "本地数据",
  incremental: "增量更新",
  refresh: "重新拉取",
  "cache-only": "仅缓存"
} as Record<DataMode, string>)[config.value?.data_mode ?? "existing"]);

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
    const availableCodes = new Set(candidates.value.map((item) => item.code));
    if (!availableCodes.has(selectedCode.value)) selectedCode.value = candidates.value[0]?.code ?? "";
  } catch {
    latest.value = null;
  }
}

async function refreshLatest() {
  await loadLatest();
  await loadAiScores();
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

async function loadMarketBreadth() {
  if (!config.value) return;
  marketBreadthLoading.value = true;
  marketBreadthError.value = "";
  try {
    const params = new URLSearchParams({
      adjust: String(config.value.global.adjust ?? "qfq"),
      markets: (config.value.global.markets ?? ["main", "gem", "star", "bse"]).join(",")
    });
    if (pickDate.value) params.set("as_of", pickDate.value);
    marketBreadth.value = await api<MarketBreadth>(`/api/market/breadth?${params.toString()}`);
  } catch (error) {
    marketBreadth.value = null;
    marketBreadthError.value = error instanceof Error ? error.message : String(error);
  } finally {
    marketBreadthLoading.value = false;
  }
}

async function openCandidateChart(item: Candidate) {
  selectedCode.value = item.code;
  workspaceTab.value = "entry";
  await nextTick();
  chartPanelEl.value?.scrollIntoView({ behavior: "smooth", block: "start" });
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

async function handleStrategyChange() {
  selectedCode.value = "";
  aiScoreJob.value = null;
  aiCandidateScores.value = { generated_at: null, status: "not_scored", scores: [] };
  closeAiEvents();
  await Promise.all([loadLatest(), loadAiScores()]);
}

async function loadAiModel() {
  try {
    aiModel.value = await api<AIModelInfo>("/api/ai/model");
    aiWebResearch.value = aiModel.value.web_search_default;
  } catch {
    aiModel.value = {
      model: "deepseek-v4-flash",
      thinking_mode: true,
      reasoning_effort: "high",
      web_search_default: true,
      max_search_candidates: 12
    };
  }
}

function closeAiEvents() {
  aiEventSource?.close();
  aiEventSource = null;
}

function applyAiJob(job: AIScoreJobStatus) {
  aiScoreJob.value = job;
  aiLoading.value = ["queued", "running"].includes(job.status);
  if (job.status === "success" && job.result) {
    aiCandidateScores.value = job.result;
    message.value = "候选股 AI 评分已完成";
    closeAiEvents();
  } else if (job.status === "failed") {
    aiError.value = job.error || "AI 评分失败";
    closeAiEvents();
  }
}

function connectAiEvents(jobId: string) {
  closeAiEvents();
  aiEventSource = new EventSource(`/api/ai/candidate-scores/jobs/${encodeURIComponent(jobId)}/events`);
  aiEventSource.onmessage = (event) => {
    applyAiJob(JSON.parse(event.data) as AIScoreJobStatus);
  };
  aiEventSource.onerror = async () => {
    try {
      const job = await api<AIScoreJobStatus>(`/api/ai/candidate-scores/jobs/${encodeURIComponent(jobId)}`);
      applyAiJob(job);
    } catch (error) {
      aiError.value = error instanceof Error ? error.message : String(error);
      aiLoading.value = false;
      closeAiEvents();
    }
  };
}

async function syncCurrentAiJob() {
  try {
    const payload = await api<{ job: AIScoreJobStatus | null }>("/api/ai/candidate-scores/jobs/current");
    if (!payload.job) return;
    applyAiJob(payload.job);
    if (["queued", "running"].includes(payload.job.status)) connectAiEvents(payload.job.job_id);
  } catch {
    // Older backends do not expose AI jobs; latest persisted scores still load normally.
  }
}

async function loadResearchDocuments() {
  try {
    const payload = await api<{ documents: ResearchDocument[] }>("/api/research/documents?limit=20");
    researchDocuments.value = payload.documents;
  } catch {
    researchDocuments.value = [];
  }
}

async function saveResearchDocument() {
  if (!researchTitle.value.trim() || !researchContent.value.trim()) {
    aiError.value = "请填写研究素材标题和摘要内容。";
    return;
  }
  researchSaving.value = true;
  aiError.value = "";
  try {
    await api<ResearchDocument>("/api/research/documents", {
      method: "POST",
      body: JSON.stringify({
        title: researchTitle.value.trim(),
        content: researchContent.value.trim(),
        source_url: researchUrl.value.trim() || null,
        source_type: "manual_summary"
      })
    });
    researchTitle.value = "";
    researchContent.value = "";
    researchUrl.value = "";
    await loadResearchDocuments();
    message.value = "研究素材已保存，下次更新赛道评分会自动纳入。";
  } catch (error) {
    aiError.value = error instanceof Error ? error.message : String(error);
  } finally {
    researchSaving.value = false;
  }
}

async function deleteResearchDocument(documentId: number) {
  researchSaving.value = true;
  try {
    await api<{ deleted: boolean }>(`/api/research/documents/${documentId}`, { method: "DELETE" });
    await loadResearchDocuments();
  } catch (error) {
    aiError.value = error instanceof Error ? error.message : String(error);
  } finally {
    researchSaving.value = false;
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
  aiCandidateScores.value = { generated_at: null, scores: [] };
  try {
    const job = await api<AIScoreJobStatus>("/api/ai/candidate-scores/jobs", {
      method: "POST",
      body: JSON.stringify({
        strategy_id: config.value?.active_strategy,
        max_candidates: 20,
        web_research: aiWebResearch.value
      })
    });
    aiScoreJob.value = job;
    message.value = `AI 评分任务 ${job.job_id} 已启动`;
    connectAiEvents(job.job_id);
  } catch (error) {
    aiError.value = error instanceof Error ? error.message : String(error);
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
  entryPlan.value = null;
  entryPlanError.value = "";
  aiCandidateScores.value = { generated_at: null, status: "not_scored", scores: [] };
  aiScoreJob.value = null;
  closeAiEvents();
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
  configDrawerOpen.value = false;
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
        await Promise.all([loadLatest(), loadMarketBreadth()]);
        await loadAiScores();
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

async function loadEntryPlan(code: string) {
  const requestId = ++entryPlanRequest;
  if (!code) {
    entryPlan.value = null;
    entryPlanLoading.value = false;
    entryPlanError.value = "";
    return;
  }
  entryPlanLoading.value = true;
  entryPlanError.value = "";
  try {
    const params = new URLSearchParams({
      adjust: config.value?.global.adjust ?? "qfq",
      account_value: String(entryAccountValue.value),
      risk_pct: String(entryRiskPct.value),
      reward_risk: String(entryRewardRisk.value),
      review_bars: "60"
    });
    const analysisDate = selectedCandidate.value?.date || latest.value?.pick_date || pickDate.value;
    if (analysisDate) params.set("as_of", analysisDate);
    const payload = await api<EntryPlan>(`/api/stocks/${code}/entry-plan?${params.toString()}`);
    if (requestId === entryPlanRequest) entryPlan.value = payload;
  } catch (error) {
    if (requestId === entryPlanRequest) {
      entryPlan.value = null;
      entryPlanError.value = error instanceof Error ? error.message : String(error);
    }
  } finally {
    if (requestId === entryPlanRequest) entryPlanLoading.value = false;
  }
}

function planPrice(value: number | null | undefined): string {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric.toFixed(2) : "-";
}

function planMoney(value: number | null | undefined): string {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric.toLocaleString("zh-CN", { maximumFractionDigits: 2 }) : "-";
}

function trendLabel(value: EntryPlan["trend"]["direction"] | undefined): string {
  if (value === "bullish") return "多头结构";
  if (value === "bearish") return "空头结构";
  return "结构不清晰";
}

function entryActionLabel(value: EntryPlan["entry"]["action"] | undefined): string {
  const labels: Record<string, string> = {
    ready: "出现日线确认",
    wait_confirmation: "等待低周期确认",
    wait_interception: "等待回到截取区",
    avoid_or_reduce: "回避或降低仓位",
    skip: "跳过"
  };
  return labels[value ?? ""] ?? "未计算";
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

function aiListText(value: unknown): string {
  if (Array.isArray(value)) return value.join("；");
  return String(value ?? "-");
}

function aiRefs(value: unknown): string[] {
  if (Array.isArray(value)) return value.map(String).filter(Boolean);
  return value ? [String(value)] : [];
}

function dimensionReview(item: CandidateAIScore, name: string) {
  return item.dimension_reviews?.[name] ?? { comment: "暂无详细评价", source_refs: [], length: 0 };
}

function isHttpUrl(value: unknown): boolean {
  return /^https?:\/\//i.test(String(value ?? ""));
}

function confidenceLabel(value: unknown): string {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? `${Math.round(numeric * 100)}%` : "待核验";
}

function decisionLabel(value: unknown): string {
  if (value === "buy") return "重点研究";
  if (value === "watch") return "观察";
  if (value === "avoid") return "回避";
  return String(value ?? "-");
}

function sourceCountLabel(item: CandidateAIScore): string {
  const refs = item.source_refs;
  return Array.isArray(refs) ? `${refs.length} 条证据` : refs ? "有证据" : "待补充";
}

watch(selectedCode, async (code) => {
  if (code) {
    await Promise.all([loadKline(code), loadEntryPlan(code)]);
  } else {
    entryPlan.value = null;
  }
});

watch(displayedReasoning, async () => {
  await nextTick();
  if (aiReasoningEl.value) aiReasoningEl.value.scrollTop = aiReasoningEl.value.scrollHeight;
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
    await loadMarketBreadth();
    if (selectedCode.value) {
      await Promise.all([loadKline(selectedCode.value), loadEntryPlan(selectedCode.value)]);
    }
    await loadAiModel();
    await loadAiScores();
    await syncCurrentAiJob();
    await loadResearchDocuments();
  } catch (error) {
    bootError.value = error instanceof Error ? error.message : String(error);
  } finally {
    bootLoading.value = false;
  }
});

onUnmounted(() => {
  clearPollTimer();
  closeAiEvents();
});
</script>

<template>
  <main class="shell">
    <section class="command-header">
      <div class="command-copy">
        <h1>多策略量化选股控制台</h1>
        <p>配置策略、监控数据任务，并从候选直接进入图表研究。</p>
      </div>
      <div class="command-facts" aria-label="当前运行摘要">
        <div><span>策略</span><strong>{{ activeStrategyInfo?.name ?? activeStrategy }}</strong></div>
        <div><span>数据</span><strong>{{ dataModeLabel }}</strong></div>
        <div><span>交易日</span><strong>{{ latest?.pick_date ?? "最新" }}</strong></div>
        <div><span>候选</span><strong>{{ candidates.length }} 只</strong></div>
      </div>
      <div class="command-actions">
        <TButton variant="outline" theme="default" @click="configDrawerOpen = true">
          <template #icon><SettingIcon /></template>
          运行参数
        </TButton>
        <TButton theme="primary" :loading="loading" :disabled="loading || isRunning || !config" @click="startRun">
          <template #icon><PlayIcon /></template>
          {{ isRunning ? "运行中" : "开始运行" }}
        </TButton>
      </div>
    </section>

    <p v-if="message" class="system-notice" role="status">{{ message }}</p>

    <section v-if="bootLoading && !config" class="panel boot-panel">
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
      <TDrawer
        v-model:visible="configDrawerOpen"
        class="config-drawer"
        header="运行参数"
        placement="right"
        size="min(580px, 100vw)"
        :footer="false"
      >
      <aside class="controls drawer-controls">
        <div class="panel-title">
          <div>
            <h2>策略与数据设置</h2>
            <p class="hint">修改后先保存；开始任务时会再次把当前配置交给后端。</p>
          </div>
          <div class="drawer-title-actions">
            <button class="drawer-close-button" type="button" @click="configDrawerOpen = false">
              <CloseIcon size="16px" />关闭
            </button>
            <button :disabled="loading" @click="saveConfig">保存配置</button>
          </div>
        </div>

        <nav class="parameter-tabs" aria-label="运行参数分类">
          <button
            v-for="tab in [
              { id: 'basic', label: '基础' },
              { id: 'strategy', label: '策略' },
              { id: 'universe', label: '高级' }
            ]"
            :key="tab.id"
            :class="{ active: parameterTab === tab.id }"
            type="button"
            :aria-selected="parameterTab === tab.id"
            @click="parameterTab = tab.id as typeof parameterTab"
          >
            {{ tab.label }}
          </button>
        </nav>

        <div v-show="parameterTab === 'basic'" class="parameter-tab-panel">
        <label class="field">
          <span>选股策略</span>
          <select v-model="config.active_strategy" @change="handleStrategyChange">
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
        <div v-if="config.data_mode === 'incremental' || config.data_mode === 'refresh'" class="data-mode-warning">
          <strong>{{ config.data_mode === "refresh" ? "完整重拉会持续较长时间" : "增量更新需要先访问 TUShare" }}</strong>
          <span>如果这次只想调整参数并重新筛选，请改用“直接使用本地数据”。任务启动后也可以在监控栏安全终止。</span>
        </div>

        <label class="field">
          <span>选股日期</span>
          <TDatePicker
            v-model="pickDate"
            clearable
            format="YYYY-MM-DD"
            value-type="YYYY-MM-DD"
            placeholder="选择交易日（默认最新）"
          />
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
        </div>

        <div v-show="parameterTab === 'universe'" class="parameter-tab-panel">
          <div class="strategy-note">
            这里控制选股范围和流动性预筛。首次调试建议保留默认值；Top M 越小，运行越快，但可能漏掉成交较弱的股票。
          </div>
        <div class="grid-2">
          <label class="field">
            <span>流动性 Top M（按成交额）</span>
            <input v-model.number="config.global.top_m" type="number" min="0" />
            <small class="hint">先按滚动成交额选前 M 只再跑策略。0 表示不过滤；常用 1000-3000。</small>
          </label>
          <label class="field">
            <span>成交额窗口（日）</span>
              <input v-model.number="config.global.n_turnover_days" type="number" min="1" max="250" />
            <small class="hint">用于计算滚动成交额，越大越偏长期流动性。示例：20、43、60。</small>
          </label>
        </div>
        </div>

        <div v-show="parameterTab === 'strategy'" class="parameter-tab-panel">
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

        <template v-if="config.active_strategy === 'high_52w_momentum'">
          <div class="section-title">52周新高动量</div>
          <div class="strategy-note">
            寻找仍靠近一年高点、且过去约半年保持正动量的股票。评分由“接近高点排名”和
            “中期动量排名”合成，建议先保留趋势过滤并通过回测检查参数稳定性。
          </div>
          <div class="grid-2">
            <label class="field">
              <span>新高回看天数</span>
              <input v-model.number="config.strategies.high_52w_momentum.high_lookback_days" type="number" min="120" max="400" />
              <small class="hint">默认 252 个交易日，约等于 52 周。建议范围 200–300。</small>
            </label>
            <label class="field">
              <span>动量回看天数</span>
              <input v-model.number="config.strategies.high_52w_momentum.momentum_lookback_days" type="number" min="20" max="252" />
              <small class="hint">默认 126 日，约半年；越长越偏中期趋势。</small>
            </label>
            <label class="field">
              <span>跳过最近天数</span>
              <input v-model.number="config.strategies.high_52w_momentum.momentum_skip_days" type="number" min="0" max="60" />
              <small class="hint">默认跳过 20 日，减少短期反转影响；0 表示不跳过。</small>
            </label>
            <label class="field">
              <span>趋势均线</span>
              <input v-model.number="config.strategies.high_52w_momentum.trend_ma_days" type="number" min="10" max="250" />
              <small class="hint">默认 MA60；趋势过滤启用时要求收盘价站在均线上方。</small>
            </label>
            <label class="field">
              <span>最低高点接近度</span>
              <input v-model.number="config.strategies.high_52w_momentum.min_high_proximity" type="number" min="0.5" max="1" step="0.01" />
              <small class="hint">0.90 表示收盘价距离 52 周最高价不超过约 10%。</small>
            </label>
            <label class="field">
              <span>最低动量收益</span>
              <input v-model.number="config.strategies.high_52w_momentum.min_momentum_return" type="number" min="-1" max="5" step="0.05" />
              <small class="hint">小数形式；0 表示中期收益为正，0.10 表示至少上涨 10%。</small>
            </label>
            <label class="field">
              <span>高点权重</span>
              <input v-model.number="config.strategies.high_52w_momentum.high_proximity_weight" type="number" min="0" max="1" step="0.1" />
              <small class="hint">默认 0.6，系统会和动量权重自动归一化。</small>
            </label>
            <label class="field">
              <span>动量权重</span>
              <input v-model.number="config.strategies.high_52w_momentum.momentum_weight" type="number" min="0" max="1" step="0.1" />
              <small class="hint">默认 0.4；提高后更偏向过去涨幅领先的股票。</small>
            </label>
            <label class="field">
              <span>最多候选数</span>
              <input v-model.number="config.strategies.high_52w_momentum.max_candidates" type="number" min="0" max="500" />
              <small class="hint">默认 30；设为 0 表示返回全部符合条件的股票。</small>
            </label>
          </div>
          <label class="switch">
            <input v-model="config.strategies.high_52w_momentum.require_above_trend_ma" type="checkbox" />
            <span>要求站上趋势均线</span>
          </label>
        </template>
        </div>

        <button class="run-button" :disabled="loading || isRunning" @click="startRun">
          {{ isRunning ? "任务正在运行" : "保存当前参数并运行" }}
        </button>
      </aside>
      </TDrawer>

      <section class="main-stack">
        <nav class="workspace-tabs" aria-label="控制台工作区">
          <button type="button" :class="{ active: workspaceTab === 'run' }" @click="workspaceTab = 'run'">
            <TaskIcon size="18px" /><span>运行与候选</span><small>任务、结果和失败报告</small>
          </button>
          <button type="button" :class="{ active: workspaceTab === 'ai' }" @click="workspaceTab = 'ai'">
            <RobotIcon size="18px" /><span>AI 评分</span><small>赛道和当前候选评分</small>
          </button>
          <button type="button" :class="{ active: workspaceTab === 'research' }" @click="workspaceTab = 'research'">
            <FileIcon size="18px" /><span>研究素材</span><small>视频、动态和研报摘要</small>
          </button>
          <button type="button" :class="{ active: workspaceTab === 'entry' }" @click="workspaceTab = 'entry'">
            <ChartIcon size="18px" /><span>进场与图表</span><small>趋势截取和 K 线计划</small>
          </button>
        </nav>

        <section v-show="workspaceTab === 'run'" class="run-workspace">
          <CandidateTable
            :candidates="candidates"
            :selected-code="selectedCode"
            :running="isRunning"
            :active-strategy="activeStrategy"
            :strategies="strategies"
            @refresh="refreshLatest"
            @select="openCandidateChart"
          />
          <aside class="monitor-rail">
            <RunStatusPanel
              :status="runStatus"
              :progress="runProgress"
              :logs="runLogs"
              :running="isRunning"
              :cancelling="isCancelling"
              :loading="loading"
              @stop="stopRun"
            />
            <MarketBreadthPanel
              :breadth="marketBreadth"
              :loading="marketBreadthLoading"
              :error="marketBreadthError"
              @refresh="loadMarketBreadth"
            />
            <details class="failure-summary">
              <summary>
                <span>数据失败报告</span>
                <strong>{{ Number(failures?.failed_count ?? 0) + Number(failures?.empty_count ?? 0) }} 项</strong>
              </summary>
              <p>失败 {{ failures?.failed_count ?? 0 }} 只，空数据 {{ failures?.empty_count ?? 0 }} 只。</p>
              <pre>{{ JSON.stringify(failures, null, 2) }}</pre>
            </details>
          </aside>
        </section>

        <div v-show="workspaceTab === 'ai'" class="panel ai-panel">
          <div class="panel-title">
            <div>
              <div class="ai-heading">
                <h2>DeepSeek AI 评分</h2>
                <span class="model-badge">{{ aiModel?.model ?? "deepseek-v4-flash" }}</span>
              </div>
              <p class="hint">Flash 思考模式 · Python 复算总分 · 联网资料附来源</p>
            </div>
            <div class="ai-actions">
              <label class="ai-search-toggle">
                <input v-model="aiWebResearch" type="checkbox" :disabled="aiLoading" />
                联网检索个股
              </label>
              <button :disabled="aiLoading" @click="refreshSectorScores">
                更新赛道景气度
              </button>
              <button :disabled="aiLoading || !candidates.length" @click="scoreCandidatesWithAi">
                {{ aiJobRunning ? "评分进行中" : "评分当前候选" }}
              </button>
            </div>
          </div>
          <p v-if="aiError" class="error">{{ aiError }}</p>

          <section v-if="aiScoreJob || displayedReasoning" class="ai-stream-panel">
            <div class="ai-stream-head">
              <div>
                <strong>{{ aiScoreJob?.stage ?? "上次评分思考记录" }}</strong>
                <span v-if="aiScoreJob">任务 {{ aiScoreJob.job_id }}</span>
              </div>
              <div class="stream-stats">
                <span>{{ aiScoreJob?.model ?? aiCandidateScores?.model ?? aiModel?.model }}</span>
                <span>已生成 {{ aiOutputChars }} 字符</span>
              </div>
            </div>
            <div v-if="aiJobRunning" class="thinking-line"><i></i></div>
            <p class="stream-note">
              以下内容来自 DeepSeek API 的 <code>reasoning_content</code>，会随模型输出实时更新。原始思考可能包含临时判断，
              估值水位、流动性系数和最终分以 Python 复算后的评分卡为准。
            </p>
            <pre ref="aiReasoningEl" class="ai-reasoning">{{ displayedReasoning || "正在准备资料，思考内容即将开始输出..." }}</pre>
            <div v-if="aiScoreJob?.logs?.length" class="ai-job-logs">
              <span v-for="line in aiScoreJob.logs.slice(-5)" :key="line">{{ line }}</span>
            </div>
          </section>

          <div class="ai-grid">
            <section class="ai-score-section sector-score-section">
              <h3>赛道景气度</h3>
              <p class="hint">更新时间：{{ aiSectorScores?.generated_at ?? "-" }}</p>
              <div class="mini-table sector-score-table">
                <table>
                  <colgroup>
                    <col class="sector-name-column" />
                    <col class="sector-score-column" />
                    <col class="sector-type-column" />
                    <col class="sector-catalyst-column" />
                  </colgroup>
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

            <section class="ai-score-section candidate-score-section">
              <h3>候选股评分</h3>
              <p class="hint">更新时间：{{ aiCandidateScores?.generated_at ?? "-" }}</p>
              <div class="mini-table candidate-score-table">
                <table>
                  <thead>
                    <tr>
                      <th>代码</th>
                      <th>名称</th>
                      <th>行业</th>
                      <th>分数</th>
                      <th>结论</th>
                      <th>置信度</th>
                      <th>证据</th>
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
                      <td>{{ confidenceLabel(item.confidence) }}</td>
                      <td>{{ sourceCountLabel(item) }}</td>
                      <td class="rationale-cell">{{ item.rationale ?? aiListText(item.evidence_gaps) }}</td>
                    </tr>
                    <tr v-if="!candidateScoreRows.length">
                      <td colspan="8" class="empty-cell">{{ candidateScoreEmptyMessage }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </section>
          </div>
          <div v-if="candidateWebResearch.enabled" class="web-evidence-summary">
            <strong>联网资料</strong>
            <span>已检索 {{ candidateWebResearch.searched_codes?.length ?? 0 }} 只股票，获得 {{ candidateWebResearch.sources?.length ?? 0 }} 个来源</span>
            <span v-if="candidateWebResearch.error" class="error-inline">{{ candidateWebResearch.error }}</span>
            <div class="source-links">
              <a
                v-for="source in candidateWebResearch.sources?.slice(0, 20) ?? []"
                :key="source.url"
                :href="source.url"
                target="_blank"
                rel="noreferrer"
              >{{ source.title }}</a>
            </div>
          </div>
          <div v-if="candidateScoreRows.length" class="ai-detail-list">
            <details v-for="item in candidateScoreRows" :key="`${item.code}-detail`">
              <summary>
                <span>{{ item.code }} {{ item.name }} 的评分明细</span>
                <b>{{ Number(item.final_score ?? 0).toFixed(1) }} 分 · {{ confidenceLabel(item.confidence) }}</b>
              </summary>
              <p class="candidate-rationale">{{ item.rationale ?? "暂无综合评价" }}</p>
              <div class="dimension-review-grid">
                <article v-for="name in dimensionNames" :key="`${item.code}-${name}`">
                  <div>
                    <strong>{{ name }}</strong>
                    <span>{{ Number(item.dimension_scores?.[name] ?? 0).toFixed(0) }} 分</span>
                  </div>
                  <p>{{ dimensionReview(item, name).comment }}</p>
                  <div v-if="dimensionReview(item, name).source_refs?.length" class="dimension-sources">
                    <template v-for="source in aiRefs(dimensionReview(item, name).source_refs)" :key="source">
                      <a v-if="isHttpUrl(source)" :href="source" target="_blank" rel="noreferrer">查看来源</a>
                      <span v-else>{{ source }}</span>
                    </template>
                  </div>
                </article>
              </div>
              <div class="score-meta">
                <span><strong>风险扣分</strong>{{ item.risk_deduction ?? "-" }}</span>
                <span><strong>流动性系数</strong>{{ item.liquidity_coefficient ?? "-" }}</span>
                <span><strong>结论</strong>{{ decisionLabel(item.decision) }}</span>
              </div>
              <p><strong>逻辑传导：</strong>{{ item.thesis_transmission ?? "待补充" }}</p>
              <p><strong>失效条件：</strong>{{ aiListText(item.invalidation_triggers) }}</p>
              <p><strong>风险事件：</strong>{{ aiListText(item.risk_events) }}</p>
              <p><strong>待补充：</strong>{{ aiListText(item.evidence_gaps ?? item.data_needed) }}</p>
              <div class="source-links detail-sources">
                <template v-for="source in aiRefs(item.source_refs)" :key="source">
                  <a v-if="isHttpUrl(source)" :href="source" target="_blank" rel="noreferrer">{{ source }}</a>
                  <span v-else>{{ source }}</span>
                </template>
              </div>
            </details>
          </div>
        </div>

        <div v-show="workspaceTab === 'research'" class="panel research-panel">
          <div class="panel-title">
            <div>
              <h2>赛道研究素材库</h2>
              <p class="hint">录入你对视频、动态、公告或研报的摘要。AI 仅把这些文字作为证据，不会假装已读取付费内容。</p>
            </div>
            <span class="stage-badge">已存 {{ researchDocuments.length }} 条</span>
          </div>
          <div class="research-grid">
            <label class="field">
              <span>素材标题</span>
              <input v-model="researchTitle" placeholder="例：笨笨的韭菜 7 月市场记录摘要" />
            </label>
            <label class="field">
              <span>来源链接（可选）</span>
              <input v-model="researchUrl" type="url" placeholder="https://www.bilibili.com/..." />
            </label>
          </div>
          <label class="field">
            <span>研究摘要与原始要点</span>
            <textarea v-model="researchContent" rows="6" placeholder="写明日期、赛道、政策/订单/技术催化、对应公司、反证和风险。保存后点击“更新赛道景气度”即可纳入评分。"></textarea>
          </label>
          <button :disabled="researchSaving" @click="saveResearchDocument">{{ researchSaving ? "保存中" : "保存研究素材" }}</button>
          <div class="research-list">
            <article v-for="item in researchDocuments" :key="item.id">
              <div><strong>{{ item.title }}</strong><span>{{ item.captured_at }}</span></div>
              <p>{{ item.content }}</p>
              <div class="research-actions">
                <a v-if="item.source_url" :href="item.source_url" target="_blank" rel="noreferrer">查看来源</a>
                <button class="text-button" :disabled="researchSaving" @click="deleteResearchDocument(item.id)">删除</button>
              </div>
            </article>
            <p v-if="!researchDocuments.length" class="empty-cell">暂无研究素材。先保存一条经过你核对的摘要，再运行赛道评分。</p>
          </div>
        </div>

        <div v-show="workspaceTab === 'entry'" class="panel entry-plan-panel">
          <div class="panel-title">
            <div>
              <h2>趋势截取入场点检查</h2>
              <p class="hint">
                逐日检查最近 60 根 K 线是否出现过入场点，并评价其后续止盈止损；这些历史点不是当前买入推荐。
              </p>
            </div>
            <span class="stage-badge">{{ entryPlan?.historical_review.signal_count ?? 0 }} 个历史入场点</span>
          </div>

          <div class="entry-plan-controls">
            <label class="field">
              <span>复盘目标盈亏比（R）</span>
              <input v-model.number="entryRewardRisk" type="number" min="0.5" max="5" step="0.5" />
            </label>
            <button :disabled="!selectedCode || entryPlanLoading" @click="loadEntryPlan(selectedCode)">
              {{ entryPlanLoading ? "计算中" : "重新计算" }}
            </button>
          </div>

          <p v-if="entryPlanLoading" class="empty-cell">正在逐日扫描最近 60 个交易日...</p>
          <p v-else-if="entryPlanError" class="entry-plan-error">{{ entryPlanError }}</p>
          <p v-else-if="!selectedCode" class="empty-cell">在候选股表中选择一只股票后查看计划。</p>

          <template v-else-if="entryPlan">
            <div class="framework-strip">
              <article class="framework-step">
                <span>观察区间</span>
                <strong>{{ entryPlan.historical_review.window_bars }} 个交易日</strong>
                <p>{{ entryPlan.historical_review.start_date }} 至 {{ entryPlan.historical_review.end_date }}</p>
                <small>每个日期只使用当日及以前的 K 线</small>
              </article>
              <article class="framework-step">
                <span>历史入场点</span>
                <strong>{{ entryPlan.historical_review.signal_count }} 个</strong>
                <p>已结束 {{ entryPlan.historical_review.completed_count }} 个</p>
                <small>相邻连续确认只记录首次出现</small>
              </article>
              <article class="framework-step">
                <span>历史结果</span>
                <strong>{{ entryPlan.historical_review.win_count }} 胜 / {{ entryPlan.historical_review.loss_count }} 负</strong>
                <p>胜率 {{ entryPlan.historical_review.win_rate === null ? "样本不足" : `${(entryPlan.historical_review.win_rate * 100).toFixed(1)}%` }}</p>
                <small>同日触及止盈止损不计胜负</small>
              </article>
            </div>

            <div v-if="entryPlan.historical_review.signals.length" class="research-list">
              <article v-for="signal in entryPlan.historical_review.signals" :key="signal.signal_date">
                <div><strong>{{ signal.signal_date }} 历史入场</strong><span>{{ signal.outcome_label }}</span></div>
                <p>
                  入场 {{ planPrice(signal.entry_price) }} · 止损 {{ planPrice(signal.stop_price) }} ·
                  {{ signal.planned_reward_risk }}R 目标 {{ planPrice(signal.target_price) }}
                </p>
                <small>
                  下行风险 {{ signal.risk_pct_of_entry.toFixed(2) }}% · 目标涨幅 {{ signal.target_return_pct.toFixed(2) }}% ·
                  {{ signal.exit_date ? `结果日期 ${signal.exit_date}` : "观察期内未结束" }}
                  {{ signal.realized_r === null || signal.realized_r === undefined ? "" : ` · ${signal.realized_r}R` }}
                </small>
              </article>
            </div>
            <p v-else class="empty-cell">最近 60 个交易日没有发现符合当前日线代理规则的入场点。</p>

            <div class="smt-boundary">
              <div>
                <strong>SMT：{{ entryPlan.smt.status }}</strong>
                <p>{{ entryPlan.smt.reason }}</p>
              </div>
              <a href="https://www.bilibili.com/video/BV1boVK6vEHY/" target="_blank" rel="noreferrer">查看作者模块二</a>
            </div>
            <ul class="entry-warnings">
              <li v-for="warning in entryPlan.warnings" :key="warning">{{ warning }}</li>
            </ul>
          </template>
        </div>

        <div ref="chartPanelEl" v-show="workspaceTab === 'entry'" class="panel chart-panel">
          <div class="chart-panel-heading">
            <div>
              <h2>{{ selectedCandidate?.name ?? "单股图表" }} {{ selectedCode }}</h2>
              <p class="hint">图钉标在历史信号实际出现的交易日；绿色达到目标，红色止损，蓝色尚未结束或结果不明确。</p>
            </div>
            <div v-if="entryPlan?.historical_review.signal_count" class="chart-plan-legend" aria-label="历史入场点图例">
              <span class="entry-line">历史入场点</span>
            </div>
          </div>
          <KlineChart
            :rows="klineRows"
            :history-signals="entryPlan?.historical_review.signals ?? []"
            :review-start="entryPlan?.historical_review.start_date ?? ''"
            :stock-label="selectedCandidate?.name ?? selectedCode"
            :active="workspaceTab === 'entry'"
          />
        </div>

      </section>
    </section>
  </main>
</template>
