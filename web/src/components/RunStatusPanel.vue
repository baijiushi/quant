<script setup lang="ts">
import { ChevronDownIcon, StopCircleIcon } from "tdesign-icons-vue-next";
import { computed, ref, watch } from "vue";

interface RunStatus {
  run_id: string;
  status: "queued" | "running" | "cancelling" | "success" | "failed" | "cancelled";
  stage: string;
  started_at?: string | null;
  finished_at?: string | null;
  error?: string | null;
}

const props = defineProps<{
  status: RunStatus | null;
  progress: number;
  logs: string[];
  running: boolean;
  cancelling: boolean;
  loading: boolean;
}>();

const emit = defineEmits<{ stop: [] }>();
const logsOpen = ref(false);

const statusText = computed(() => {
  const labels: Record<string, string> = {
    queued: "排队中",
    running: "运行中",
    cancelling: "正在终止",
    success: "已完成",
    failed: "运行失败",
    cancelled: "已终止"
  };
  return labels[props.status?.status ?? ""] ?? "空闲";
});

watch(
  () => [props.running, props.status?.status, props.status?.error],
  ([running, status, error]) => {
    if (running || status === "failed" || error) logsOpen.value = true;
  },
  { immediate: true }
);
</script>

<template>
  <section class="panel status task-monitor">
    <div class="panel-title monitor-heading">
      <div>
        <h2>任务状态</h2>
        <p>{{ status ? `任务：${status.run_id}` : "等待启动新的选股任务" }}</p>
      </div>
      <span class="stage-badge" :data-state="status?.status ?? 'idle'">{{ status?.stage ?? "空闲" }}</span>
    </div>

    <template v-if="status">
      <div class="monitor-summary">
        <div><span>状态</span><strong>{{ statusText }}</strong></div>
        <div><span>开始</span><strong>{{ status.started_at ?? "-" }}</strong></div>
        <div><span>结束</span><strong>{{ status.finished_at ?? "-" }}</strong></div>
      </div>

      <div class="progress-wrap" :aria-label="`当前进度 ${progress}%`">
        <div class="progress-meta"><span>{{ status.stage }}</span><strong>{{ progress }}%</strong></div>
        <div class="progress-track"><i :style="{ width: `${progress}%` }"></i></div>
        <div class="run-steps">
          <span :class="{ done: progress >= 20 }">数据</span>
          <span :class="{ done: progress >= 38 }">加载</span>
          <span :class="{ done: progress >= 58 }">流动性</span>
          <span :class="{ done: progress >= 75 }">策略</span>
          <span :class="{ done: progress >= 92 }">结果</span>
        </div>
      </div>

      <div class="monitor-actions">
        <button
          class="monitor-toggle"
          type="button"
          :aria-expanded="logsOpen"
          @click="logsOpen = !logsOpen"
        >
          <ChevronDownIcon :class="{ rotated: logsOpen }" size="17px" />
          {{ logsOpen ? "收起运行日志" : `展开运行日志（${logs.length}）` }}
        </button>
        <button
          v-if="running"
          class="danger-button"
          type="button"
          :disabled="loading || cancelling"
          @click="emit('stop')"
        >
          <StopCircleIcon size="17px" />
          {{ cancelling ? "终止中" : "终止任务" }}
        </button>
      </div>

      <pre v-if="logsOpen && logs.length" class="terminal">{{ logs.join("\n") }}</pre>
      <p v-else-if="logsOpen" class="monitor-empty">任务暂时还没有输出日志。</p>
      <p v-if="status.error" class="error">{{ status.error }}</p>
    </template>
  </section>
</template>

<style scoped>
.task-monitor {
  min-height: 0;
  padding: 16px;
}

.monitor-heading {
  align-items: flex-start;
  margin-bottom: 13px;
}

.monitor-heading p {
  margin: 4px 0 0;
  color: var(--wb-muted);
  font-family: "IBM Plex Mono", monospace;
  font-size: 11px;
}

.monitor-summary {
  display: grid;
  gap: 1px;
  margin-bottom: 12px;
  overflow: hidden;
  border: 1px solid var(--wb-line);
  border-radius: var(--wb-radius-md);
  background: var(--wb-line);
}

.monitor-summary > div {
  display: grid;
  grid-template-columns: 54px minmax(0, 1fr);
  gap: 8px;
  padding: 8px 10px;
  background: var(--wb-surface-muted);
}

.monitor-summary span {
  color: var(--wb-muted);
  font-size: 12px;
}

.monitor-summary strong {
  overflow: hidden;
  color: var(--wb-ink);
  font-family: "IBM Plex Mono", monospace;
  font-size: 11px;
  font-weight: 500;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.monitor-actions {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  margin-top: 11px;
}

.monitor-actions button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  min-height: 34px;
  padding: 6px 10px;
  border-radius: var(--wb-radius-sm);
  font-size: 12px;
  box-shadow: none;
}

.monitor-toggle {
  background: transparent;
  color: var(--wb-text);
}

.monitor-toggle:hover:not(:disabled) {
  background: var(--wb-primary-soft);
  color: #075e59;
  box-shadow: none;
  transform: none;
}

.monitor-toggle svg {
  transition: transform 150ms ease-out;
}

.monitor-toggle svg.rotated {
  transform: rotate(180deg);
}

.terminal {
  min-height: 190px;
  max-height: 360px;
  margin: 12px 0 0;
  border-radius: var(--wb-radius-md);
  font-size: 12px;
  line-height: 1.62;
}

.monitor-empty {
  margin: 12px 0 0;
  padding: 16px;
  background: var(--wb-surface-muted);
  color: var(--wb-muted);
  font-size: 12px;
  text-align: center;
}
</style>
