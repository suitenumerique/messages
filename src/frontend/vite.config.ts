import { defineConfig, type Plugin } from 'vite';
import react from '@vitejs/plugin-react';
import { tanstackRouter } from '@tanstack/router-plugin/vite';
import { visualizer } from 'rollup-plugin-visualizer';
import path from 'node:path';

export default defineConfig({
  plugins: [
    // See ./tsr.config.json for tanstackRouter config
    tanstackRouter(),
    react(),
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
  },
});
