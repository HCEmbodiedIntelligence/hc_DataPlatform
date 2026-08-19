import { defineConfig } from "@playwright/test";

const mockMode =
  process.env.HC_REAL_API_E2E_RUN_OWNER === "fe13" ||
  process.env.HC_REAL_API_E2E_RUN_OWNER === "fe14"
    ? "off"
    : "browser";

process.env.NO_PROXY = [process.env.NO_PROXY, "127.0.0.1", "localhost"]
  .filter(Boolean)
  .join(",");
process.env.no_proxy = process.env.NO_PROXY;

export default defineConfig({
  testDir: ".",
  testMatch: "AnnotationWorkbenchVisual.pw.ts",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: "http://127.0.0.1:5192",
    browserName: "chromium",
    colorScheme: "light",
    locale: "zh-CN",
    timezoneId: "Asia/Shanghai",
  },
  webServer: {
    command: `VITE_API_BASE_URL=/api/v1 VITE_SSE_BASE_URL=/api/v1 VITE_MOCK_MODE=${mockMode} VITE_BUILD_VERSION=e07-e08-visual VITE_RELEASE_ENV=local pnpm dev --host 127.0.0.1 --port 5192`,
    url: "http://127.0.0.1:5192",
    reuseExistingServer: false,
    timeout: 30_000,
  },
  outputDir: "../../../../../artifacts/test-gates/latest/e07-e08-annotation",
});
