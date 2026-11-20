import type { Middleware } from 'openapi-fetch';

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
  }
}

const UNSAFE_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);
export const CSRF_COOKIE = 'csrf_token';
export const CSRF_HEADER = 'X-CSRF-Token';

export function readCsrfToken(): string | null {
  for (const part of document.cookie.split(';')) {
    const [name, ...rest] = part.trim().split('=');
    // Raw value: the server issues url-safe tokens, so no decoding (which could throw).
    if (name === CSRF_COOKIE) return rest.join('=');
  }
  return null;
}

function addCsrf(headers: Headers, method: string): void {
  if (!UNSAFE_METHODS.has(method.toUpperCase())) return;
  const token = readCsrfToken();
  if (token) headers.set(CSRF_HEADER, token);
}

/** For `client.use()` on an openapi-fetch client (the typed client arrives with the API schema). */
export const csrfMiddleware: Middleware = {
  onRequest({ request }) {
    addCsrf(request.headers, request.method);
    return request;
  },
};

/** Turns a non-2xx response into an ApiError using the `{"error":{code,message}}` envelope. */
export async function toApiError(res: Response): Promise<ApiError> {
  let code = 'unknown_error';
  let message = res.statusText || 'Something went wrong';
  try {
    const body: unknown = await res.json();
    const err = (body as { error?: { code?: unknown; message?: unknown } } | null)?.error;
    if (typeof err?.code === 'string') code = err.code;
    if (typeof err?.message === 'string') message = err.message;
  } catch {
    // Non-JSON error body: keep the defaults.
  }
  return new ApiError(res.status, code, message);
}

/** fetch with cookies and CSRF handling; resolves to the parsed JSON body (undefined for 204). */
export async function apiFetch<T = unknown>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  addCsrf(headers, init.method ?? 'GET');
  if (init.body !== undefined && init.body !== null && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  const res = await fetch(path, { ...init, headers, credentials: 'include' });
  if (!res.ok) throw await toApiError(res);
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}
