import { useQuery } from '@tanstack/react-query';
import { apiFetch } from '../api/http';

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

/** GET /api/v1/auth/session. Also makes sure the csrf_token cookie exists before any write. */
export function useSession() {
  return useQuery({
    queryKey: SESSION_KEY,
    queryFn: () => apiFetch<Session>('/api/v1/auth/session'),
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
