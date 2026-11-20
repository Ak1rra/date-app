import { useMutation, useQueryClient } from '@tanstack/react-query';
import { apiFetch } from '../api/http';
import { resetForProfile } from './profileSwitch';
import type { Session } from './useSession';

export type Profile = 'husband' | 'wife';

/** POST /api/v1/auth/choose-profile: picks (or switches) who is using the app. */
export function useChooseProfile() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (profile: Profile) =>
      apiFetch<Session>('/api/v1/auth/choose-profile', {
        method: 'POST',
        body: JSON.stringify({ profile }),
      }),
    onSuccess: (session) => resetForProfile(queryClient, session),
  });
}
