<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";

interface StrategyMeta {
  id: string;
  name: string;
  description: string;
  default_config: Record<string, unknown>;
}

interface ConfigPayload {
  data_mode: string;
  fetch: Record<string, unknown>;
  active_strategy: string;
  global: Record<string, any>;
  strategies: Record<string, Record<string, any>>;
}

interface BacktestStatus {
  backtest_id: string;
  strategy_id: string;
  start_date: string;
  end_date: string;
  holding_days: number;
  status: "queued" | "running" | "cancelling" | "success" | "failed" | "cancelled";
  stage: string;
  progress: number;
  processed_days: number;
  total_days: number;
  started_at?: string | null;
  finished_at?: string | null;
  error?: string | null;
  logs: string[];
}

interface DailyReturn {
  day: number;
  date: string;
  close: number | null;
  return_pct: number | null;
  carried: boolean;
}

interface BacktestTrade {
  rank: number;
  signal_rank: number;
  signal_date: string;
  code: string;
  name: string;
  strategy_score: number;
  signal_close: number;
  entry_date: string | null;
  entry_open: number | null;
  exit_date: string | null;
  exit_close: number | null;
  final_return_pct: number | null;
  max_gain_pct: number | null;
  max_drawdown_pct: number | null;
  status: string;
  note: string;
  daily_returns: DailyReturn[];
}

interface BacktestResult {
  backtest_id: string;
  generated_at: string;
  request: Record<string, any>;
  metrics: Record<string, number | null>;
  horizon_stats: Array<Record<string, number | null>>;
  daily_stats: Array<Record<string, any>>;
  stock_ranking: Array<Record<string, any>>;
  trades: BacktestTrade[];
  meta: Record<string, any>;
}

const strategies = ref<StrategyMeta[]>([]);
const config = ref<ConfigPayload | null>(null);
const status = ref<BacktestStatus | null>(null);
const result = ref<BacktestResult | null>(null);
const booting = ref(true);
const actionError = ref("");
const logPanel = ref<HTMLElement | null>(null);
const activeTab = ref<"trades" | "daily" | "stocks">("trades");
const searchText = ref("");
const sortMode = ref("return_desc");
const page = ref(1);
const pageSize = 100;
let pollTimer: number | undefined;

const form = reactive({
  strategy_id: "b1",
  start_date: "",
  end_date: "",
  holding_days: 5,
});

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
    ...init,
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || `请求失败：${response.status}`);
  }
  return response.json();
}

const strategy = computed(() => strategies.value.find((item) => item.id === form.strategy_id));
const strategyConfig = computed<Record<string, any>>(() => {
  if (!config.value) return {};
  if (!config.value.strategies[form.strategy_id]) {
    config.value.strategies[form.strategy_id] = { ...(strategy.value?.default_config || {}) };
  }
  return config.value.strategies[form.strategy_id];
});
const globalConfig = computed(() => config.value?.global || {});
const isRunning = computed(() => ["queued", "running", "cancelling"].includes(status.value?.status || ""));
const displayHoldingDays = computed(() => Number(result.value?.request.holding_days || form.holding_days));
const holdingColumns = computed(() => Array.from({ length: displayHoldingDays.value }, (_, index) => index + 1));

const filteredTrades = computed(() => {
  const needle = searchText.value.trim().toLowerCase();
  const rows = [...(result.value?.trades || [])].filter((item) => {
    if (!needle) return true;
    return item.code.includes(needle) || item.name.toLowerCase().includes(needle) || item.signal_date.includes(needle);
  });
  rows.sort((left, right) => {
    if (sortMode.value === "return_asc") return numericSort(left.final_return_pct, right.final_return_pct, true);
    if (sortMode.value === "date_desc") return right.signal_date.localeCompare(left.signal_date);
    if (sortMode.value === "score_desc") return right.strategy_score - left.strategy_score;
    return numericSort(left.final_return_pct, right.final_return_pct, false);
  });
  return rows;
});
const pageCount = computed(() => Math.max(1, Math.ceil(filteredTrades.value.length / pageSize)));
const pagedTrades = computed(() => filteredTrades.value.slice((page.value - 1) * pageSize, page.value * pageSize));

function numericSort(left: number | null, right: number | null, ascending: boolean) {
  const l = left ?? (ascending ? Number.POSITIVE_INFINITY : Number.NEGATIVE_INFINITY);
  const r = right ?? (ascending ? Number.POSITIVE_INFINITY : Number.NEGATIVE_INFINITY);
  return ascending ? l - r : r - l;
}

function setMarket(market: string) {
  if (!config.value) return;
  const current = new Set<string>(globalConfig.value.markets || []);
  if (current.has(market)) current.delete(market);
  else current.add(market);
  if (!current.size) return;
  config.value.global.markets = Array.from(current);
}

function marketSelected(market: string) {
  return (globalConfig.value.markets || []).includes(market);
}

function parameterNumber(key: string, fallback = 0) {
  const value = Number(strategyConfig.value[key]);
  return Number.isFinite(value) ? value : fallback;
}

function updateParameter(key: string, event: Event) {
  strategyConfig.value[key] = Number((event.target as HTMLInputElement).value);
}

function updateBoolean(key: string, event: Event) {
  strategyConfig.value[key] = (event.target as HTMLInputElement).checked;
}

async function initialize() {
  booting.value = true;
  actionError.value = "";
  try {
    const [strategyPayload, configPayload, meta, current] = await Promise.all([
      api<{ strategies: StrategyMeta[] }>("/api/strategies"),
      api<ConfigPayload>("/api/config"),
      api<{ first_date: string | null; latest_date: string | null; suggested_start_date: string | null }>("/api/backtests/meta"),
      api<{ backtest: BacktestStatus | null }>("/api/backtests/current"),
    ]);
    strategies.value = strategyPayload.strategies;
    config.value = configPayload;
    form.strategy_id = current.backtest?.strategy_id || configPayload.active_strategy || strategyPayload.strategies[0]?.id || "b1";
    form.start_date = current.backtest?.start_date || meta.suggested_start_date || meta.first_date || "";
    form.end_date = current.backtest?.end_date || meta.latest_date || "";
    form.holding_days = current.backtest?.holding_days || 5;
    status.value = current.backtest;
    if (current.backtest?.status === "success") await loadResult(current.backtest.backtest_id);
    if (current.backtest && ["queued", "running", "cancelling"].includes(current.backtest.status)) startPolling();
  } catch (error) {
    actionError.value = error instanceof Error ? error.message : String(error);
  } finally {
    booting.value = false;
  }
}

async function startBacktest() {
  if (!config.value || !form.start_date || !form.end_date) return;
  actionError.value = "";
  result.value = null;
  page.value = 1;
  try {
    status.value = await api<BacktestStatus>("/api/backtests", {
      method: "POST",
      body: JSON.stringify({
        strategy_id: form.strategy_id,
        start_date: form.start_date,
        end_date: form.end_date,
        holding_days: form.holding_days,
        config: config.value,
      }),
    });
    startPolling();
  } catch (error) {
    actionError.value = error instanceof Error ? error.message : String(error);
  }
}

async function cancelBacktest() {
  if (!status.value) return;
  try {
    status.value = await api<BacktestStatus>(`/api/backtests/${status.value.backtest_id}/cancel`, { method: "POST" });
  } catch (error) {
    actionError.value = error instanceof Error ? error.message : String(error);
  }
}

function startPolling() {
  if (pollTimer) window.clearInterval(pollTimer);
  pollTimer = window.setInterval(refreshStatus, 900);
  void refreshStatus();
}

async function refreshStatus() {
  if (!status.value) return;
  try {
    const next = await api<BacktestStatus>(`/api/backtests/${status.value.backtest_id}`);
    status.value = next;
    if (["success", "failed", "cancelled"].includes(next.status)) {
      if (pollTimer) window.clearInterval(pollTimer);
      pollTimer = undefined;
      if (next.status === "success") await loadResult(next.backtest_id);
    }
  } catch (error) {
    actionError.value = error instanceof Error ? error.message : String(error);
  }
}

async function loadResult(backtestId: string) {
  result.value = await api<BacktestResult>(`/api/backtests/${backtestId}/result`);
}

function dailyReturn(trade: BacktestTrade, day: number) {
  return trade.daily_returns.find((item) => item.day === day)?.return_pct ?? null;
}

function formatNumber(value: number | null | undefined, digits = 2) {
  return value == null || !Number.isFinite(value) ? "-" : value.toFixed(digits);
}

function formatPercent(value: number | null | undefined) {
  return value == null || !Number.isFinite(value) ? "-" : `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
}

function returnClass(value: number | null | undefined) {
  if (value == null) return "neutral";
  return value > 0 ? "positive" : value < 0 ? "negative" : "neutral";
}

function statusLabel(value: string) {
  return ({ queued: "排队中", running: "运行中", cancelling: "终止中", success: "已完成", failed: "失败", cancelled: "已终止" } as Record<string, string>)[value] || value;
}

function exportCsv() {
  if (!result.value) return;
  const horizonHeaders = holdingColumns.value.map((day) => `D${day}收益`).join(",");
  const rows = result.value.trades.map((trade) => [
    trade.rank, trade.signal_date, trade.code, trade.name, trade.strategy_score, trade.signal_close,
    trade.entry_date || "", trade.entry_open ?? "", trade.exit_date || "", trade.exit_close ?? "",
    ...holdingColumns.value.map((day) => dailyReturn(trade, day) ?? ""),
    trade.final_return_pct ?? "", trade.max_gain_pct ?? "", trade.max_drawdown_pct ?? "", trade.status, trade.note,
  ]);
  const header = `排名,信号日,代码,名称,策略评分,信号收盘,买入日,买入开盘,卖出日,卖出收盘,${horizonHeaders},最终收益,最大涨幅,最大回撤,状态,说明`;
  const csv = [header, ...rows.map((row) => row.map((cell) => `"${String(cell).replace(/"/g, '""')}"`).join(","))].join("\n");
  const blob = new Blob(["\ufeff", csv], { type: "text/csv;charset=utf-8" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `backtest_${result.value.request.strategy_id}_${result.value.request.start_date}_${result.value.request.end_date}.csv`;
  link.click();
  URL.revokeObjectURL(link.href);
}

watch(() => status.value?.logs.length, async () => {
  await nextTick();
  if (logPanel.value) logPanel.value.scrollTop = logPanel.value.scrollHeight;
});
watch([searchText, sortMode], () => { page.value = 1; });
watch(() => form.strategy_id, (value) => {
  if (config.value && !config.value.strategies[value]) {
    config.value.strategies[value] = { ...(strategy.value?.default_config || {}) };
  }
});

onMounted(initialize);
onBeforeUnmount(() => { if (pollTimer) window.clearInterval(pollTimer); });
</script>

<template>
  <div class="backtest-shell">
    <header class="backtest-hero">
      <div>
        <p class="eyebrow">STRATEGY REPLAY LAB</p>
        <h1>逐日选股回测</h1>
        <p class="hero-copy">用当日收盘信号，在下一交易日开盘买入，观察后续每个交易日的真实涨跌路径。</p>
      </div>
      <div class="backtest-badges">
        <span>SQLite 行情</span>
        <span>次日开盘</span>
        <span>独立信号统计</span>
      </div>
    </header>

    <main v-if="!booting" class="backtest-layout">
      <aside class="panel backtest-controls">
        <div class="panel-title">
          <div>
            <p class="mini-label">实验设置</p>
            <h2>回测参数</h2>
          </div>
          <span class="data-dot">本地数据</span>
        </div>

        <label class="field">
          <span>选股策略</span>
          <select v-model="form.strategy_id" :disabled="isRunning">
            <option v-for="item in strategies" :key="item.id" :value="item.id">{{ item.name }}</option>
          </select>
          <small class="hint">{{ strategy?.description }}</small>
        </label>

        <div class="date-pair">
          <label class="field">
            <span>开始日期</span>
            <input v-model="form.start_date" type="date" :disabled="isRunning" />
          </label>
          <label class="field">
            <span>结束日期</span>
            <input v-model="form.end_date" type="date" :disabled="isRunning" />
          </label>
        </div>

        <label class="field">
          <span>持有交易日 X</span>
          <input v-model.number="form.holding_days" type="number" min="1" max="60" :disabled="isRunning" />
          <small class="hint">D1 是买入当天收盘收益，最终收益按第 X 个交易日收盘计算。</small>
        </label>

        <p class="section-title">板块范围</p>
        <div class="market-grid">
          <button type="button" :class="{ selected: marketSelected('main') }" :disabled="isRunning" @click="setMarket('main')">主板</button>
          <button type="button" :class="{ selected: marketSelected('gem') }" :disabled="isRunning" @click="setMarket('gem')">创业板</button>
          <button type="button" :class="{ selected: marketSelected('star') }" :disabled="isRunning" @click="setMarket('star')">科创板</button>
          <button type="button" :class="{ selected: marketSelected('bse') }" :disabled="isRunning" @click="setMarket('bse')">北交所</button>
        </div>

        <div class="date-pair">
          <label class="field">
            <span>流动性 Top M</span>
            <input v-model.number="globalConfig.top_m" type="number" min="0" max="6000" :disabled="isRunning" />
          </label>
          <label class="field">
            <span>成交额窗口（日）</span>
            <input v-model.number="globalConfig.n_turnover_days" type="number" min="1" max="250" :disabled="isRunning" />
          </label>
        </div>

        <template v-if="form.strategy_id === 'b1'">
          <p class="section-title">B1 参数</p>
          <div class="parameter-note">J 值越低越超卖；均线周期越长，信号越稳健但数量通常越少。回测沿用当前选股策略的全部条件。</div>
          <div class="date-pair">
            <label class="field"><span>KDJ 周期</span><input :value="parameterNumber('kdj_period', 9)" type="number" min="3" max="60" :disabled="isRunning" @input="updateParameter('kdj_period', $event)" /></label>
            <label class="field"><span>J 阈值</span><input :value="parameterNumber('j_threshold', 10)" type="number" min="-50" max="100" :disabled="isRunning" @input="updateParameter('j_threshold', $event)" /></label>
          </div>
          <div class="ma-grid">
            <label class="field" v-for="key in ['zx_m1', 'zx_m2', 'zx_m3', 'zx_m4']" :key="key">
              <span>{{ key.toUpperCase().replace('ZX_', '') }} 均线</span>
              <input :value="parameterNumber(key)" type="number" min="2" max="500" :disabled="isRunning" @input="updateParameter(key, $event)" />
            </label>
          </div>
          <label class="switch-row"><input :checked="Boolean(strategyConfig.require_weekly_ma_bull)" type="checkbox" :disabled="isRunning" @change="updateBoolean('require_weekly_ma_bull', $event)" /><span>要求周线多头排列</span></label>
          <label class="switch-row"><input :checked="Boolean(strategyConfig.require_macd_bull)" type="checkbox" :disabled="isRunning" @change="updateBoolean('require_macd_bull', $event)" /><span>要求 MACD 多头</span></label>
          <label class="switch-row"><input :checked="Boolean(strategyConfig.require_volume_ratio)" type="checkbox" :disabled="isRunning" @change="updateBoolean('require_volume_ratio', $event)" /><span>启用成交量过滤</span></label>
        </template>

        <template v-else>
          <p class="section-title">缩量新高参数</p>
          <div class="parameter-note">相关性越负越符合“价升量缩”；新高窗口越长，信号越稀缺。量比上限 0.85 表示成交量不高于均量的 85%。</div>
          <div class="date-pair">
            <label class="field"><span>相关性窗口</span><input :value="parameterNumber('corr_window', 10)" type="number" min="3" max="120" :disabled="isRunning" @input="updateParameter('corr_window', $event)" /></label>
            <label class="field"><span>波动率窗口</span><input :value="parameterNumber('stddev_window', 10)" type="number" min="3" max="120" :disabled="isRunning" @input="updateParameter('stddev_window', $event)" /></label>
            <label class="field"><span>新高窗口</span><input :value="parameterNumber('new_high_window', 60)" type="number" min="10" max="500" :disabled="isRunning" @input="updateParameter('new_high_window', $event)" /></label>
            <label class="field"><span>最大量比</span><input :value="parameterNumber('max_volume_ratio', 0.85)" type="number" min="0.05" max="3" step="0.05" :disabled="isRunning" @input="updateParameter('max_volume_ratio', $event)" /></label>
          </div>
        </template>

        <div class="assumption-card">
          <strong>统计口径</strong>
          <span>参数只用于本次回测，不会覆盖选股控制台。暂不计佣金、印花税、滑点和涨跌停无法成交；每条信号独立计算，不模拟资金仓位。</span>
        </div>
        <button class="run-backtest" :disabled="isRunning || !form.start_date || !form.end_date" @click="startBacktest">
          {{ isRunning ? "回测运行中" : "开始逐日回测" }}
        </button>
        <p v-if="actionError" class="action-error">{{ actionError }}</p>
      </aside>

      <section class="backtest-content">
        <article class="panel task-card">
          <div class="panel-title">
            <div>
              <p class="mini-label">EXECUTION</p>
              <h2>任务进度</h2>
            </div>
            <button v-if="isRunning" class="danger-button" :disabled="status?.status === 'cancelling'" @click="cancelBacktest">终止回测</button>
          </div>
          <div v-if="status" class="status-line">
            <span :class="['status-chip', status.status]">{{ statusLabel(status.status) }}</span>
            <span>{{ status.stage }}</span>
            <span v-if="status.total_days">{{ status.processed_days }}/{{ status.total_days }} 个交易日</span>
            <span class="run-id">任务 {{ status.backtest_id }}</span>
          </div>
          <div v-if="status" class="progress-track"><div :style="{ width: `${status.progress}%` }"></div></div>
          <div v-if="status" ref="logPanel" class="backtest-log">
            <p v-for="(line, index) in status.logs" :key="index">{{ line }}</p>
            <p v-if="!status.logs.length" class="muted">等待任务输出…</p>
          </div>
          <div v-else class="empty-state compact"><strong>尚未运行回测</strong><span>选择日期和策略后开始，历史任务会在刷新页面后自动恢复。</span></div>
          <p v-if="status?.error" class="action-error">{{ status.error }}</p>
        </article>

        <template v-if="result">
          <section class="metric-grid">
            <article class="metric-card accent"><span>胜率</span><strong>{{ formatPercent(result.metrics.win_rate_pct) }}</strong><small>{{ result.metrics.win_count }} 胜 / {{ result.metrics.loss_count }} 负</small></article>
            <article class="metric-card"><span>平均收益</span><strong :class="returnClass(result.metrics.average_return_pct)">{{ formatPercent(result.metrics.average_return_pct) }}</strong><small>中位数 {{ formatPercent(result.metrics.median_return_pct) }}</small></article>
            <article class="metric-card"><span>盈亏比</span><strong>{{ formatNumber(result.metrics.profit_loss_ratio) }}</strong><small>平均盈利 / 平均亏损</small></article>
            <article class="metric-card"><span>有效交易</span><strong>{{ result.metrics.completed_count }}</strong><small>共 {{ result.metrics.signal_count }} 条信号</small></article>
          </section>

          <article class="panel horizon-card">
            <div class="panel-title"><div><p class="mini-label">HORIZON CURVE</p><h2>持有期表现</h2></div><span class="data-dot">D1 → D{{ displayHoldingDays }}</span></div>
            <div class="horizon-list">
              <div v-for="item in result.horizon_stats" :key="String(item.day)" class="horizon-item">
                <strong>D{{ item.day }}</strong>
                <div class="horizon-bar"><span :class="returnClass(item.average_return_pct)" :style="{ width: `${Math.min(100, Math.abs(Number(item.average_return_pct || 0)) * 8 + 4)}%` }"></span></div>
                <b :class="returnClass(item.average_return_pct)">{{ formatPercent(item.average_return_pct) }}</b>
                <small>胜率 {{ formatPercent(item.win_rate_pct) }} · {{ item.sample_count }} 笔</small>
              </div>
            </div>
          </article>

          <article class="panel ranking-card">
            <div class="ranking-head">
              <div><p class="mini-label">RANKING</p><h2>回测排行</h2></div>
              <div class="ranking-actions">
                <button class="secondary-button" @click="exportCsv">导出 CSV</button>
              </div>
            </div>
            <div class="tabs">
              <button :class="{ active: activeTab === 'trades' }" @click="activeTab = 'trades'">逐笔排行</button>
              <button :class="{ active: activeTab === 'daily' }" @click="activeTab = 'daily'">每日汇总</button>
              <button :class="{ active: activeTab === 'stocks' }" @click="activeTab = 'stocks'">个股排行</button>
            </div>

            <template v-if="activeTab === 'trades'">
              <div class="table-toolbar">
                <input v-model="searchText" type="search" placeholder="搜索代码、名称或日期" />
                <select v-model="sortMode">
                  <option value="return_desc">最终收益从高到低</option>
                  <option value="return_asc">最终收益从低到高</option>
                  <option value="date_desc">信号日期从近到远</option>
                  <option value="score_desc">策略评分从高到低</option>
                </select>
                <span>共 {{ filteredTrades.length }} 条</span>
              </div>
              <div class="table-scroll">
                <table class="backtest-table wide-table">
                  <thead><tr><th>排行</th><th>信号日</th><th>股票</th><th>策略分</th><th>信号收盘</th><th>买入日 / 开盘</th><th v-for="day in holdingColumns" :key="day">D{{ day }}</th><th>最终收益</th><th>最大涨幅</th><th>最大回撤</th><th>状态</th></tr></thead>
                  <tbody>
                    <tr v-for="trade in pagedTrades" :key="`${trade.signal_date}-${trade.code}`">
                      <td class="rank-cell">{{ trade.rank }}</td><td>{{ trade.signal_date }}</td><td><strong>{{ trade.name }}</strong><small>{{ trade.code }}</small></td><td>{{ formatNumber(trade.strategy_score, 4) }}</td><td>{{ formatNumber(trade.signal_close) }}</td><td><strong>{{ trade.entry_date || '-' }}</strong><small>{{ trade.entry_open == null ? '-' : `¥${formatNumber(trade.entry_open)}` }}</small></td>
                      <td v-for="day in holdingColumns" :key="day" :class="returnClass(dailyReturn(trade, day))">{{ formatPercent(dailyReturn(trade, day)) }}</td>
                      <td :class="['final-return', returnClass(trade.final_return_pct)]">{{ formatPercent(trade.final_return_pct) }}</td><td class="positive">{{ formatPercent(trade.max_gain_pct) }}</td><td class="negative">{{ formatPercent(trade.max_drawdown_pct) }}</td><td><span :class="['trade-status', trade.status]">{{ trade.status === 'completed' ? '完成' : trade.status === 'unexecutable' ? '未成交' : '待完整' }}</span><small class="status-note">{{ trade.note }}</small></td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <div class="pagination"><button :disabled="page <= 1" @click="page--">上一页</button><span>{{ page }} / {{ pageCount }}</span><button :disabled="page >= pageCount" @click="page++">下一页</button></div>
            </template>

            <div v-else-if="activeTab === 'daily'" class="table-scroll">
              <table class="backtest-table"><thead><tr><th>信号日期</th><th>选出数量</th><th>完整交易</th><th>胜率</th><th>平均收益</th></tr></thead><tbody><tr v-for="item in result.daily_stats" :key="item.signal_date"><td>{{ item.signal_date }}</td><td>{{ item.selected_count }}</td><td>{{ item.completed_count }}</td><td>{{ formatPercent(item.win_rate_pct) }}</td><td :class="returnClass(item.average_return_pct)">{{ formatPercent(item.average_return_pct) }}</td></tr></tbody></table>
            </div>

            <div v-else class="table-scroll">
              <table class="backtest-table"><thead><tr><th>排行</th><th>股票</th><th>出现次数</th><th>胜率</th><th>平均收益</th><th>最佳</th><th>最差</th></tr></thead><tbody><tr v-for="item in result.stock_ranking" :key="item.code"><td class="rank-cell">{{ item.rank }}</td><td><strong>{{ item.name }}</strong><small>{{ item.code }}</small></td><td>{{ item.trade_count }}</td><td>{{ formatPercent(item.win_rate_pct) }}</td><td :class="returnClass(item.average_return_pct)">{{ formatPercent(item.average_return_pct) }}</td><td class="positive">{{ formatPercent(item.best_return_pct) }}</td><td class="negative">{{ formatPercent(item.worst_return_pct) }}</td></tr></tbody></table>
            </div>
          </article>
        </template>
      </section>
    </main>
    <div v-else class="boot-panel panel">正在读取本地策略和行情范围…</div>
  </div>
</template>
