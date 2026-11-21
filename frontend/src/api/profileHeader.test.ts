import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { client } from './client';
import { apiFetch, setActiveProfile } from './http';

describe('X-Date-Profile header', () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn(async () => new Response('{}', { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    setActiveProfile(null);
    vi.unstubAllGlobals();
  });

  it('is sent by the typed client when a profile is active', async () => {
    setActiveProfile('u1');
    await client.GET('/api/v1/couple');
    const request = fetchMock.mock.calls.at(-1)![0] as Request;
    expect(request.headers.get('X-Date-Profile')).toBe('u1');
  });

  it('is not sent by the typed client when no profile is active', async () => {
    await client.GET('/api/v1/couple');
    const request = fetchMock.mock.calls.at(-1)![0] as Request;
    expect(request.headers.has('X-Date-Profile')).toBe(false);
  });

  it('is sent by apiFetch only when a profile is active', async () => {
    setActiveProfile('u2');
    await apiFetch('/api/v1/auth/session');
    const init = fetchMock.mock.calls.at(-1)![1] as RequestInit;
    expect(new Headers(init.headers).get('X-Date-Profile')).toBe('u2');
    setActiveProfile(null);
    await apiFetch('/api/v1/auth/session');
    const next = fetchMock.mock.calls.at(-1)![1] as RequestInit;
    expect(new Headers(next.headers).has('X-Date-Profile')).toBe(false);
  });
});

describe('remembered profile', () => {
  const KEY = 'dateapp-active-profile';

  beforeEach(() => {
    vi.resetModules();
    localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it('sends the stored profile on the first request after a reload', async () => {
    localStorage.setItem(KEY, 'u9');
    const fetchMock = vi.fn<typeof fetch>(async () => new Response('{}', { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    const http = await import('./http');
    await http.apiFetch('/api/v1/auth/session');
    const init = fetchMock.mock.calls.at(-1)![1] as RequestInit;
    expect(new Headers(init.headers).get('X-Date-Profile')).toBe('u9');
  });

  it('stores the profile and clears it again on null', async () => {
    const http = await import('./http');
    http.setActiveProfile('u3');
    expect(localStorage.getItem(KEY)).toBe('u3');
    http.setActiveProfile(null);
    expect(localStorage.getItem(KEY)).toBeNull();
  });

  it('keeps working when storage throws', async () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    const fetchMock = vi.fn<typeof fetch>(async () => new Response('{}', { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    const http = await import('./http');
    expect(() => http.setActiveProfile('u4')).not.toThrow();
    await http.apiFetch('/api/v1/x');
    const init = fetchMock.mock.calls.at(-1)![1] as RequestInit;
    expect(new Headers(init.headers).get('X-Date-Profile')).toBe('u4');
    expect(() => http.setActiveProfile(null)).not.toThrow();
  });
});
