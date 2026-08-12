<script setup lang="ts">
import { RefreshIcon, SearchIcon } from "tdesign-icons-vue-next";
import { computed, ref } from "vue";

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

interface StrategyInfo {
  id: string;
  name: string;
}

const props = defineProps<{
  candidates: Candidate[];
  selectedCode: string;
  running: boolean;
  activeStrategy: string;
  strategies: StrategyInfo[];
}>();

const emit = defineEmits<{
  refresh: [];
  select: [candidate: Candidate];
}>();

const query = ref("");
const filteredCandidates = computed(() => {
  const needle = query.value.trim().toLowerCase();
  if (!needle) return props.candidates;
  return props.candidates.filter((item) =>
    item.code.toLowerCase().includes(needle) || item.name.toLowerCase().includes(needle)
  );
});

const activeStrategyName = computed(
  () => props.strategies.find((item) => item.id === props.activeStrategy)?.name ?? props.activeStrategy
);

const factorLabel = computed(() => {
  if (props.activeStrategy === "volume_new_high") return "相关系数";
  if (props.activeStrategy === "high_52w_momentum") return "距52周高点";
  return "J";
});

function extraNumber(item: Candidate, key: string): number {
  const value = Number(item.extra?.[key] ?? 0);
  return Number.isFinite(value) ? value : 0;
}

function factorValue(item: Candidate): string {
  if (item.strategy === "volume_new_high") return extraNumber(item, "high_volume_corr").toFixed(3);
  if (item.strategy === "high_52w_momentum") return `${extraNumber(item, "distance_to_high_pct").toFixed(1)}%`;
  return extraNumber(item, "J").toFixed(1);
}

function turnoverYi(value: unknown): string {
  const numeric = Number(value ?? 0);
  return Number.isFinite(numeric) ? (numeric / 100000).toFixed(2) : "0.00";
}

function marketLabel(value: unknown): string {
  return ({ main: "主板", gem: "创业板", star: "科创板", bse: "北交所" } as Record<string, string>)[String(value)] ?? String(value ?? "-");
}

function strategyName(value: string): string {
  return props.strategies.find((item) => item.id === value)?.name ?? value;
}
</script>

<template>
  <section class="panel candidate-panel">
    <div class="candidate-heading">
      <div>
        <div class="title-line">
          <h2>候选股票</h2>
          <span>{{ activeStrategyName }}</span>
        </div>
        <p>共 {{ candidates.length }} 只，点击一行直接打开 K 线与入场检查</p>
      </div>
      <div class="candidate-tools">
        <label class="candidate-search">
          <SearchIcon size="17px" />
          <input v-model="query" aria-label="筛选候选股票" type="search" placeholder="代码或名称" />
        </label>
        <button type="button" class="refresh-button" :disabled="running" @click="emit('refresh')">
          <RefreshIcon size="17px" />
          {{ running ? "运行中" : "刷新结果" }}
        </button>
      </div>
    </div>

    <div class="table-wrap candidate-table-wrap">
      <table>
        <thead>
          <tr>
            <th>代码</th>
            <th>名称</th>
            <th class="numeric">收盘</th>
            <th class="numeric">评分</th>
            <th class="numeric">{{ factorLabel }}</th>
            <th class="numeric">滚动成交额(亿元)</th>
            <th class="numeric">量比</th>
            <th>策略</th>
            <th>板块</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="item in filteredCandidates"
            :key="item.code"
            :class="{ selected: item.code === selectedCode }"
            tabindex="0"
            :aria-label="`打开 ${item.name} ${item.code} 的 K 线图`"
            @click="emit('select', item)"
            @keydown.enter.prevent="emit('select', item)"
            @keydown.space.prevent="emit('select', item)"
          >
            <td><strong>{{ item.code }}</strong></td>
            <td>{{ item.name }}</td>
            <td class="numeric">{{ item.close?.toFixed(2) }}</td>
            <td class="numeric score-cell">{{ item.score?.toFixed(4) }}</td>
            <td class="numeric">{{ factorValue(item) }}</td>
            <td class="numeric">{{ turnoverYi(item.turnover_n) }}</td>
            <td class="numeric">{{ Number(item.extra?.volume_ratio ?? 0).toFixed(2) }}</td>
            <td>{{ strategyName(item.strategy) }}</td>
            <td>{{ marketLabel(item.extra?.market) }}</td>
          </tr>
          <tr v-if="!filteredCandidates.length">
            <td colspan="9" class="empty-cell">
              {{ running ? "任务运行中，旧候选已清空，等待新结果..." : query ? "没有匹配的候选股票" : "暂无候选结果" }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<style scoped>
.candidate-panel {
  min-height: 700px;
  padding: 0;
  overflow: hidden;
}

.candidate-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  min-height: 76px;
  padding: 14px 16px;
  border-bottom: 1px solid var(--wb-line);
}

.title-line {
  display: flex;
  align-items: center;
  gap: 9px;
}

.title-line span {
  padding: 4px 7px;
  border-radius: 999px;
  background: var(--wb-primary-soft);
  color: #075e59;
  font-size: 11px;
  font-weight: 800;
}

.candidate-heading p {
  margin: 4px 0 0;
  color: #4c676c;
  font-size: 12px;
}

.candidate-tools {
  display: flex;
  align-items: center;
  gap: 8px;
}

.candidate-search {
  display: flex;
  align-items: center;
  gap: 7px;
  min-width: 190px;
  min-height: 36px;
  padding: 0 10px;
  border: 1px solid var(--wb-line);
  border-radius: var(--wb-radius-sm);
  background: var(--wb-surface-muted);
  color: var(--wb-muted);
}

.candidate-search:focus-within {
  border-color: var(--wb-primary);
  box-shadow: 0 0 0 3px rgba(8, 127, 120, 0.12);
}

.candidate-search input {
  min-height: 32px;
  padding: 0;
  border: 0;
  border-radius: 0;
  background: transparent;
  box-shadow: none;
}

.candidate-search input:focus {
  border: 0;
  background: transparent;
  box-shadow: none;
}

.refresh-button {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 36px;
  padding: 7px 11px;
  border-radius: var(--wb-radius-sm);
  background: var(--wb-primary);
  font-size: 13px;
  box-shadow: none;
}

.refresh-button:hover:not(:disabled) {
  background: var(--wb-primary-hover);
  box-shadow: none;
  transform: none;
}

.candidate-table-wrap {
  height: clamp(620px, calc(100vh - 235px), 820px);
  max-height: none;
  border: 0;
  border-radius: 0;
}

th,
td {
  height: 46px;
  padding: 9px 13px;
  font-variant-numeric: tabular-nums;
}

th {
  top: 0;
  background: #f1f6f5;
  color: #405e63;
  font-size: 12px;
  letter-spacing: 0.01em;
}

td {
  color: var(--wb-text);
  font-size: 13px;
}

td:first-child strong {
  color: var(--wb-info);
  font-weight: 650;
}

.numeric {
  text-align: right;
}

.score-cell {
  color: #075e59;
  font-weight: 700;
}

tbody tr:hover {
  background: #edf7f5;
}

tr.selected {
  background: #d8eeea;
  box-shadow: inset 3px 0 0 var(--wb-primary);
}

@media (max-width: 720px) {
  .candidate-panel {
    min-height: 620px;
  }

  .candidate-table-wrap {
    height: 560px;
  }

  .candidate-heading {
    align-items: stretch;
    flex-direction: column;
  }

  .candidate-tools {
    width: 100%;
  }

  .candidate-search {
    flex: 1 1 auto;
    width: auto;
    min-width: 0;
  }

  .refresh-button {
    flex: 0 0 auto;
    white-space: nowrap;
  }
}
</style>
