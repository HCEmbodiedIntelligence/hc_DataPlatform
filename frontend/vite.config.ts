import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  build: {
    target: 'es2023',
    sourcemap: true,
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
