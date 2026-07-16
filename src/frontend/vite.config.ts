import { defineConfig, type Plugin } from 'vite';
import react from '@vitejs/plugin-react';
import legacy from '@vitejs/plugin-legacy';
import { tanstackRouter } from '@tanstack/router-plugin/vite';
import { visualizer } from 'rollup-plugin-visualizer';
import path from 'node:path';

import pkg from './package.json';

// Single source of truth for supported browsers; drives which polyfills the
// legacy plugin injects (see the `legacy` plugin below).
const browserslist = pkg.browserslist;

export default defineConfig({
  plugins: [
    // See ./tsr.config.json for tanstackRouter config
    tanstackRouter(),
    react(),
    // Polyfill runtime APIs missing from our oldest supported browsers (see the
    // `browserslist` field in package.json — floor is Chromium 109 on Windows 7).
    // Those browsers still support ES modules, so they load the modern bundle;
    // we don't ship a separate nomodule bundle (renderLegacyChunks: false) and
    // only inject the missing polyfills into the modern build. `modernPolyfills`
    // is usage-driven: Babel derives the exact core-js modules from the code
    // (app + dependencies like BlockNote's `marks.toReversed()`), so future
    // ES20xx gaps are covered without hand-writing shims. modernTargets is
    // pinned to our browserslist so both the injected polyfills and esbuild's
    // syntax target track our declared 109 floor rather than the plugin's own
    // default baseline (chrome>=105).
    legacy({
      renderLegacyChunks: false,
      modernPolyfills: true,
      modernTargets: browserslist,
    }),
    // Opt-in bundle analyzer: emits bundle-stats.json next to the project
    // root when ANALYZE=1. Consumed by `npm run analyze` (see Makefile).
    process.env.ANALYZE === '1' &&
      (visualizer({
        filename: 'bundle-stats.json',
        template: 'raw-data',
        gzipSize: true,
      }) as Plugin),
  ].filter(Boolean) as Plugin[],
  server: {
    host: '0.0.0.0',
    port: 3000,
    strictPort: true,
  },
  preview: {
    host: '0.0.0.0',
    port: 3000,
    strictPort: true,
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  // Runtime configuration comes from the backend /config endpoint; the only
  // build-time env vars left are NEXT_PUBLIC_API_ORIGIN and the deprecated
  // NEXT_PUBLIC_* fallbacks (see features/config/resolve.ts). envPrefix tells
  // Vite which env vars to expose to client code at build time.
  envPrefix: 'NEXT_PUBLIC_',
  build: {
    outDir: 'dist',
    sourcemap: false,
    minify: 'oxc',
  },
});
