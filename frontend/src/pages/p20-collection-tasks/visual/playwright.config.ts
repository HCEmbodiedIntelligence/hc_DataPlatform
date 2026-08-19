import { defineConfig } from "@playwright/test";

process.env.NO_PROXY = [process.env.NO_PROXY, "127.0.0.1", "localhost"]
  .filter(Boolean)
  .join(",");
process.env.no_proxy = process.env.NO_PROXY;

export default defineConfig({
  testDir: ".",
  testMatch: "**/*.pw.ts",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: "http://127.0.0.1:5195",
    browserName: "chromium",
    colorScheme: "light",
    locale: "zh-CN",
    timezoneId: "Asia/Shanghai",
  },
  webServer: {
    command:
      "VITE_API_BASE_URL=/api/v1 VITE_SSE_BASE_URL=/api/v1 VITE_MOCK_MODE=off VITE_BUILD_VERSION=e04-visual VITE_RELEASE_ENV=test pnpm dev --host 127.0.0.1 --port 5195",
    url: "http://127.0.0.1:5195/collection-tasks",
    reuseExistingServer: false,
    timeout: 30_000,
  },
  outputDir: "../../../../../artifacts/test-gates/latest/p20-visual",
});
