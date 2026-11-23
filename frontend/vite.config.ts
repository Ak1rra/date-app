/// <reference types="vitest/config" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { VitePWA } from 'vite-plugin-pwa';

// Colours mirror --canvas / --accent in src/styles.css (light theme).
const THEME_COLOR = '#FFF4EE';

export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    VitePWA({
      // 'autoUpdate': the app holds no unsaved offline state in M1, so silently
      // taking the new service worker on next load is simpler and safer than
      // an update prompt people may ignore for weeks.
      registerType: 'autoUpdate',
      // Custom worker (src/sw.ts): offline API cache, push display, notification clicks.
      strategies: 'injectManifest',
      srcDir: 'src',
      filename: 'sw.ts',
      includeAssets: ['icon.svg', 'favicon-32.png', 'apple-touch-icon.png'],
      manifest: {
        name: 'Date Planner',
        short_name: 'Dates',
        description: 'Plan thoughtful dates for the person you love.',
        theme_color: THEME_COLOR,
        background_color: THEME_COLOR,
        display: 'standalone',
        start_url: '/',
        scope: '/',
        icons: [
          { src: 'icon-192.png', sizes: '192x192', type: 'image/png' },
          { src: 'icon-512.png', sizes: '512x512', type: 'image/png' },
          {
            src: 'icon-maskable-512.png',
            sizes: '512x512',
            type: 'image/png',
            purpose: 'maskable',
          },
        ],
      },
    }),
  ],
  build: {
    // One ~580 kB (~170 kB gzip) bundle is fine here: the app is served over the
    // LAN and the service worker precaches it, so it downloads once per release.
    // Route-level code splitting isn't worth the churn; revisit past ~800 kB.
    chunkSizeWarningLimit: 800,
  },
  server: {
    proxy: {
      '/api': { target: 'http://localhost:8090', ws: true },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    css: false,
  },
});
