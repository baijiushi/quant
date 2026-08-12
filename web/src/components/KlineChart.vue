<script setup lang="ts">
import { BarChart, CandlestickChart, ScatterChart } from "echarts/charts";
import {
  AxisPointerComponent,
  DataZoomComponent,
  GridComponent,
  MarkPointComponent,
  TooltipComponent
} from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";

// 按需注册图表模块，避免把地图、关系图等未使用功能装进 K 线异步包。
echarts.use([
  AxisPointerComponent,
  BarChart,
  CandlestickChart,
  CanvasRenderer,
  DataZoomComponent,
  GridComponent,
  MarkPointComponent,
  ScatterChart,
  TooltipComponent
]);

interface KlineRow {
  date: string;
  open: number;
  close: number;
  high: number;
  low: number;
  volume?: number;
  amount?: number;
}

interface HistoricalSignal {
  signal_date: string;
  entry_price: number;
  outcome: "target" | "stopped" | "ambiguous" | "open" | "invalid";
}

interface PriceRange {
  min: number;
  max: number;
}

const props = withDefaults(defineProps<{
  rows: KlineRow[];
  historySignals?: HistoricalSignal[];
  reviewStart?: string;
  stockLabel: string;
  active?: boolean;
}>(), {
  historySignals: () => [],
  reviewStart: "",
  active: true
});

const chartEl = ref<HTMLDivElement | null>(null);
const priceRange = ref<PriceRange | null>(null);
let resizeObserver: ResizeObserver | null = null;

const priceRangeLabel = computed(() => {
  if (!priceRange.value) return "纵轴自动";
  return `${priceRange.value.min.toFixed(2)} – ${priceRange.value.max.toFixed(2)}`;
});

function finitePrice(value: unknown): number | null {
  const numeric = Number(value);
  return Number.isFinite(numeric) && numeric > 0 ? numeric : null;
}

function renderChart() {
  const element = chartEl.value;
  if (!element || !props.active) return;
  const chart = echarts.getInstanceByDom(element) ?? echarts.init(element);
  if (!props.rows.length) {
    chart.clear();
    return;
  }

  const dates = props.rows.map((row) => row.date);
  const candles = props.rows.map((row) => [row.open, row.close, row.low, row.high]);
  // TUShare 日线 volume 为手、amount 为千元；图表统一转换为万手和亿元。
  const volumeWanShou = props.rows.map((row) => (row.volume ?? 0) / 10000);
  const amountYi = props.rows.map((row) => (row.amount ?? 0) / 100000);
  const reviewStartIndex = props.reviewStart ? dates.indexOf(props.reviewStart) : -1;
  const reviewStartPercent = reviewStartIndex >= 0 && dates.length > 1
    ? Math.max(0, reviewStartIndex / (dates.length - 1) * 100)
    : 45;
  const rangePoints: Array<[string, number]> = [];
  const signalPoints: Array<Record<string, unknown>> = [];

  props.historySignals.forEach((signal, index) => {
    const entryPrice = finitePrice(signal.entry_price);
    if (entryPrice === null || !dates.includes(signal.signal_date)) return;
    const outcomeColor = signal.outcome === "target"
      ? "#00897b"
      : signal.outcome === "stopped"
        ? "#c62828"
        : "#1565c0";
    signalPoints.push({
      name: `历史入场点 ${index + 1}`,
      coord: [signal.signal_date, entryPrice],
      value: entryPrice,
      symbol: "pin",
      symbolSize: 58,
      itemStyle: { color: outcomeColor, borderColor: "#ffffff", borderWidth: 2 },
      label: {
        color: "#ffffff",
        fontSize: 9,
        fontWeight: 800,
        lineHeight: 12,
        formatter: `历史入场\n${entryPrice.toFixed(2)}`
      }
    });
    // 透明散点让历史计划价格也参与 ECharts 自动纵轴范围计算。
    rangePoints.push([signal.signal_date, entryPrice]);
  });

  chart.resize();
  chart.setOption({
    animation: false,
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "cross" },
      backgroundColor: "rgba(16, 47, 51, 0.96)",
      borderColor: "rgba(139, 205, 196, 0.42)",
      textStyle: { color: "#f2fbf9", fontSize: 13 },
      formatter(params: unknown) {
        const items = Array.isArray(params) ? params : [params];
        const first = items[0] as { dataIndex?: number; axisValue?: string } | undefined;
        const index = first?.dataIndex ?? 0;
        const row = props.rows[index];
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
      { left: 72, right: 36, top: 36, height: "61%" },
      { left: 72, right: 36, top: "72%", height: "15%" }
    ],
    xAxis: [
      {
        type: "category",
        data: dates,
        boundaryGap: true,
        axisLine: { lineStyle: { color: "#78908f" } },
        axisLabel: { color: "#526a6e", fontSize: 12 }
      },
      { type: "category", data: dates, gridIndex: 1, axisLabel: { show: false } }
    ],
    yAxis: [
      {
        name: "价格",
        scale: true,
        min: priceRange.value?.min ?? null,
        max: priceRange.value?.max ?? null,
        nameTextStyle: { color: "#38545a", fontSize: 12, fontWeight: 700 },
        axisLine: { lineStyle: { color: "#78908f" } },
        axisLabel: { color: "#38545a", fontSize: 12, margin: 12 },
        splitLine: { lineStyle: { color: "#dfe8e6" } }
      },
      {
        name: "成交量(万手)",
        scale: true,
        gridIndex: 1,
        nameTextStyle: { color: "#526a6e", fontSize: 11 },
        splitLine: { show: false },
        axisLabel: {
          color: "#526a6e",
          fontSize: 11,
          formatter(value: number) {
            return value.toFixed(0);
          }
        }
      }
    ],
    dataZoom: [
      {
        type: "inside",
        xAxisIndex: [0, 1],
        start: reviewStartPercent,
        end: 100,
        zoomOnMouseWheel: false,
        moveOnMouseWheel: false
      },
      { show: true, xAxisIndex: [0, 1], start: reviewStartPercent, end: 100, bottom: 8, height: 22 }
    ],
    series: [
      {
        name: "K线",
        type: "candlestick",
        data: candles,
        itemStyle: {
          color: "#c94f3d",
          color0: "#208c71",
          borderColor: "#c94f3d",
          borderColor0: "#208c71"
        },
        markPoint: { silent: true, animation: false, data: signalPoints }
      },
      {
        name: "计划价格范围",
        type: "scatter",
        data: rangePoints,
        symbolSize: 0,
        silent: true,
        tooltip: { show: false },
        itemStyle: { opacity: 0 }
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
  }, { replaceMerge: ["series"] });
}

function handleWheel(event: WheelEvent) {
  const element = chartEl.value;
  if (!element || !props.rows.length || event.deltaY === 0) return;
  const chart = echarts.getInstanceByDom(element);
  if (!chart) return;

  const rect = element.getBoundingClientRect();
  const localX = event.clientX - rect.left;
  const localY = event.clientY - rect.top;
  const gridTop = 36;
  const gridBottom = gridTop + chart.getHeight() * 0.61;
  const overPriceArea = localY >= gridTop && localY <= gridBottom;
  const overPriceAxis = localX >= 0 && localX <= 72 && overPriceArea;
  if ((!event.shiftKey || !overPriceArea) && !overPriceAxis) return;

  const dataLow = Math.min(...props.rows.map((row) => Number(row.low)).filter(Number.isFinite));
  const dataHigh = Math.max(...props.rows.map((row) => Number(row.high)).filter(Number.isFinite));
  const dataSpan = Math.max(dataHigh - dataLow, dataHigh * 0.02, 0.01);
  const padding = dataSpan * 0.06;
  const currentMin = priceRange.value?.min ?? Math.max(0.01, dataLow - padding);
  const currentMax = priceRange.value?.max ?? dataHigh + padding;
  if (!Number.isFinite(currentMin) || !Number.isFinite(currentMax) || currentMax <= currentMin) return;

  event.preventDefault();
  const currentSpan = currentMax - currentMin;
  // 鼠标所指价格保持不动，只收缩或放大它上下两侧的区间，交互不会突然跳轴。
  const anchorRatio = Math.max(0, Math.min(1, (gridBottom - localY) / (gridBottom - gridTop)));
  const anchor = currentMin + currentSpan * anchorRatio;
  const requestedFactor = event.deltaY < 0 ? 0.84 : 1.18;
  // 限制极端缩放，避免纵轴接近零跨度或一次滚动后看不到全部行情。
  const targetSpan = Math.min(dataSpan * 20, Math.max(dataSpan * 0.035, currentSpan * requestedFactor));
  const factor = targetSpan / currentSpan;
  let nextMin = anchor - (anchor - currentMin) * factor;
  let nextMax = anchor + (currentMax - anchor) * factor;
  if (nextMin < 0.01) {
    nextMax += 0.01 - nextMin;
    nextMin = 0.01;
  }

  priceRange.value = { min: nextMin, max: nextMax };
  chart.setOption({ yAxis: [{ min: nextMin, max: nextMax }] });
}

function resetPriceAxis() {
  if (!priceRange.value) return;
  priceRange.value = null;
  const chart = chartEl.value ? echarts.getInstanceByDom(chartEl.value) : null;
  chart?.clear();
  renderChart();
}

watch(
  () => [props.rows, props.historySignals, props.reviewStart],
  async () => {
    priceRange.value = null;
    await nextTick();
    renderChart();
  },
  { deep: true }
);

watch(
  () => props.active,
  async (active) => {
    if (!active) return;
    await nextTick();
    renderChart();
    chartEl.value && echarts.getInstanceByDom(chartEl.value)?.resize();
  }
);

onMounted(async () => {
  await nextTick();
  if (!chartEl.value) return;
  resizeObserver = new ResizeObserver(() => {
    chartEl.value && echarts.getInstanceByDom(chartEl.value)?.resize();
  });
  resizeObserver.observe(chartEl.value);
  renderChart();
});

onUnmounted(() => {
  resizeObserver?.disconnect();
  resizeObserver = null;
  if (chartEl.value) echarts.getInstanceByDom(chartEl.value)?.dispose();
});
</script>

<template>
  <div class="chart-interaction-bar">
    <div>
      <strong>纵轴缩放</strong>
      <span>按住 Shift 在主图滚轮，或将鼠标放在左侧价格轴上滚动；双击图表恢复。</span>
    </div>
    <span class="chart-range-label">{{ priceRangeLabel }}</span>
    <button type="button" :disabled="!priceRange" @click="resetPriceAxis">恢复纵轴</button>
  </div>
  <div
    ref="chartEl"
    class="chart"
    role="img"
    :aria-label="`${stockLabel} K 线、成交量与历史入场点图表`"
    @wheel="handleWheel"
    @dblclick="resetPriceAxis"
  ></div>
</template>
