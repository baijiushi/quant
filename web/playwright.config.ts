import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  timeout: 60_000,
  expect: {
    timeout: 10_000
  },
  use: {
    baseURL: "http://127.0.0.1:18080",
    trace: "on-first-retry",
    screenshot: "only-on-failure"
  },
  webServer: {
    command: "cmd /c \"set PYTHONUTF8=1&& python -m uvicorn backend.app:app --host 127.0.0.1 --port 18080\"",
    cwd: "..",
    url: "http://127.0.0.1:18080/api/health",
    reuseExistingServer: false,
    timeout: 120_000
  },
  projects: [
    {
      name: "msedge",
      use: {
        ...devices["Desktop Edge"],
        channel: "msedge"
      }
    }
  ]
});
