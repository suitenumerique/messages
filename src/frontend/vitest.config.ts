import { defineConfig, mergeConfig } from 'vitest/config';

import viteConfig from './vite.config';

export default mergeConfig(
  // Inherit the app's build config — `define`, `resolve.alias`, `envPrefix` —
  // so tests compile modules exactly like the app does and can't drift from it.
  // Plugins are dropped rather than merged: the router codegen, the legacy
  // polyfill pass and the bundle analyzer are build-time concerns that only
  // slow the runner down. `mergeConfig` concatenates arrays, so the reset has
  // to happen before the merge, not in the override below.
  { ...viteConfig, plugins: [] },
  defineConfig({
    // Pin the build stamp: vite.config.ts derives it from the git HEAD (or a
    // timestamp when .git is absent, as in the test container), which would
    // make every run compile to different output for no benefit.
    define: {
      __SOURCE_VERSION__: JSON.stringify('test'),
      // Pinned for the same reason, and so the version assertions do not have
      // to be rewritten every time package.json is bumped.
      __WEB_APP_VERSION__: JSON.stringify('0.0.0-test'),
    },
    test: {
      globals: true,
      environment: 'jsdom',
      setupFiles: ['./vitest.setup.ts'],
      include: ['**/*.test.{ts,tsx}'],
      coverage: {
        provider: 'v8',
        reporter: ['text', 'json', 'html'],
        reportsDirectory: '.coverage',
      },
    },
  }),
);
