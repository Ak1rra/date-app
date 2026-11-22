import type { Middleware } from 'openapi-fetch';
import { PROFILE_HEADER } from '../lib/offlineApi';

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  /** The `reset_at` the API adds to a 429 ai_cap_reached (ISO), when it sent one. */
  readonly resetAt: string | null;

  constructor(status: number, code: string, message: string, resetAt: string | null = null) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.resetAt = resetAt;
  }
}

const UNSAFE_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);
export const CSRF_COOKIE = 'csrf_token';
export const CSRF_HEADER = 'X-CSRF-Token';

const PROFILE_STORAGE_KEY = 'dateapp-active-profile';

function readStoredProfile(): string | null {
  try {
    return localStorage.getItem(PROFILE_STORAGE_KEY) || null;
  } catch {
    return null;
  }
}

// Remembered across reloads (an id isn't secret; this is a per-device convenience) so the first
// session request after a reload already carries the header and an offline reload can be served
// from that profile's cache. The server's session response still wins (see fetchSession).
let activeProfile: string | null = readStoredProfile();

export function getActiveProfile(): string | null {
  return activeProfile;
}

/** The signed-in profile's user id; sent as X-Date-Profile so the service worker keys its cache per profile. */
export function setActiveProfile(userId: string | null): void {
  activeProfile = userId;
  try {
    if (userId) localStorage.setItem(PROFILE_STORAGE_KEY, userId);
    else localStorage.removeItem(PROFILE_STORAGE_KEY);
  } catch {
    // Storage blocked or full: the in-memory value still works for this page load.
  }
}

let profileGeneration = 0;

/**
 * Marks that the active profile is about to change. A session response that was requested before
 * this (see currentProfileGeneration) is stale and must not overwrite the active profile.
 */
export function beginProfileChange(): void {
  profileGeneration += 1;
}

export function currentProfileGeneration(): number {
  return profileGeneration;
}

function addProfile(headers: Headers): void {
  if (activeProfile) headers.set(PROFILE_HEADER, activeProfile);
}

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
    addProfile(request.headers);
    return request;
  },
};

/** Turns a non-2xx response into an ApiError using the `{"error":{code,message}}` envelope. */
export async function toApiError(res: Response): Promise<ApiError> {
  let code = 'unknown_error';
  let message = res.statusText || 'Something went wrong';
  let resetAt: string | null = null;
  try {
    const body: unknown = await res.json();
    const err = (
      body as { error?: { code?: unknown; message?: unknown; reset_at?: unknown } } | null
    )?.error;
    if (typeof err?.code === 'string') code = err.code;
    if (typeof err?.message === 'string') message = err.message;
    if (typeof err?.reset_at === 'string') resetAt = err.reset_at;
  } catch {
    // Non-JSON error body: keep the defaults.
  }
  return new ApiError(res.status, code, message, resetAt);
}

/** fetch with cookies and CSRF handling; resolves to the parsed JSON body (undefined for 204). */
export async function apiFetch<T = unknown>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  addCsrf(headers, init.method ?? 'GET');
  addProfile(headers);
  if (init.body !== undefined && init.body !== null && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  const res = await fetch(path, { ...init, headers, credentials: 'include' });
  if (!res.ok) throw await toApiError(res);
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}
