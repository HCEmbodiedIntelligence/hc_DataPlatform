import { defineConfig } from "@playwright/test";

process.env.NO_PROXY = [process.env.NO_PROXY, "127.0.0.1", "localhost"]
  .filter(Boolean)
  .join(",");
process.env.no_proxy = process.env.NO_PROXY;

export default defineConfig({
  testDir: ".",
  testMatch: "UploadVisual.pw.ts",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: "http://127.0.0.1:5193",
    browserName: "chromium",
    colorScheme: "light",
    locale: "zh-CN",
    timezoneId: "Asia/Shanghai",
  },
  webServer: {
    command:
      "VITE_API_BASE_URL=/api/v1 VITE_SSE_BASE_URL=/api/v1 VITE_MOCK_MODE=off VITE_BUILD_VERSION=e05-visual VITE_RELEASE_ENV=test VITE_E02_VISUAL_FIXTURE=loading pnpm dev --host 127.0.0.1 --port 5193",
    url: "http://127.0.0.1:5193",
    reuseExistingServer: false,
    timeout: 30_000,
  },
  outputDir: "../../../../../artifacts/test-gates/latest/e05-upload",
});
