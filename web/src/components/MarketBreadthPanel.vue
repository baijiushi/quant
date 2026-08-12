<script setup lang="ts">
import { RefreshIcon } from "tdesign-icons-vue-next";

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

defineProps<{
  breadth: MarketBreadth | null;
  loading: boolean;
  error: string;
}>();

const emit = defineEmits<{ refresh: [] }>();
</script>

<template>
  <section class="panel breadth-panel">
    <div class="panel-title breadth-heading">
      <div>
        <h2>市场宽度与风险状态</h2>
        <p>{{ breadth?.trade_date ?? "最新交易日" }} · {{ breadth?.stock_count ?? 0 }} 只股票</p>
      </div>
      <button type="button" class="icon-button" :disabled="loading" aria-label="刷新市场宽度" @click="emit('refresh')">
        <RefreshIcon size="17px" />
      </button>
    </div>

    <p v-if="error" class="error">{{ error }}</p>
    <p v-else-if="loading && !breadth" class="empty-cell">正在从 SQLite 计算市场宽度...</p>
    <template v-else-if="breadth?.available">
      <div class="breadth-overview">
        <div class="breadth-score">
          <span>宽度得分</span>
          <strong>{{ Number(breadth.score ?? 0).toFixed(1) }}</strong>
          <small>/ 100</small>
        </div>
        <div class="breadth-summary">
          <div><b>{{ breadth.status }}</b><span>风险 {{ breadth.risk_level }}</span></div>
          <p>{{ breadth.summary }}</p>
          <strong>{{ breadth.position_guidance }}</strong>
        </div>
      </div>
      <div class="breadth-components">
        <article v-for="component in breadth.components" :key="component.id">
          <div><span>{{ component.name }}</span><b>{{ component.value_pct.toFixed(1) }}%</b></div>
          <div class="breadth-meter"><i :style="{ width: `${Math.max(0, Math.min(100, component.value_pct))}%` }"></i></div>
          <small>{{ component.signal }} · {{ component.description }}</small>
        </article>
      </div>
      <details class="breadth-method">
        <summary>查看计算说明</summary>
        <p>{{ breadth.methodology }}。{{ breadth.disclaimer }}</p>
      </details>
    </template>
    <p v-else class="empty-cell">{{ breadth?.summary ?? "暂无市场宽度数据" }}</p>
  </section>
</template>

<style scoped>
.breadth-panel {
  padding: 16px;
}

.breadth-heading {
  align-items: flex-start;
  margin-bottom: 13px;
}

.breadth-heading p {
  margin: 4px 0 0;
  color: var(--wb-muted);
  font-family: "IBM Plex Mono", monospace;
  font-size: 11px;
}

.icon-button {
  display: grid;
  place-items: center;
  width: 34px;
  min-height: 34px;
  padding: 0;
  border: 1px solid var(--wb-line);
  border-radius: var(--wb-radius-sm);
  background: var(--wb-surface);
  color: var(--wb-primary);
  box-shadow: none;
}

.icon-button:hover:not(:disabled) {
  border-color: var(--wb-line-strong);
  background: var(--wb-primary-soft);
  box-shadow: none;
  transform: none;
}

.breadth-overview {
  grid-template-columns: 86px minmax(0, 1fr);
  padding: 0;
  border: 1px solid var(--wb-line);
  border-radius: var(--wb-radius-md);
  background: var(--wb-surface-muted);
}

.breadth-score {
  border-radius: 0;
  box-shadow: none;
}

.breadth-score strong {
  font-size: 30px;
}

.breadth-summary {
  padding: 12px 12px 12px 0;
}

.breadth-components {
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 7px;
}

.breadth-components article {
  padding: 9px;
  border-radius: var(--wb-radius-sm);
}

.breadth-method {
  margin-top: 10px;
  color: var(--wb-muted);
  font-size: 11px;
}

.breadth-method summary {
  cursor: pointer;
  font-weight: 700;
}

.breadth-method p {
  margin: 7px 0 0;
  line-height: 1.55;
}

@media (max-width: 620px) {
  .breadth-components {
    grid-template-columns: 1fr;
  }
}
</style>
