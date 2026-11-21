import type { WorkboxPlugin } from 'workbox-core';

/** Sent by the API client so the service worker can key its offline cache per profile. */
export const PROFILE_HEADER = 'X-Date-Profile';

/** Runtime cache the service worker (src/sw.ts) keeps for the offline views. */
export const OFFLINE_API_CACHE = 'api-offline';

/**
 * Drops the offline API cache. The page and the service worker share CacheStorage, so no message
 * to the worker is needed. Best effort: without the Cache API (or if it fails) this is a no-op.
 */
export async function clearOfflineApiCache(): Promise<void> {
  try {
    if (typeof caches === 'undefined') return;
    await caches.delete(OFFLINE_API_CACHE);
  } catch {
    // Nothing to clear, or storage is blocked; entries are keyed per profile regardless.
  }
}

const SESSION_PATH = '/api/v1/auth/session';
const OFFLINE_API_PATHS = /^\/api\/v1\/(calendar|itineraries)(\/|$)/;

/** The few same-origin API reads the offline views need. Nothing else under /api/ is cached. */
export function isOfflineApi(url: URL, origin: string): boolean {
  return (
    url.origin === origin && (url.pathname === SESSION_PATH || OFFLINE_API_PATHS.test(url.pathname))
  );
}

/** Cache key for a request made while `profile` was active; one profile's entries can't match another's. */
export function profileCacheKey(url: string, profile: string | null): string {
  return `${url}${url.includes('?') ? '&' : '?'}__profile=${encodeURIComponent(profile ?? '')}`;
}

/**
 * Whether a response may be stored. Requires a profile header, a plain 200 from this origin and,
 * for the session itself, a body whose user is the profile the request was made for (a stale tab
 * can send an outdated header against a newer cookie).
 */
export async function shouldCacheResponse(
  response: Response,
  profile: string | null,
  pathname: string,
): Promise<boolean> {
  if (!profile) return false;
  if (response.status !== 200 || (response.type !== 'basic' && response.type !== 'cors')) {
    return false;
  }
  if (pathname !== SESSION_PATH) return true;
  try {
    const body = (await response.clone().json()) as { user?: { id?: unknown } | null } | null;
    return body?.user?.id === profile;
  } catch {
    return false;
  }
}

/**
 * NetworkFirst plugin for the offline API cache: entries are keyed by the request's profile header,
 * so a response (even a late one) is only ever stored and found under the profile that asked for it.
 */
export const profileCachePlugin: WorkboxPlugin = {
  cacheKeyWillBeUsed: ({ request }) =>
    Promise.resolve(profileCacheKey(request.url, request.headers.get(PROFILE_HEADER))),
  cacheWillUpdate: async ({ request, response }) =>
    (await shouldCacheResponse(
      response,
      request.headers.get(PROFILE_HEADER),
      new URL(request.url).pathname,
    ))
      ? response
      : null,
};
