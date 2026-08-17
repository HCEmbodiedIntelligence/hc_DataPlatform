import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

const devProxyTarget = process.env.VITE_DEV_PROXY_TARGET;

export default defineConfig({
  plugins: [react()],
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
          if (id.includes('/echarts/')) return 'analytics';
          if (id.includes('/three/') || id.includes('/urdf-loader/')) return 'viewer';
          return undefined;
        },
      },
    },
  },
});
