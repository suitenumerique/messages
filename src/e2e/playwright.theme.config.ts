import { defineConfig, devices } from '@playwright/test';

// The runner sets CI to the string "false" outside CI, which is truthy.
const isCI = process.env.CI === 'true';

export default defineConfig({
  testDir: './src/__theme__',
  fullyParallel: false,
  forbidOnly: isCI,
  retries: isCI ? 2 : 0,
  workers: 1,
  reporter: isCI ? 'dot' : [['list'], ['html', { host: '0.0.0.0', port: 8932, outputFolder: './src/__theme__/playwright-report' }]],
  outputDir: './src/__theme__/test-results',
  use: {
    baseURL: process.env.KEYCLOAK_BASE_URL,
    // Keycloak reads Accept-Language, so the pages render in English unless a test asks for French.
    locale: 'en-US',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        // keycloak-js needs the Web Crypto API for PKCE, and Chromium exposes it
        // only in a secure context. The Keycloak origin of the stack is plain HTTP,
        // and only the full Chromium build honours the flag, not the headless shell.
        channel: 'chromium',
        launchOptions: {
          args: [`--unsafely-treat-insecure-origin-as-secure=${process.env.KEYCLOAK_BASE_URL}`],
        },
      },
    },
  ],
  webServer: undefined,
});
