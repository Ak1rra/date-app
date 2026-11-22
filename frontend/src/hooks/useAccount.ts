import { useMutation, useQueryClient } from '@tanstack/react-query';
import { client, unwrap, type Schemas } from '../api/client';
import { setActiveProfile } from '../api/http';
import { clearOfflineApiCache } from '../lib/offlineApi';
import { broadcast } from './profileSwitch';

export type AccountExport = Schemas['AccountExportOut'];
export type AccountDeleteOut = Schemas['AccountDeleteOut'];

/** GET /api/v1/me/export: the caller's own data. A mutation, because it is an action, not a view. */
export function useExportData() {
  return useMutation({
    mutationFn: async () => unwrap(await client.GET('/api/v1/me/export')),
  });
}

/**
 * DELETE /api/v1/me: resets the profile's data in NO_LOGIN mode, deletes the account otherwise.
 * The variable is the id of the profile the person confirmed for; the server refuses (409) if the
 * session has moved to another profile since. Cache cleanup lives here, not in a per-call callback,
 * because the component may unmount while the request is in flight.
 */
export function useDeleteMe() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (userId: string) =>
      unwrap(await client.DELETE('/api/v1/me', { params: { query: { user_id: userId } } })),
    onSuccess: async (result) => {
      if (result.mode === 'deleted') {
        queryClient.clear();
        await clearOfflineApiCache();
        setActiveProfile(null);
        // Other open tabs refetch their session, get a 401 and leave for /signin.
        broadcast({ type: 'profile-changed', userId: null });
      } else {
        await queryClient.invalidateQueries();
      }
    },
  });
}
