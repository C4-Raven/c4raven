import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Emitted assets are grouped by kind under /assets/ (nginx caches that
// directory for a year: every file in it carries a content hash).
const ASSET_FOLDERS = [
  [/\.css$/i, 'css/'],
  [/\.(png|jpe?g|gif|svg|webp|avif|ico)$/i, 'images/'],
];

// `yarn dev` proxies the API to a running Raven backend; point it elsewhere
// with RAVEN_DEV_API=http://host:8080.
const DEV_API = process.env.RAVEN_DEV_API || 'http://127.0.0.1:8080';

function assetFolder(fileName = '') {
  const match = ASSET_FOLDERS.find(([pattern]) => pattern.test(fileName));
  return match ? match[1] : '';
}

export default defineConfig({
  plugins: [react()],
  resolve: {
    tsconfigPaths: true
  },
  server: {
    proxy: {
      '/api': {
        target: DEV_API,
        changeOrigin: true,
      },
      '/socket.io': {
        target: DEV_API,
        changeOrigin: true,
        ws: true,
      },
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: './vitest.setup.mjs',
    // Some page tests render a whole section and take ~3.5 s; 5 s flaked under load
    testTimeout: 15000,
  },
  build: {
    rollupOptions: {
      output: {
        entryFileNames: 'assets/js/[name]-[hash].js',
        chunkFileNames: 'assets/js/[name]-[hash].js',
        assetFileNames: (asset) => `assets/${assetFolder(asset.names?.[0] ?? asset.name)}[name]-[hash][extname]`,
      },
    },
  },
});
