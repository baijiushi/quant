<script setup lang="ts">
import { RouterLink, RouterView } from "vue-router";
</script>

<template>
  <nav class="global-nav" aria-label="主导航">
    <RouterLink class="brand-link" to="/">
      <span class="brand-mark">OS</span>
      <span>oversell</span>
    </RouterLink>
    <div class="route-links">
      <RouterLink to="/">选股控制台</RouterLink>
      <RouterLink to="/backtest">策略回测</RouterLink>
    </div>
  </nav>
  <RouterView />
</template>
