import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { client, unwrap, type Schemas } from '../api/client';
import { ApiError } from '../api/http';
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

export type ProfileSuggestionOut = Schemas['ProfileSuggestionOut'];
export type ProfileSuggestionIn = Schemas['ProfileSuggestionIn'];
export type ProfileChanges = Schemas['ProfileChanges'];

const suggestionsKey = (userId: string | undefined, id: string) =>
  ['profiles', userId, id, 'suggestions'] as const;

/** POST /profiles/{id}/confirm: the subject confirms a profile their partner filled in. */
export function useConfirmProfile(id: string) {
  const userId = useViewerId();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () =>
      unwrap(
        await client.POST('/api/v1/profiles/{profile_id}/confirm', {
          params: { path: { profile_id: id } },
        }),
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: profilesKey(userId) }),
  });
}

/** GET /profiles/{id}/suggestions?status=pending (the subject sees all; others only their own). */
export function useSuggestions(id: string, enabled: boolean) {
  const userId = useViewerId();
  return useQuery({
    queryKey: suggestionsKey(userId, id),
    enabled: !!userId && enabled,
    queryFn: async () =>
      unwrap(
        await client.GET('/api/v1/profiles/{profile_id}/suggestions', {
          params: { path: { profile_id: id }, query: { status: 'pending' } },
        }),
      ),
  });
}

/** POST /profiles/{id}/suggestions. A 409 `edit_directly` means the caller may edit it now. */
export function useCreateSuggestion(id: string) {
  const userId = useViewerId();
  const queryClient = useQueryClient();
  const refresh = () => queryClient.invalidateQueries({ queryKey: profilesKey(userId) });
  return useMutation({
    mutationFn: async (body: ProfileSuggestionIn) =>
      unwrap(
        await client.POST('/api/v1/profiles/{profile_id}/suggestions', {
          params: { path: { profile_id: id } },
          body,
        }),
      ),
    onSuccess: refresh,
    onError: (error) => {
      if (error instanceof ApiError && error.code === 'edit_directly') void refresh();
    },
  });
}

/** POST /profiles/{id}/suggestions/{sid}/accept (subject only). */
export function useAcceptSuggestion(id: string) {
  const userId = useViewerId();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (suggestionId: string) =>
      unwrap(
        await client.POST('/api/v1/profiles/{profile_id}/suggestions/{suggestion_id}/accept', {
          params: { path: { profile_id: id, suggestion_id: suggestionId } },
        }),
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: profilesKey(userId) }),
  });
}

/** POST /profiles/{id}/suggestions/{sid}/decline (subject only). */
export function useDeclineSuggestion(id: string) {
  const userId = useViewerId();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (suggestionId: string) =>
      unwrap(
        await client.POST('/api/v1/profiles/{profile_id}/suggestions/{suggestion_id}/decline', {
          params: { path: { profile_id: id, suggestion_id: suggestionId } },
        }),
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: profilesKey(userId) }),
  });
}
