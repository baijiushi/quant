import { createApp } from "vue";
import RouterShell from "./RouterShell.vue";
import router from "./router";
import "tdesign-vue-next/es/style/index.css";
import "@fontsource-variable/ibm-plex-sans";
import "@fontsource/ibm-plex-mono/400.css";
import "./styles.css";
import "./workbench.css";

createApp(RouterShell).use(router).mount("#app");
