import createClient from 'openapi-fetch';
import { ApiError, csrfMiddleware } from './http';
import type { components, paths } from './schema';

export type Schemas = components['schemas'];

/**
 * Typed API client. The generated paths already carry the `/api/v1` prefix, so the base URL is
 * just this origin (an absolute URL also keeps `Request` happy outside a browser, e.g. in tests).
 * `fetch` is looked up per call so tests can stub it.
 */
export const client = createClient<paths>({
  baseUrl: typeof location === 'undefined' ? '' : location.origin,
  credentials: 'include',
  fetch: (request) => globalThis.fetch(request),
});
client.use(csrfMiddleware);

function errorFrom(response: Response, body: unknown): ApiError {
  let code = 'unknown_error';
  let message = response.statusText || 'Something went wrong';
  const envelope = (
    body as { error?: { code?: unknown; message?: unknown; reset_at?: unknown } } | null
  )?.error;
  if (typeof envelope?.code === 'string') code = envelope.code;
  if (typeof envelope?.message === 'string') message = envelope.message;
  const resetAt = typeof envelope?.reset_at === 'string' ? envelope.reset_at : null;
  return new ApiError(response.status, code, message, resetAt);
}

/** Returns the data of an openapi-fetch result, or throws an ApiError built from the error envelope. */
export function unwrap<T>(result: { data?: T; error?: unknown; response: Response }): T {
  if (result.error !== undefined || !result.response.ok) {
    throw errorFrom(result.response, result.error);
  }
  return result.data as T;
}
