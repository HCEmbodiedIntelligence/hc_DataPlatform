import { defineConfig } from "@playwright/test";

process.env.NO_PROXY = [process.env.NO_PROXY, "127.0.0.1", "localhost"]
  .filter(Boolean)
  .join(",");
process.env.no_proxy = process.env.NO_PROXY;

export default defineConfig({
  testDir: "./e2e/auth-real-api",
  testMatch: "**/*.pw.ts",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: "http://127.0.0.1:5194",
    browserName: "chromium",
    viewport: { width: 1280, height: 800 },
    colorScheme: "light",
    locale: "zh-CN",
    timezoneId: "Asia/Shanghai",
  },
  webServer: [
    {
      command:
        "HC_ENVIRONMENT=test HC_RUNTIME_BACKEND=memory HC_API_HOST=127.0.0.1 HC_API_PORT=5193 .venv/bin/hc-data-api",
      cwd: "../backend",
      url: "http://127.0.0.1:5193/health/live",
      reuseExistingServer: false,
      timeout: 30_000,
    },
    {
      command:
        "VITE_API_BASE_URL=/api/v1 VITE_SSE_BASE_URL=/api/v1 VITE_MOCK_MODE=off VITE_BUILD_VERSION=auth-real-api VITE_RELEASE_ENV=test VITE_DEV_PROXY_TARGET=http://127.0.0.1:5193 pnpm dev --host 127.0.0.1 --port 5194",
      url: "http://127.0.0.1:5194/auth/login",
      reuseExistingServer: false,
      timeout: 30_000,
    },
  ],
  outputDir: "../artifacts/test-gates/latest/auth-real-api",
});
