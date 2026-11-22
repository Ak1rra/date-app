import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { client, unwrap, type Schemas } from '../api/client';
import { itineraryKey, itinerariesKey } from './useItineraries';
import { useViewerId } from './useSession';

export type RatingIn = Schemas['RatingIn'];
export type RatingOut = Schemas['RatingOut'];
export type RatingsOut = Schemas['RatingsOut'];
export type PlannerReadIn = Schemas['PlannerReadIn'];
export type PlannerReadOut = Schemas['PlannerReadOut'];
export type PastDateOut = Schemas['PastDateOut'];

export const ratingsKey = (userId: string | undefined, id: string | undefined) =>
  ['ratings', userId, id] as const;
export const pastDatesKey = (userId: string | undefined) => ['past-dates', userId] as const;

export function useRatings(id: string | undefined) {
  const userId = useViewerId();
  return useQuery({
    queryKey: ratingsKey(userId, id),
    enabled: !!userId && !!id,
    queryFn: async () =>
      unwrap(
        await client.GET('/api/v1/itineraries/{itinerary_id}/ratings', {
          params: { path: { itinerary_id: id! } },
        }),
      ),
  });
}

export function usePastDates() {
  const userId = useViewerId();
  return useQuery({
    queryKey: pastDatesKey(userId),
    enabled: !!userId,
    queryFn: async () => unwrap(await client.GET('/api/v1/past-dates')),
  });
}

function useInvalidateFeedback(id: string) {
  const userId = useViewerId();
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({ queryKey: ratingsKey(userId, id) });
    void queryClient.invalidateQueries({ queryKey: pastDatesKey(userId) });
    void queryClient.invalidateQueries({ queryKey: itineraryKey(userId, id) });
    void queryClient.invalidateQueries({ queryKey: itinerariesKey(userId) });
  };
}

export function usePutRating(id: string) {
  const invalidate = useInvalidateFeedback(id);
  return useMutation({
    mutationFn: async (body: RatingIn) =>
      unwrap(
        await client.PUT('/api/v1/itineraries/{itinerary_id}/rating', {
          params: { path: { itinerary_id: id } },
          body,
        }),
      ),
    onSuccess: invalidate,
  });
}

export function usePutPlannerRead(id: string) {
  const invalidate = useInvalidateFeedback(id);
  return useMutation({
    mutationFn: async (body: PlannerReadIn) =>
      unwrap(
        await client.PUT('/api/v1/itineraries/{itinerary_id}/planner-read', {
          params: { path: { itinerary_id: id } },
          body,
        }),
      ),
    onSuccess: invalidate,
  });
}
