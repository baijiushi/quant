import { createApp } from "vue";
import RouterShell from "./RouterShell.vue";
import router from "./router";
import "./styles.css";

createApp(RouterShell).use(router).mount("#app");
