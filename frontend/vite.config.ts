import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

const devProxyTarget = process.env.VITE_DEV_PROXY_TARGET;

export default defineConfig({
  plugins: [react()],
  test: {
    include: ['src/**/*.test.{ts,tsx}'],
    exclude: [
      'src/**/*.pw.{ts,tsx}',
      'e2e/**/*.pw.{ts,tsx}',
      'e2e/**/*.spec.{ts,tsx}',
    ],
  },
  server: {
    host: '0.0.0.0',
    port: 5173,
    strictPort: true,
    ...(devProxyTarget
      ? {
          proxy: {
            '/api': {
              target: devProxyTarget,
              changeOrigin: true,
            },
          },
        }
      : {}),
  },
  build: {
    target: 'es2023',
    sourcemap: process.env.VITE_PUBLISH_SOURCEMAPS === 'true' ? 'hidden' : false,
    rollupOptions: {
      output: {
        manualChunks(id) {
          const normalized = id.replaceAll('\\', '/');
          if (!normalized.includes('/node_modules/')) return undefined;
          if (
            normalized.includes('/node_modules/react/') ||
            normalized.includes('/node_modules/react-dom/') ||
            normalized.includes('/node_modules/react-router/') ||
            normalized.includes('/node_modules/scheduler/')
          )
            return 'react-platform';
          if (normalized.includes('/node_modules/@tanstack/'))
            return 'query-platform';
          if (normalized.includes('/node_modules/zod/')) return 'contracts';
          return undefined;
        },
      },
    },
  },
});
