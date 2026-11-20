import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { client, unwrap, type Schemas } from '../api/client';
import { useViewerId } from './useSession';

export type ProfileOut = Schemas['ProfileOut'];
export type ProfileIn = Schemas['ProfileIn'];
export type AvailabilityIO = Schemas['AvailabilityIO'];

const profilesKey = (userId: string | undefined) => ['profiles', userId] as const;
const availabilityKey = (userId: string | undefined, id: string) =>
  ['availability', userId, id] as const;

/** GET /profiles: both slots, owner (husband) first. */
export function useProfiles() {
  const userId = useViewerId();
  return useQuery({
    queryKey: profilesKey(userId),
    enabled: !!userId,
    queryFn: async () => unwrap(await client.GET('/api/v1/profiles')),
  });
}

/** The editable shape of a profile (PUT replaces the whole thing). */
export function toProfileIn(p: ProfileOut): ProfileIn {
  return {
    display_name: p.display_name,
    liked_categories: p.liked_categories as ProfileIn['liked_categories'],
    disliked_categories: p.disliked_categories as ProfileIn['disliked_categories'],
    cuisines_liked: p.cuisines_liked,
    cuisines_disliked: p.cuisines_disliked,
    energy: p.energy,
    social_setting: p.social_setting,
    indoor_outdoor: p.indoor_outdoor as ProfileIn['indoor_outdoor'],
    loves: p.loves,
    hard_nos: p.hard_nos,
    dietary: p.dietary as ProfileIn['dietary'],
    allergies: p.allergies,
    accessibility: p.accessibility,
  };
}

/** PUT /profiles/{id} */
export function useSaveProfile() {
  const userId = useViewerId();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, body }: { id: string; body: ProfileIn }) =>
      unwrap(
        await client.PUT('/api/v1/profiles/{profile_id}', {
          params: { path: { profile_id: id } },
          body,
        }),
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: profilesKey(userId) }),
  });
}

/** GET /profiles/{id}/availability */
export function useAvailability(id: string) {
  const userId = useViewerId();
  return useQuery({
    queryKey: availabilityKey(userId, id),
    enabled: !!userId,
    queryFn: async () =>
      unwrap(
        await client.GET('/api/v1/profiles/{profile_id}/availability', {
          params: { path: { profile_id: id } },
        }),
      ),
  });
}

/** PUT /profiles/{id}/availability (replace-all) */
export function useSaveAvailability(id: string) {
  const userId = useViewerId();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: AvailabilityIO) =>
      unwrap(
        await client.PUT('/api/v1/profiles/{profile_id}/availability', {
          params: { path: { profile_id: id } },
          body,
        }),
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: availabilityKey(userId, id) }),
  });
}
