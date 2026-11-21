import { describe, expect, it } from 'vitest';
import {
  isOfflineApi,
  profileCacheKey,
  profileCachePlugin,
  shouldCacheResponse,
} from './offlineApi';

const ORIGIN = 'https://app.example.com';
const match = (path: string, origin = ORIGIN) => isOfflineApi(new URL(path, origin), ORIGIN);

describe('isOfflineApi', () => {
  it.each([
    '/api/v1/auth/session',
    '/api/v1/calendar',
    '/api/v1/calendar/x',
    '/api/v1/calendar?month=2026-01',
    '/api/v1/itineraries/123',
  ])('matches %s', (path) => {
    expect(match(path)).toBe(true);
  });

  it.each([
    '/api/v1/auth/session-anything',
    '/api/v1/auth/session/x',
    '/api/v1/calendar-private',
    '/api/v1/itineraries-secret/1',
    '/api/v1/couple',
    '/api/v1/auth/login',
  ])('does not match %s', (path) => {
    expect(match(path)).toBe(false);
  });

  it('does not match another origin', () => {
    expect(match('https://evil.com/api/v1/calendar', 'https://evil.com')).toBe(false);
  });
});

describe('profileCacheKey', () => {
  it('appends the profile to the url', () => {
    expect(profileCacheKey('https://a/x', 'u1')).toBe('https://a/x?__profile=u1');
    expect(profileCacheKey('https://a/x?m=1', 'u 1')).toBe('https://a/x?m=1&__profile=u%201');
  });

  it('gives different profiles different keys', () => {
    expect(profileCacheKey('https://a/x', 'u1')).not.toBe(profileCacheKey('https://a/x', 'u2'));
  });
});

/** A constructed Response has type "default"; a same-origin fetch in the worker yields "basic". */
function basic(body: string, status = 200): Response {
  const res = new Response(body, { status });
  Object.defineProperty(res, 'type', { value: 'basic' });
  return res;
}

describe('shouldCacheResponse', () => {
  const ok = () => basic('{}');

  it('refuses without a profile header', async () => {
    expect(await shouldCacheResponse(ok(), null, '/api/v1/calendar')).toBe(false);
  });

  it('refuses non-200 responses', async () => {
    const res = basic('{}', 500);
    expect(await shouldCacheResponse(res, 'u1', '/api/v1/calendar')).toBe(false);
  });

  it('refuses opaque responses', async () => {
    const res = new Response('{}', { status: 200 });
    Object.defineProperty(res, 'type', { value: 'opaque' });
    expect(await shouldCacheResponse(res, 'u1', '/api/v1/calendar')).toBe(false);
  });

  it('accepts a 200 same-origin response', async () => {
    expect(await shouldCacheResponse(ok(), 'u1', '/api/v1/calendar')).toBe(true);
  });

  it('accepts the session only when its user matches the header profile', async () => {
    const body = JSON.stringify({ authenticated: true, user: { id: 'u1' } });
    const make = () => basic(body);
    expect(await shouldCacheResponse(make(), 'u1', '/api/v1/auth/session')).toBe(true);
    expect(await shouldCacheResponse(make(), 'u2', '/api/v1/auth/session')).toBe(false);
    const anon = basic(JSON.stringify({ authenticated: false, user: null }));
    expect(await shouldCacheResponse(anon, 'u1', '/api/v1/auth/session')).toBe(false);
  });
});

describe('profileCachePlugin', () => {
  const req = (path: string, profile?: string) =>
    new Request(`${ORIGIN}${path}`, profile ? { headers: { 'X-Date-Profile': profile } } : {});
  const keyFor = async (request: Request) =>
    String(await profileCachePlugin.cacheKeyWillBeUsed!({ request, mode: 'write' } as never));
  const update = (request: Request, response: Response) =>
    profileCachePlugin.cacheWillUpdate!({ request, response } as never);

  it('keys the same url differently for different profiles', async () => {
    const u1 = await keyFor(req('/api/v1/calendar', 'u1'));
    const u2 = await keyFor(req('/api/v1/calendar', 'u2'));
    expect(u1).not.toBe(u2);
    expect(u1).toContain('__profile=u1');
  });

  it('never stores a response for a request without the header', async () => {
    expect(await update(req('/api/v1/calendar'), basic('{}'))).toBeNull();
  });

  it('stores a late old-profile response under the old profile key only', async () => {
    // u1 asked, the profile switched to u2, and the answer arrives afterwards.
    const late = req('/api/v1/calendar', 'u1');
    const response = basic('{"events":["u1 data"]}');
    expect(await update(late, response)).toBe(response);
    expect(await keyFor(late)).toBe(profileCacheKey(late.url, 'u1'));
    expect(await keyFor(late)).not.toBe(await keyFor(req('/api/v1/calendar', 'u2')));
  });

  it('does not store a session response whose user differs from the header', async () => {
    const body = JSON.stringify({ authenticated: true, user: { id: 'u2' } });
    expect(await update(req('/api/v1/auth/session', 'u1'), basic(body))).toBeNull();
    const same = basic(body);
    expect(await update(req('/api/v1/auth/session', 'u2'), same)).toBe(same);
  });
});
