import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query';
import { client, unwrap, type Schemas } from '../api/client';
import { ApiError } from '../api/http';
import { clearOfflineApiCache } from '../lib/offlineApi';
import { useMeta } from './useMeta';
import { useViewerId } from './useSession';

export type InviteOut = Schemas['InviteOut'];
export type InviteCurrentOut = Schemas['InviteCurrentOut'];
export type JoinIn = Schemas['JoinIn'];

const inviteKey = (userId: string | undefined) => ['invite', userId] as const;

/**
 * True once the server says it has real accounts. The pairing routes 404 in NO_LOGIN mode, so
 * every pairing control waits for this (and stays hidden while meta is still loading).
 */
export function useEmailMode(): boolean {
  return useMeta().data?.no_login === false;
}

/** GET /couple/invites/current. A 404 `no_invite` is an answer ("none yet"), so it maps to null. */
export function useCurrentInvite(enabled: boolean) {
  const userId = useViewerId();
  return useQuery({
    queryKey: inviteKey(userId),
    queryFn: async (): Promise<InviteCurrentOut | null> => {
      try {
        return unwrap(await client.GET('/api/v1/couple/invites/current'));
      } catch (error) {
        if (error instanceof ApiError && error.status === 404 && error.code === 'no_invite') {
          return null;
        }
        throw error;
      }
    },
    enabled: enabled && !!userId,
  });
}

/** POST /couple/invites. The link is only in this response; the server never returns it again. */
export function useCreateInvite() {
  const userId = useViewerId();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => unwrap(await client.POST('/api/v1/couple/invites')),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: inviteKey(userId) }),
  });
}

/** DELETE /couple/invites/current */
export function useRevokeInvite() {
  const userId = useViewerId();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () =>
      unwrap<undefined>(await client.DELETE('/api/v1/couple/invites/current')),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: inviteKey(userId) }),
  });
}

/**
 * The couple changed, so nothing cached for the old space is right any more. Reset (not just
 * invalidate) puts every query back to its initial state, so inactive ones can't render stale
 * data later, and refetches the active ones. The offline copy goes first so it can't serve the
 * old couple either.
 */
async function resetEverything(queryClient: QueryClient) {
  await clearOfflineApiCache();
  await queryClient.resetQueries();
}

/** POST /couple/join with the link's token or a typed short code. */
export function useJoin() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: JoinIn) => unwrap(await client.POST('/api/v1/couple/join', { body })),
    onSuccess: async () => {
      clearPendingInvite();
      await resetEverything(queryClient);
    },
  });
}

/** POST /couple/leave */
export function useLeave() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => unwrap<undefined>(await client.POST('/api/v1/couple/leave')),
    onSuccess: () => resetEverything(queryClient),
  });
}

const PENDING_KEY = 'dateapp-pending-invite';

/** An invite the person opened or typed but hasn't used yet; kept across the sign-in round trip. */
export interface PendingInvite {
  token?: string;
  short_code?: string;
}

// Storage can be blocked or full (private windows, quota): every access is best-effort.
export function getPendingInvite(): PendingInvite | null {
  try {
    const raw = localStorage.getItem(PENDING_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as { token?: unknown; short_code?: unknown } | null;
    const pending: PendingInvite = {};
    if (typeof parsed?.token === 'string' && parsed.token) pending.token = parsed.token;
    if (typeof parsed?.short_code === 'string' && parsed.short_code) {
      pending.short_code = parsed.short_code;
    }
    return pending.token || pending.short_code ? pending : null;
  } catch {
    return null;
  }
}

export function setPendingInvite(invite: PendingInvite): void {
  try {
    localStorage.setItem(PENDING_KEY, JSON.stringify(invite));
  } catch {
    // The invite still works for this page load.
  }
}

export function clearPendingInvite(): void {
  try {
    localStorage.removeItem(PENDING_KEY);
  } catch {
    // Nothing to clear if storage is unavailable.
  }
}
