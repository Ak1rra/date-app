import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query';
import { client, unwrap, type Schemas } from '../api/client';
import { ApiError } from '../api/http';
import { setSetupSkipped } from './setupSkipped';
import { SESSION_KEY, useViewerId } from './useSession';

export type CoupleOut = Schemas['CoupleOut'];
export type CouplePatch = Schemas['CouplePatch'];
export type MePatch = Schemas['MePatch'];

const coupleKey = (userId: string | undefined) => ['couple', userId] as const;

const fetchCouple = async () => unwrap(await client.GET('/api/v1/couple'));

/** One-off GET /couple through the cache, for callers outside a component's render (route choices). */
export const loadCouple = (queryClient: QueryClient, userId: string) =>
  queryClient.fetchQuery({ queryKey: coupleKey(userId), queryFn: fetchCouple });

/** GET /couple */
export function useCouple() {
  const userId = useViewerId();
  return useQuery({ queryKey: coupleKey(userId), queryFn: fetchCouple, enabled: !!userId });
}

/** PATCH /couple (name, timezone) */
export function usePatchCouple() {
  const userId = useViewerId();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: CouplePatch) => unwrap(await client.PATCH('/api/v1/couple', { body })),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: coupleKey(userId) }),
  });
}

/** PUT /couple/home-area. A 503 `provider_unavailable` means lookup is off; callers offer a skip. */
export function useSetHomeArea() {
  const userId = useViewerId();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (query: string) =>
      unwrap(await client.PUT('/api/v1/couple/home-area', { body: { query } })),
    onSuccess: () => {
      setSetupSkipped(false);
      return queryClient.invalidateQueries({ queryKey: coupleKey(userId) });
    },
  });
}

/** True when the geocoding provider is off (503 provider_unavailable). */
export const isProviderUnavailable = (error: unknown) =>
  error instanceof ApiError && error.status === 503 && error.code === 'provider_unavailable';

/** PATCH /me (display_name, timezone); refreshes the session so route guards see the new name. */
export function usePatchMe() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: MePatch) => unwrap(await client.PATCH('/api/v1/me', { body })),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: SESSION_KEY }),
  });
}
