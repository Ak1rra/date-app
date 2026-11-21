import { useQuery } from '@tanstack/react-query';
import {
  apiFetch,
  currentProfileGeneration,
  getActiveProfile,
  setActiveProfile,
} from '../api/http';
import { clearOfflineApiCache } from '../lib/offlineApi';

export interface SessionUser {
  id: string;
  email: string;
  display_name: string | null;
  timezone: string;
  couple_id: string | null;
  role: string;
}

export interface Session {
  authenticated: boolean;
  user: SessionUser | null;
}

export const SESSION_KEY = ['session'] as const;

/**
 * GET /api/v1/auth/session. The active-profile header only follows the response if no profile
 * change began while it was in flight: apiFetch can't be relied on to abort, so a late answer for
 * the previous profile must not put its header back.
 */
export async function fetchSession(signal?: AbortSignal): Promise<Session> {
  const generation = currentProfileGeneration();
  const session = await apiFetch<Session>('/api/v1/auth/session', { signal });
  if (generation !== currentProfileGeneration()) return session;
  const userId = session.user?.id ?? null;
  // Logged out: nobody's cached data should outlive the session.
  if (!userId && getActiveProfile()) await clearOfflineApiCache();
  if (generation === currentProfileGeneration()) setActiveProfile(userId);
  return session;
}

/** Hook for the session. Also makes sure the csrf_token cookie exists before any write. */
export function useSession() {
  return useQuery({
    queryKey: SESSION_KEY,
    queryFn: ({ signal }) => fetchSession(signal),
    retry: false,
    staleTime: 60_000,
  });
}

/**
 * Who is using the app, for scoping per-viewer query keys: [root, userId, ...]. A different person
 * gets different cache entries, so one person's data can never render for another, whatever the
 * timing of a profile switch.
 */
export function useViewerId(): string | undefined {
  return useSession().data?.user?.id;
}
