import { createRouter, createWebHistory } from "vue-router";

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", name: "selection", component: () => import("../App.vue") },
    { path: "/backtest", name: "backtest", component: () => import("../views/BacktestView.vue") },
  ],
  scrollBehavior: () => ({ top: 0 }),
});

export default router;
