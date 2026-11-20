import { useQuery } from '@tanstack/react-query';
import { apiFetch } from '../api/http';

export interface Meta {
  demo_mode: boolean;
  version: string;
  no_login: boolean;
}

export const META_KEY = ['meta'] as const;

/** GET /api/v1/meta. Server flags don't change while the app is open, so cache forever. */
export function useMeta() {
  return useQuery({
    queryKey: META_KEY,
    queryFn: () => apiFetch<Meta>('/api/v1/meta'),
    retry: false,
    staleTime: Infinity,
  });
}
