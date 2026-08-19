import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e/real-api",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 10 * 60 * 1_000,
  reporter: [
    ["list"],
    [
      "junit",
      { outputFile: "../artifacts/test-gates/latest/real-api-e2e.xml" },
    ],
    ["./e2e/real-api/fail-on-skipped-reporter.ts"],
  ],
  use: {
    baseURL: process.env.HC_REAL_API_E2E_BASE_URL ?? "http://127.0.0.1:8088",
    trace: "off",
    screenshot: "only-on-failure",
    video: "off",
  },
  outputDir: "../artifacts/test-gates/latest/playwright",
});
