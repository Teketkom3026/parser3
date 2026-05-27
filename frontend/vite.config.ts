import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

const basePath = process.env.VITE_BASE_PATH ?? '/parser3';

export default defineConfig({
  plugins: [react()],
  base: `${basePath}/`,
  server: {
    port: 5173,
    proxy: {
      [`${basePath}/api`]: 'http://localhost:8000',
      [`${basePath}/ws`]: { target: 'ws://localhost:8000', ws: true },
    },
  },
  build: {
    outDir: 'dist',
  },
});
