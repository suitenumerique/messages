import { defineConfig, devices } from '@playwright/test';
import { getStorageStatePathIfExists } from './src/utils';

// The runner always receives CI, set to "false" outside of CI: a truthiness
// check would retry, and so hide, every flaky test run locally.
const isCI = process.env.CI === 'true';

/**
 * See https://playwright.dev/docs/test-configuration.
 */
export default defineConfig({
  testDir: './src/__tests__',

  /* Run tests in files in parallel */
  fullyParallel: false,

  /* Fail the build on CI if you accidentally left test.only in the source code. */
  forbidOnly: isCI,

  /* Retry on CI only */
  retries: isCI ? 2 : 0,

  /* Opt out of parallel tests on CI. */
  workers: 1,

  /* Reporter to use. See https://playwright.dev/docs/test-reporters */
  reporter: isCI ? 'dot' : [['list'], ['html', { host: '0.0.0.0', port: 8932, outputDir: './src/__tests__/playwright-report' }]],
  outputDir: './src/__tests__/playwright-report',

  /* Shared settings for all the projects below. See https://playwright.dev/docs/api/class-testoptions. */
  use: {
    /* Base URL to use in actions like `await page.goto('/')`. */
    baseURL: process.env.FRONTEND_BASE_URL,

    /* Collect trace when retrying the failed test. See https://playwright.dev/docs/trace-viewer */
    trace: 'on-first-retry',

    /* Screenshot on failure */
    screenshot: 'only-on-failure',

    /* Video on failure */
    video: 'retain-on-failure',
  },

  /* Configure projects for major browsers */
  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        storageState: getStorageStatePathIfExists('user.e2e.chromium'),
      },
    },
    {
      name: 'firefox',
      use: { ...devices['Desktop Firefox'],
        storageState: getStorageStatePathIfExists('user.e2e.firefox'),
      },
    },
    {
      name: 'webkit',
      use: { ...devices['Desktop Safari'],
        storageState: getStorageStatePathIfExists('user.e2e.webkit'),
      },
    },
  ],

  /* Run your local dev server before starting the tests */
  webServer: process.env.SKIP_WEBSERVER ? undefined : {
    command: 'echo "Waiting for services to be ready..."',
    url: process.env.FRONTEND_BASE_URL,
    reuseExistingServer: true,
    timeout: 120 * 1000,
  },
});

