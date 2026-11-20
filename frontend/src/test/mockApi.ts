import { vi } from 'vitest';
import { jsonResponse } from './renderApp';

export interface Call {
  method: string;
  path: string;
  body: unknown;
}

type Reply = unknown | Response;
/** A fixed body, a Response, or a function of the call (may return a Promise to hold the reply). */
export type Handler = Reply | ((call: Call) => Reply);

/**
 * Stubs fetch with routes keyed "METHOD /path" (query strings ignored). Works for both the typed
 * client (which passes a Request) and apiFetch (a URL string). Unmatched calls get a 404 envelope.
 */
export function mockApi(routes: Record<string, Handler>) {
  const calls: Call[] = [];
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const request = input instanceof Request ? input : null;
    const url = request ? request.url : String(input);
    const method = (request?.method ?? init?.method ?? 'GET').toUpperCase();
    const text = request ? await request.clone().text() : String(init?.body ?? '');
    const call: Call = {
      method,
      path: new URL(url, 'http://localhost').pathname,
      body: text ? JSON.parse(text) : undefined,
    };
    calls.push(call);
    const handler = routes[`${method} ${call.path}`];
    if (handler === undefined) {
      return jsonResponse(
        { error: { code: 'not_found', message: 'No such route in the mock.' } },
        404,
      );
    }
    const reply =
      typeof handler === 'function' ? await (handler as (c: Call) => Reply)(call) : handler;
    return reply instanceof Response ? reply.clone() : jsonResponse(reply);
  });
  vi.stubGlobal('fetch', fetchMock);
  return {
    fetchMock,
    calls,
    find: (method: string, path: string) =>
      calls.filter((c) => c.method === method && c.path === path),
    /** Body of the first matching call; throws if there was none. */
    body: (method: string, path: string): unknown => {
      const call = calls.find((c) => c.method === method && c.path === path);
      if (!call) throw new Error(`No ${method} ${path} call was made`);
      return call.body;
    },
  };
}
