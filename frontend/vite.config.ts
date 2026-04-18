import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  base: '/parser3/',
  server: {
    port: 5173,
    proxy: {
      '/parser3/api': 'http://localhost:8000',
      '/parser3/ws': { target: 'ws://localhost:8000', ws: true },
    },
  },
  build: {
    outDir: 'dist',
  },
});
