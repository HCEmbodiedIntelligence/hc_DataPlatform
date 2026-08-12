import { defineConfig, devices } from '@playwright/test';

// Playwright's webServer readiness probe observes process proxy variables. Keep
// loopback traffic local so a configured HTTP proxy cannot return a false 400
// and make the runner skip starting Vite.
const loopbackNoProxy = '127.0.0.1,localhost';
process.env.NO_PROXY = [process.env.NO_PROXY, loopbackNoProxy].filter(Boolean).join(',');
process.env.no_proxy = [process.env.no_proxy, loopbackNoProxy].filter(Boolean).join(',');

const commonWebServerEnv = {
  VITE_API_BASE_URL: '/api/v1',
  VITE_SSE_BASE_URL: '/api/v1/events',
  VITE_BUILD_VERSION: 'web-e2e',
  VITE_RELEASE_ENV: 'test',
};

export default defineConfig({
  testDir: './tests/e2e',
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 2 : 0,
  outputDir: './node_modules/.cache/playwright-results',
  reporter: [['html', { outputFolder: './node_modules/.cache/playwright-report', open: 'never' }]],
  use: {
    baseURL: 'http://127.0.0.1:4173',
    trace: 'on-first-retry',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
    { name: 'mobile', use: { ...devices['iPhone 13'] } },
  ],
  webServer: [
    {
      command: './node_modules/.bin/vite --host 127.0.0.1 --port 4173',
      url: 'http://127.0.0.1:4173',
      reuseExistingServer: !process.env.CI,
      env: {
        ...commonWebServerEnv,
        VITE_MOCK_MODE: 'browser',
      },
    },
    {
      command: './node_modules/.bin/vite --host 127.0.0.1 --port 4174',
      url: 'http://127.0.0.1:4174',
      reuseExistingServer: !process.env.CI,
      env: {
        ...commonWebServerEnv,
        VITE_MOCK_MODE: 'off',
      },
    },
  ],
});
