import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError, apiFetch, csrfMiddleware, readCsrfToken } from './http';

function lastInit(fetchMock: ReturnType<typeof vi.fn>): RequestInit {
  return fetchMock.mock.calls.at(-1)![1] as RequestInit;
}

describe('apiFetch', () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    document.cookie = 'csrf_token=abc123; path=/';
    fetchMock = vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    document.cookie = 'csrf_token=; path=/; max-age=0';
    vi.unstubAllGlobals();
  });

  it('adds X-CSRF-Token from the cookie on POST and includes credentials', async () => {
    await apiFetch('/api/v1/things', { method: 'POST', body: '{}' });
    const init = lastInit(fetchMock);
    expect(new Headers(init.headers).get('X-CSRF-Token')).toBe('abc123');
    expect(init.credentials).toBe('include');
  });

  it.each(['PUT', 'PATCH', 'DELETE'])('adds the token on %s', async (method) => {
    await apiFetch('/api/v1/things/1', { method });
    expect(new Headers(lastInit(fetchMock).headers).get('X-CSRF-Token')).toBe('abc123');
  });

  it('does not add the token on GET', async () => {
    await apiFetch('/api/v1/auth/session');
    expect(new Headers(lastInit(fetchMock).headers).has('X-CSRF-Token')).toBe(false);
  });

  it('maps the error envelope to ApiError', async () => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ error: { code: 'forbidden', message: 'Nope.' } }), {
        status: 403,
      }),
    );
    const err = await apiFetch('/api/v1/x', { method: 'POST' }).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: 403, code: 'forbidden', message: 'Nope.' });
  });

  it('falls back to a generic ApiError for non-JSON error bodies', async () => {
    fetchMock.mockResolvedValue(new Response('<html>bad gateway</html>', { status: 502 }));
    await expect(apiFetch('/api/v1/x')).rejects.toMatchObject({
      status: 502,
      code: 'unknown_error',
    });
  });
});

describe('readCsrfToken', () => {
  afterEach(() => {
    document.cookie = 'csrf_token=; path=/; max-age=0';
  });

  it('returns the raw cookie value without decoding, even when it looks malformed', () => {
    document.cookie = 'csrf_token=%E0%A4%A; path=/';
    expect(() => readCsrfToken()).not.toThrow();
    expect(readCsrfToken()).toBe('%E0%A4%A');
  });

  it('returns null when there is no cookie', () => {
    expect(readCsrfToken()).toBeNull();
  });
});

describe('apiFetch with a malformed CSRF cookie', () => {
  it('still reaches fetch', async () => {
    document.cookie = 'csrf_token=%E0%A4%A; path=/';
    const fetchMock = vi.fn(async () => new Response('{}', { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    await apiFetch('/api/v1/x', { method: 'POST', body: '{}' });
    expect(fetchMock).toHaveBeenCalledOnce();
    vi.unstubAllGlobals();
    document.cookie = 'csrf_token=; path=/; max-age=0';
  });
});

describe('csrfMiddleware', () => {
  it('sets the header on unsafe requests only', async () => {
    document.cookie = 'csrf_token=tok; path=/';
    const run = async (method: string) => {
      const request = new Request('http://localhost/api/v1/x', { method });
      await csrfMiddleware.onRequest!({ request } as never);
      return request.headers.get('X-CSRF-Token');
    };
    expect(await run('POST')).toBe('tok');
    expect(await run('GET')).toBeNull();
    document.cookie = 'csrf_token=; path=/; max-age=0';
  });
});
