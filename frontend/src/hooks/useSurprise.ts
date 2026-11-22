import { useEffect, useRef } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { client, unwrap, type Schemas } from '../api/client';
import { itineraryKey, itinerariesKey, type ItineraryOut } from './useItineraries';
import { useViewerId } from './useSession';

export type HintOut = Schemas['HintOut'];
export type HintIn = Schemas['HintIn'];
export type HintPatch = Schemas['HintPatch'];
export type RevealAtIn = Schemas['RevealAtIn'];
export type HintSuggestionOut = Schemas['HintSuggestionOut'];

export const hintsKey = (userId: string | undefined, id: string | undefined) =>
  ['hints', userId, id] as const;

/** GET /itineraries/{id}/hints: the planner's hints, soonest release first. */
export function useHints(id: string | undefined, enabled = true) {
  const userId = useViewerId();
  return useQuery({
    queryKey: hintsKey(userId, id),
    enabled: enabled && !!userId && !!id,
    queryFn: async () =>
      unwrap(
        await client.GET('/api/v1/itineraries/{itinerary_id}/hints', {
          params: { path: { itinerary_id: id! } },
        }),
      ),
  });
}

function useInvalidateHints(id: string) {
  const userId = useViewerId();
  const queryClient = useQueryClient();
  return () => void queryClient.invalidateQueries({ queryKey: hintsKey(userId, id) });
}

/**
 * Refetches the hints when `version` changes after mount: scheduling and rescheduling run due
 * processing server-side, which can release hints the cache still shows as waiting.
 */
export function useRefreshHintsOnChange(id: string, version: string) {
  const userId = useViewerId();
  const queryClient = useQueryClient();
  const previous = useRef(version);
  useEffect(() => {
    if (previous.current === version) return;
    previous.current = version;
    void queryClient.invalidateQueries({ queryKey: hintsKey(userId, id) });
  }, [queryClient, userId, id, version]);
}

export function useAddHint(id: string) {
  const invalidate = useInvalidateHints(id);
  return useMutation({
    mutationFn: async (body: HintIn) =>
      unwrap(
        await client.POST('/api/v1/itineraries/{itinerary_id}/hints', {
          params: { path: { itinerary_id: id } },
          body,
        }),
      ),
    onSuccess: invalidate,
  });
}

/** POST /hints/suggest: AI hint ideas for the planner. Nothing is saved, so nothing is refetched. */
export function useSuggestHints(id: string) {
  return useMutation({
    mutationFn: async () =>
      unwrap(
        await client.POST('/api/v1/itineraries/{itinerary_id}/hints/suggest', {
          params: { path: { itinerary_id: id } },
        }),
      ),
  });
}

export function useUpdateHint(id: string) {
  const invalidate = useInvalidateHints(id);
  return useMutation({
    mutationFn: async ({ hintId, body }: { hintId: string; body: HintPatch }) =>
      unwrap(
        await client.PATCH('/api/v1/itineraries/{itinerary_id}/hints/{hint_id}', {
          params: { path: { itinerary_id: id, hint_id: hintId } },
          body,
        }),
      ),
    onSuccess: invalidate,
  });
}

export function useDeleteHint(id: string) {
  const invalidate = useInvalidateHints(id);
  return useMutation({
    mutationFn: async (hintId: string) =>
      unwrap<undefined>(
        await client.DELETE('/api/v1/itineraries/{itinerary_id}/hints/{hint_id}', {
          params: { path: { itinerary_id: id, hint_id: hintId } },
        }),
      ),
    onSuccess: invalidate,
  });
}

function useWriteItinerary(id: string) {
  const userId = useViewerId();
  const queryClient = useQueryClient();
  return (data: ItineraryOut) => {
    queryClient.setQueryData(itineraryKey(userId, id), data);
    void queryClient.invalidateQueries({ queryKey: itinerariesKey(userId) });
  };
}

/** PUT /reveal-at: a time, or null for "at the start". Returns the itinerary. */
export function useSetRevealAt(id: string) {
  const write = useWriteItinerary(id);
  const invalidateHints = useInvalidateHints(id);
  return useMutation({
    mutationFn: async (body: RevealAtIn) =>
      unwrap(
        await client.PUT('/api/v1/itineraries/{itinerary_id}/reveal-at', {
          params: { path: { itinerary_id: id } },
          body,
        }),
      ),
    // A past time or "at the start" can release due hints server-side.
    onSuccess: (data) => {
      write(data);
      invalidateHints();
    },
  });
}

/** POST /reveal: show the surprise now. Returns the itinerary. */
export function useRevealNow(id: string) {
  const write = useWriteItinerary(id);
  const invalidateHints = useInvalidateHints(id);
  return useMutation({
    mutationFn: async () =>
      unwrap(
        await client.POST('/api/v1/itineraries/{itinerary_id}/reveal', {
          params: { path: { itinerary_id: id } },
        }),
      ),
    onSuccess: (data) => {
      write(data);
      invalidateHints();
    },
  });
}
