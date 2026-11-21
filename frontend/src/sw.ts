/// <reference lib="webworker" />
import { clientsClaim } from 'workbox-core';
import { ExpirationPlugin } from 'workbox-expiration';
import { createHandlerBoundToURL, precacheAndRoute } from 'workbox-precaching';
import { NavigationRoute, registerRoute } from 'workbox-routing';
import { NetworkFirst } from 'workbox-strategies';
import { OFFLINE_API_CACHE, isOfflineApi, profileCachePlugin } from './lib/offlineApi';
import { safeUrl } from './lib/pushUrl';

declare const self: ServiceWorkerGlobalScope;

/** Runtime cache for the few API reads the offline views need (M3). Cleared on profile switch. */
const FALLBACK_TITLE = 'Date Planner 💌';

// Matches registerType 'autoUpdate': a new worker takes over without a prompt.
void self.skipWaiting();
clientsClaim();

precacheAndRoute(self.__WB_MANIFEST);

registerRoute(
  new NavigationRoute(createHandlerBoundToURL('index.html'), { denylist: [/^\/api\//] }),
);

// Nothing else under /api/ is cached. Entries are keyed by the X-Date-Profile request header, so a
// response fetched for one profile (even one still in flight when the profile switches) can only be
// stored and found under that profile's key; requests without the header are never stored.
registerRoute(
  ({ request, url, sameOrigin }) =>
    request.method === 'GET' && sameOrigin && isOfflineApi(url, self.location.origin),
  new NetworkFirst({
    cacheName: OFFLINE_API_CACHE,
    networkTimeoutSeconds: 3,
    plugins: [
      profileCachePlugin,
      new ExpirationPlugin({ maxEntries: 100, maxAgeSeconds: 7 * 24 * 60 * 60 }),
    ],
  }),
);

interface PushPayload {
  title?: unknown;
  body?: unknown;
  url?: unknown;
}

self.addEventListener('push', (event) => {
  let payload: PushPayload = {};
  try {
    const parsed: unknown = event.data?.json();
    if (parsed && typeof parsed === 'object') payload = parsed as PushPayload;
  } catch {
    // Not JSON: fall back to the generic title below.
  }
  const title = typeof payload.title === 'string' && payload.title ? payload.title : FALLBACK_TITLE;
  const body = typeof payload.body === 'string' ? payload.body : undefined;
  event.waitUntil(
    self.registration.showNotification(title, {
      body,
      icon: '/icon-192.png',
      badge: '/icon-192.png',
      data: { url: safeUrl(payload.url) },
    }),
  );
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const url = safeUrl((event.notification.data as { url?: unknown } | null)?.url);
  event.waitUntil(
    (async () => {
      const windows = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
      const existing = windows.find(
        (client) => new URL(client.url).origin === self.location.origin,
      );
      if (existing) {
        try {
          const navigated = await existing.navigate(url);
          await (navigated ?? existing).focus();
          return;
        } catch {
          // Fall through to opening a fresh window.
        }
      }
      await self.clients.openWindow(url);
    })(),
  );
});
