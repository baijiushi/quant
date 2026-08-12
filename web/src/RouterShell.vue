<script setup lang="ts">
import { ChartLineIcon, DashboardIcon, DataBaseIcon } from "tdesign-icons-vue-next";
import { RouterLink, RouterView } from "vue-router";
</script>

<template>
  <div class="app-frame">
    <aside class="app-sidebar">
      <RouterLink class="brand-link" to="/" aria-label="oversell 首页">
        <span class="brand-mark">OS</span>
        <span class="brand-copy">
          <strong>oversell</strong>
          <small>本地量化研究台</small>
        </span>
      </RouterLink>

      <nav class="route-links" aria-label="主导航">
        <RouterLink to="/">
          <DashboardIcon size="20px" />
          <span><strong>选股工作台</strong><small>策略、候选与研究</small></span>
        </RouterLink>
        <RouterLink to="/backtest">
          <ChartLineIcon size="20px" />
          <span><strong>策略回测</strong><small>历史信号与收益</small></span>
        </RouterLink>
      </nav>

      <div class="sidebar-storage">
        <DataBaseIcon size="18px" />
        <span><strong>SQLite 本地存储</strong><small>行情与任务可复用</small></span>
      </div>
    </aside>

    <section class="app-viewport">
      <header class="mobile-nav">
        <RouterLink class="brand-link" to="/">
          <span class="brand-mark">OS</span>
          <span class="brand-copy"><strong>oversell</strong></span>
        </RouterLink>
        <nav aria-label="移动端主导航">
          <RouterLink to="/">选股</RouterLink>
          <RouterLink to="/backtest">回测</RouterLink>
        </nav>
      </header>
      <RouterView />
    </section>
  </div>
</template>
