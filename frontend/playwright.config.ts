import { defineConfig, devices } from '@playwright/test';

// Run through `bash scripts/e2e.sh`, which starts a throwaway server on the Linux box and tunnels
// it to E2E_BASE_URL. Pointing E2E_BASE_URL at any other DEMO_MODE + NO_LOGIN server also works.
export default defineConfig({
  testDir: './e2e',
  testMatch: '**/*.e2e.ts',
  outputDir: './e2e-results',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 60_000,
  reporter: 'list',
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:18090',
    // Fixed, so the datetime-local values the tests type and the server's idea of "now" agree.
    timezoneId: 'America/Chicago',
    locale: 'en-US',
    trace: 'on-first-retry',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
});
