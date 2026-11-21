import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { apiFetch, setActiveProfile } from '../api/http';
import { resetForProfile, useProfileSwitchSync } from './profileSwitch';
import { SESSION_KEY, fetchSession } from './useSession';
import type { Session } from './useSession';

const session: Session = {
  authenticated: true,
  user: {
    id: 'u2',
    email: 'wife@example.com',
    display_name: 'Alex',
    timezone: 'UTC',
    couple_id: 'c1',
    role: 'partner',
  },
};

/** resetForProfile awaits a session refetch, so the client needs a session query that resolves. */
function sessionClient() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  queryClient.setQueryDefaults(SESSION_KEY, { queryFn: ({ signal }) => fetchSession(signal) });
  return queryClient;
}

const sessionResponse = () => new Response(JSON.stringify(session), { status: 200 });

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  setActiveProfile(null);
  Reflect.deleteProperty(window, 'caches');
});

describe('resetForProfile offline cache', () => {
  beforeEach(() => {
    vi.stubGlobal(
      'fetch',
      vi.fn<typeof fetch>(async () => sessionResponse()),
    );
  });

  it('deletes the api-offline cache when the profile changes', async () => {
    const remove = vi.fn().mockResolvedValue(true);
    Object.defineProperty(window, 'caches', { value: { delete: remove }, configurable: true });

    await resetForProfile(sessionClient(), session);

    expect(remove).toHaveBeenCalledWith('api-offline');
  });

  it('still switches when the cache API throws or rejects', async () => {
    const queryClient = sessionClient();
    Object.defineProperty(window, 'caches', {
      value: { delete: () => Promise.reject(new Error('nope')) },
      configurable: true,
    });
    await expect(resetForProfile(queryClient, session)).resolves.toBeUndefined();

    Object.defineProperty(window, 'caches', {
      value: {
        delete: () => {
          throw new Error('boom');
        },
      },
      configurable: true,
    });
    await expect(resetForProfile(queryClient, session)).resolves.toBeUndefined();
    expect(queryClient.getQueryData(['session'])).toBeDefined();
  });

  it('works when the Cache API is unavailable', async () => {
    await expect(resetForProfile(sessionClient(), session)).resolves.toBeUndefined();
  });
});

describe('active profile header across a switch', () => {
  afterEach(() => {
    setActiveProfile(null);
    vi.unstubAllGlobals();
  });

  it('is not reverted by a session response that was in flight for the old profile', async () => {
    const u1Session: Session = { authenticated: true, user: { ...session.user!, id: 'u1' } };
    let resolveU1!: (res: Response) => void;
    const fetchMock = vi
      .fn()
      .mockImplementationOnce(() => new Promise<Response>((resolve) => (resolveU1 = resolve)))
      .mockImplementation(async () => sessionResponse());
    vi.stubGlobal('fetch', fetchMock);

    const pending = fetchSession();
    await resetForProfile(sessionClient(), session);
    resolveU1(new Response(JSON.stringify(u1Session), { status: 200 }));
    await pending;

    await apiFetch('/api/v1/calendar');
    const init = fetchMock.mock.calls.at(-1)![1] as RequestInit;
    expect(new Headers(init.headers).get('X-Date-Profile')).toBe('u2');
  });

  it('follows a session response when no switch happened meanwhile', async () => {
    const fetchMock = vi.fn<typeof fetch>(
      async () => new Response(JSON.stringify(session), { status: 200 }),
    );
    vi.stubGlobal('fetch', fetchMock);
    await fetchSession();
    await apiFetch('/api/v1/calendar');
    const init = fetchMock.mock.calls.at(-1)![1] as RequestInit;
    expect(new Headers(init.headers).get('X-Date-Profile')).toBe('u2');
  });
});

describe('offline bootstrap and cache clearing', () => {
  const KEY = 'dateapp-active-profile';

  afterEach(() => {
    setActiveProfile(null);
    vi.unstubAllGlobals();
    Reflect.deleteProperty(window, 'caches');
  });

  it('awaits the cache delete before the header changes, then refetches the session with it', async () => {
    setActiveProfile('u1');
    let finishDelete!: () => void;
    const remove = vi.fn(
      () => new Promise<boolean>((resolve) => (finishDelete = () => resolve(true))),
    );
    Object.defineProperty(window, 'caches', { value: { delete: remove }, configurable: true });
    const fetchMock = vi.fn<typeof fetch>(
      async () => new Response(JSON.stringify(session), { status: 200 }),
    );
    vi.stubGlobal('fetch', fetchMock);
    const queryClient = new QueryClient();
    queryClient.setQueryDefaults(SESSION_KEY, {
      queryFn: ({ signal }) => fetchSession(signal),
    });

    const done = resetForProfile(queryClient, session);
    await vi.waitFor(() => expect(remove).toHaveBeenCalledWith('api-offline'));
    expect(localStorage.getItem(KEY)).toBe('u1');
    finishDelete();
    await done;
    expect(localStorage.getItem(KEY)).toBe('u2');

    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const init = fetchMock.mock.calls.at(-1)![1] as RequestInit;
    expect(new Headers(init.headers).get('X-Date-Profile')).toBe('u2');
  });

  it('deletes api-offline when an anonymous session follows a signed-in one', async () => {
    setActiveProfile('u2');
    const remove = vi.fn().mockResolvedValue(true);
    Object.defineProperty(window, 'caches', { value: { delete: remove }, configurable: true });
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ authenticated: false, user: null }))),
    );
    await fetchSession();
    expect(remove).toHaveBeenCalledWith('api-offline');
    expect(localStorage.getItem(KEY)).toBeNull();
  });

  it('does not touch the cache for an anonymous session when nobody was signed in', async () => {
    const remove = vi.fn().mockResolvedValue(true);
    Object.defineProperty(window, 'caches', { value: { delete: remove }, configurable: true });
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ authenticated: false, user: null }))),
    );
    await fetchSession();
    expect(remove).not.toHaveBeenCalled();
  });

  it('keeps the shared stored profile when another tab announces a switch', async () => {
    const channels: { onmessage: ((e: MessageEvent) => void) | null }[] = [];
    class FakeChannel {
      onmessage: ((e: MessageEvent) => void) | null = null;
      constructor() {
        channels.push(this);
      }
      postMessage() {}
      close() {}
    }
    vi.stubGlobal('BroadcastChannel', FakeChannel);
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify(session))),
    );
    setActiveProfile('u1');
    // The initiating tab stored 'u2' before broadcasting.
    localStorage.setItem(KEY, 'u2');
    const queryClient = new QueryClient();
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );
    renderHook(() => useProfileSwitchSync('u1'), { wrapper });
    await waitFor(() => expect(channels.length).toBeGreaterThan(0));

    channels[0]!.onmessage!({ data: { type: 'profile-changed', userId: 'u2' } } as MessageEvent);
    expect(localStorage.getItem(KEY)).toBe('u2');
  });
});

describe('resetForProfile ordering', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('broadcasts only after the session refetch has resolved', async () => {
    const events: string[] = [];
    class FakeChannel {
      postMessage() {
        events.push('broadcast');
      }
      close() {}
    }
    vi.stubGlobal('BroadcastChannel', FakeChannel);
    let release!: () => void;
    vi.stubGlobal(
      'fetch',
      vi.fn<typeof fetch>(async () => {
        events.push('session request');
        await new Promise<void>((resolve) => (release = resolve));
        events.push('session response');
        return sessionResponse();
      }),
    );

    const done = resetForProfile(sessionClient(), session);
    await vi.waitFor(() => expect(events).toEqual(['session request']));
    release();
    await done;

    expect(events).toEqual(['session request', 'session response', 'broadcast']);
  });
});
