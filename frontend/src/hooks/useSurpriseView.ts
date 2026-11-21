import { useQuery } from '@tanstack/react-query';
import { client, unwrap, type Schemas } from '../api/client';
import { ApiError } from '../api/http';
import { useViewerId } from './useSession';

export type SurpriseOut = Schemas['SurpriseOut'];
export type ReleasedHintOut = Schemas['ReleasedHintOut'];

export const surprisesKey = (userId: string | undefined) => ['surprises', userId] as const;
export const surpriseKey = (userId: string | undefined, id: string | undefined) =>
  ['surprise', userId, id] as const;

const REFETCH_MS = 60_000;

const retryUnlessMissing = (count: number, error: unknown) =>
  !(error instanceof ApiError && error.status === 404) && count < 2;

/** The surprises the viewer is being surprised with (the surprisee's redacted view). */
export function useSurprises() {
  const userId = useViewerId();
  return useQuery({
    queryKey: surprisesKey(userId),
    enabled: !!userId,
    queryFn: async () => unwrap(await client.GET('/api/v1/surprises')),
    retry: retryUnlessMissing,
    refetchInterval: REFETCH_MS,
    refetchOnWindowFocus: true,
  });
}

export function useSurpriseView(id: string | undefined) {
  const userId = useViewerId();
  return useQuery({
    queryKey: surpriseKey(userId, id),
    enabled: !!userId && !!id,
    queryFn: async () =>
      unwrap(
        await client.GET('/api/v1/surprises/{itinerary_id}', {
          params: { path: { itinerary_id: id! } },
        }),
      ),
    retry: retryUnlessMissing,
    refetchInterval: REFETCH_MS,
    refetchOnWindowFocus: true,
  });
}
