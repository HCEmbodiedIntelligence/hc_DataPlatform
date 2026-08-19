import { defineConfig } from '@playwright/test';

// Keep the local visual server outside developer/CI outbound proxies.
process.env.NO_PROXY = [process.env.NO_PROXY, '127.0.0.1', 'localhost'].filter(Boolean).join(',');
process.env.no_proxy = process.env.NO_PROXY;

export default defineConfig({
  testDir: './e2e/storage-visual',
  testMatch: '**/*.pw.ts',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list']],
  expect: {
    toHaveScreenshot: { animations: 'disabled', maxDiffPixelRatio: 0.005 },
  },
  use: {
    baseURL: 'http://127.0.0.1:5187',
    browserName: 'chromium',
    viewport: { width: 1440, height: 1000 },
    colorScheme: 'light',
    locale: 'zh-CN',
    timezoneId: 'Asia/Shanghai',
  },
  webServer: {
    command: 'VITE_API_BASE_URL=/api/v1 VITE_SSE_BASE_URL=/api/v1 VITE_MOCK_MODE=browser VITE_BUILD_VERSION=storage-visual VITE_RELEASE_ENV=local pnpm dev --host 127.0.0.1 --port 5187',
    url: 'http://127.0.0.1:5187',
    reuseExistingServer: false,
    timeout: 30_000,
  },
  outputDir: '../artifacts/test-gates/latest/storage-visual',
});
