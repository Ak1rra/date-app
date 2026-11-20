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
