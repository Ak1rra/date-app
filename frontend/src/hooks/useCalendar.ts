import { useEffect } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { client, unwrap, type Schemas } from '../api/client';
import { fetchItinerary, itineraryKey } from './useItineraries';
import { useViewerId } from './useSession';

export type CalendarEntry = Schemas['CalendarEntry'];
export type SuggestTimesIn = Schemas['SuggestTimesIn'];
export type SuggestTimesOut = Schemas['SuggestTimesOut'];
export type TimeSlot = Schemas['TimeSlot'];

/** Itineraries starting within this many days are cached for offline use (FR-ITIN-8). */
const PREFETCH_DAYS = 30;

export const calendarKey = (userId: string | undefined, from: string, to: string) =>
  ['calendar', userId, from, to] as const;

const fetchCalendar = async (from: string, to: string) =>
  unwrap(await client.GET('/api/v1/calendar', { params: { query: { from, to } } }));

/** `from` and `to` are ISO datetimes; the API caps the range at 120 days. */
export function useCalendar(from: string, to: string) {
  const userId = useViewerId();
  return useQuery({
    queryKey: calendarKey(userId, from, to),
    enabled: !!userId,
    queryFn: () => fetchCalendar(from, to),
  });
}

export function useSuggestTimes() {
  return useMutation({
    mutationFn: async (body: SuggestTimesIn) =>
      unwrap(await client.POST('/api/v1/calendar/suggest-times', { body })),
  });
}

/**
 * Warms the cache with every itinerary behind a date in the next 30 days, so it opens offline.
 * Best effort: a failure here must never show on the page.
 */
export function usePrefetchUpcoming() {
  const userId = useViewerId();
  const queryClient = useQueryClient();
  useEffect(() => {
    if (!userId) return;
    let cancelled = false;
    const now = new Date();
    const from = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    // The API's `to` is exclusive.
    const to = new Date(now.getFullYear(), now.getMonth(), now.getDate() + PREFETCH_DAYS + 1);
    const [fromIso, toIso] = [from.toISOString(), to.toISOString()];
    queryClient
      .fetchQuery({
        queryKey: calendarKey(userId, fromIso, toIso),
        queryFn: () => fetchCalendar(fromIso, toIso),
        staleTime: 60_000,
      })
      .then((entries) => {
        if (cancelled) return;
        for (const entry of entries) {
          const id = entry.itinerary_id;
          if (entry.kind !== 'date' || !id || entry.status === 'cancelled') continue;
          if (new Date(entry.starts_at) > to) continue;
          void queryClient.prefetchQuery({
            queryKey: itineraryKey(userId, id),
            queryFn: () => fetchItinerary(id),
            staleTime: 60_000,
          });
        }
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [userId, queryClient]);
}
