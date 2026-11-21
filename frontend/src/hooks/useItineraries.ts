import { useIsMutating, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { client, unwrap, type Schemas } from '../api/client';
import { ApiError } from '../api/http';
import { useViewerId } from './useSession';

export type ItineraryOut = Schemas['ItineraryOut'];
export type ItinerarySummary = Schemas['ItinerarySummary'];
export type ChecklistItemOut = Schemas['ChecklistItemOut'];
export type ChecklistIn = Schemas['ChecklistIn'];
export type ChecklistPatch = Schemas['ChecklistPatch'];
export type ReactIn = Schemas['ReactIn'];
export type ScheduleIn = Schemas['ScheduleIn'];
export type StopIn = Schemas['StopIn'];
export type ItineraryPatch = Schemas['ItineraryPatch'];
export type PlaceOut = Schemas['PlaceOut'];

export const itinerariesKey = (userId: string | undefined) => ['itineraries', userId] as const;
export const itineraryKey = (userId: string | undefined, id: string | undefined) =>
  ['itinerary', userId, id] as const;

export const fetchItinerary = async (id: string) =>
  unwrap(
    await client.GET('/api/v1/itineraries/{itinerary_id}', {
      params: { path: { itinerary_id: id } },
    }),
  );

export function itineraryQueryOptions(userId: string | undefined, id: string | undefined) {
  return {
    queryKey: itineraryKey(userId, id),
    enabled: !!userId && !!id,
    queryFn: () => fetchItinerary(id!),
    retry: (count: number, error: unknown) =>
      !(error instanceof ApiError && error.status === 404) && count < 2,
  };
}

export function useItineraries() {
  const userId = useViewerId();
  return useQuery({
    queryKey: itinerariesKey(userId),
    enabled: !!userId,
    queryFn: async () => unwrap(await client.GET('/api/v1/itineraries')),
  });
}

export function useItinerary(id: string | undefined) {
  const userId = useViewerId();
  return useQuery(itineraryQueryOptions(userId, id));
}

function invalidateItinerary(
  queryClient: ReturnType<typeof useQueryClient>,
  userId: string | undefined,
  id: string,
) {
  void queryClient.invalidateQueries({ queryKey: itineraryKey(userId, id) });
  void queryClient.invalidateQueries({ queryKey: itinerariesKey(userId) });
}

export function useAddChecklistItem(id: string) {
  const userId = useViewerId();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: ChecklistIn) =>
      unwrap(
        await client.POST('/api/v1/itineraries/{itinerary_id}/checklist', {
          params: { path: { itinerary_id: id } },
          body,
        }),
      ),
    onSuccess: () => invalidateItinerary(queryClient, userId, id),
  });
}

export function useUpdateChecklistItem(id: string) {
  const userId = useViewerId();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ itemId, body }: { itemId: string; body: ChecklistPatch }) =>
      unwrap(
        await client.PATCH('/api/v1/itineraries/{itinerary_id}/checklist/{item_id}', {
          params: { path: { itinerary_id: id, item_id: itemId } },
          body,
        }),
      ),
    onSuccess: () => invalidateItinerary(queryClient, userId, id),
  });
}

export function useDeleteChecklistItem(id: string) {
  const userId = useViewerId();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (itemId: string) =>
      unwrap<undefined>(
        await client.DELETE('/api/v1/itineraries/{itinerary_id}/checklist/{item_id}', {
          params: { path: { itinerary_id: id, item_id: itemId } },
        }),
      ),
    onSuccess: () => invalidateItinerary(queryClient, userId, id),
  });
}

export function useScheduleItinerary(id: string) {
  const userId = useViewerId();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: ScheduleIn) =>
      unwrap(
        await client.POST('/api/v1/itineraries/{itinerary_id}/schedule', {
          params: { path: { itinerary_id: id } },
          body,
        }),
      ),
    onSuccess: () => invalidateItinerary(queryClient, userId, id),
  });
}

export function useCancelItinerary(id: string) {
  const userId = useViewerId();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () =>
      unwrap(
        await client.POST('/api/v1/itineraries/{itinerary_id}/cancel', {
          params: { path: { itinerary_id: id } },
        }),
      ),
    onSuccess: () => invalidateItinerary(queryClient, userId, id),
  });
}

async function replaceItinerary(
  queryClient: ReturnType<typeof useQueryClient>,
  userId: string | undefined,
  id: string,
  data: ItineraryOut,
) {
  // An in-flight refetch started before this write would otherwise land after it with old data.
  await queryClient.cancelQueries({ queryKey: itineraryKey(userId, id) });
  queryClient.setQueryData(itineraryKey(userId, id), data);
  void queryClient.invalidateQueries({ queryKey: itinerariesKey(userId) });
}

export function useReplaceStops(id: string) {
  const userId = useViewerId();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (stops: StopIn[]) =>
      unwrap(
        await client.PUT('/api/v1/itineraries/{itinerary_id}/stops', {
          params: { path: { itinerary_id: id } },
          body: { stops },
        }),
      ),
    onSuccess: (data) => replaceItinerary(queryClient, userId, id, data),
  });
}

export const regenerateStopKey = (userId: string | undefined, id: string) =>
  ['regenerate-stop', userId, id] as const;

/** True while any stop of this itinerary is being regenerated; each response replaces the whole itinerary. */
export function useIsRegenerating(id: string): boolean {
  const userId = useViewerId();
  return useIsMutating({ mutationKey: regenerateStopKey(userId, id) }) > 0;
}

export function useRegenerateStop(id: string) {
  const userId = useViewerId();
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: regenerateStopKey(userId, id),
    mutationFn: async ({ stopId, note }: { stopId: string; note?: string }) =>
      unwrap(
        await client.POST('/api/v1/itineraries/{itinerary_id}/stops/{stop_id}/regenerate', {
          params: { path: { itinerary_id: id, stop_id: stopId } },
          ...(note ? { body: { note } } : {}),
        }),
      ),
    onSuccess: (data) => replaceItinerary(queryClient, userId, id, data),
  });
}

export function usePolishInvitation(id: string) {
  return useMutation({
    mutationFn: async (draft: string) =>
      unwrap(
        await client.POST('/api/v1/itineraries/{itinerary_id}/polish-invitation', {
          params: { path: { itinerary_id: id } },
          body: { draft },
        }),
      ),
  });
}

export function useUpdateItinerary(id: string) {
  const userId = useViewerId();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: ItineraryPatch) =>
      unwrap(
        await client.PATCH('/api/v1/itineraries/{itinerary_id}', {
          params: { path: { itinerary_id: id } },
          body,
        }),
      ),
    onSuccess: (data) => replaceItinerary(queryClient, userId, id, data),
  });
}

export function usePlaceSearch(query: string) {
  const userId = useViewerId();
  const q = query.trim();
  return useQuery({
    queryKey: ['places', userId, q] as const,
    enabled: !!userId && q.length >= 2,
    queryFn: async () =>
      unwrap(await client.GET('/api/v1/places/search', { params: { query: { q } } })),
    retry: false,
    staleTime: 60_000,
  });
}

export function useReactToItinerary(id: string) {
  const userId = useViewerId();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: ReactIn) =>
      unwrap(
        await client.POST('/api/v1/itineraries/{itinerary_id}/react', {
          params: { path: { itinerary_id: id } },
          body,
        }),
      ),
    onSuccess: () => invalidateItinerary(queryClient, userId, id),
  });
}
