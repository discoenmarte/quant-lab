import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

/** Dev server on :5173 proxies API calls to the Lab API on :8765. */
const api = 'http://127.0.0.1:8765';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { '/api': api, '/docs': api, '/openapi.json': api },
  },
  build: { outDir: 'dist', chunkSizeWarningLimit: 2500 },
});
