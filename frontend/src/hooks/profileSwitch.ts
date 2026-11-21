import { useEffect } from 'react';
import { useQueryClient, type QueryClient } from '@tanstack/react-query';
import { beginProfileChange, setActiveProfile } from '../api/http';
import { clearOfflineApiCache } from '../lib/offlineApi';
import { META_KEY } from './useMeta';
import { SESSION_KEY, type Session } from './useSession';

const CHANNEL_NAME = 'dateapp-session';

/**
 * Per-viewer queries are keyed [root, userId, ...] (see useViewerId), so another person's data
 * can't render for the current one. Everything below is tidy-up: it drops other people's entries
 * and discards responses that were already in flight. Correctness doesn't depend on its timing.
 */
const isShared = (q: { queryKey: readonly unknown[] }) =>
  q.queryKey[0] === SESSION_KEY[0] || q.queryKey[0] === META_KEY[0];
const isViewerQuery = (q: { queryKey: readonly unknown[] }) => !isShared(q);

/** Removes every viewer query that belongs to someone other than `userId`. */
function removeOthersData(queryClient: QueryClient, userId: string) {
  queryClient.removeQueries({
    predicate: (q) => isViewerQuery(q) && q.queryKey[1] !== userId,
  });
}

function broadcast(message: { type: 'profile-changed'; userId: string | null }) {
  try {
    if (typeof BroadcastChannel === 'undefined') return;
    const channel = new BroadcastChannel(CHANNEL_NAME);
    channel.postMessage(message);
    channel.close();
  } catch {
    // Other tabs fall back to noticing the new user id the next time their session loads.
  }
}

/** Called after choose-profile succeeds: discard in-flight fetches, adopt the new session, tidy up. */
export async function resetForProfile(queryClient: QueryClient, session: Session) {
  // First, so a session response still in flight for the old profile can't set its header again.
  beginProfileChange();
  // Viewer queries and the session only; meta doesn't depend on who is using the app.
  await queryClient.cancelQueries({
    predicate: (q) => isViewerQuery(q) || q.queryKey[0] === SESSION_KEY[0],
  });
  // Awaited before the header changes, so nothing from the old profile survives the switch.
  await clearOfflineApiCache();
  queryClient.setQueryData(SESSION_KEY, session);
  const userId = session.user?.id ?? null;
  setActiveProfile(userId);
  if (userId) removeOthersData(queryClient, userId);
  // The new header is set, so the service worker caches this session under the new profile and an
  // offline reload can bootstrap from it. Awaited so the caller navigates only once it is cached.
  if (userId) await queryClient.refetchQueries({ queryKey: SESSION_KEY });
  broadcast({ type: 'profile-changed', userId });
}

/**
 * Keeps this tab honest when the profile is switched somewhere else: a message from another tab
 * refetches the session, and whenever the session shows a user, other people's entries are removed.
 */
export function useProfileSwitchSync(userId: string | undefined) {
  const queryClient = useQueryClient();

  useEffect(() => {
    if (typeof BroadcastChannel === 'undefined') return;
    let channel: BroadcastChannel;
    try {
      channel = new BroadcastChannel(CHANNEL_NAME);
    } catch {
      return;
    }
    channel.onmessage = (event: MessageEvent) => {
      const data = event.data as { type?: unknown; userId?: unknown } | null;
      if (data?.type !== 'profile-changed') return;
      const current = queryClient.getQueryData<Session>(SESSION_KEY)?.user?.id ?? null;
      if (data.userId === current) return;
      // Adopt the new profile's header right away (the same value the initiating tab stored); the
      // session refetch confirms it.
      beginProfileChange();
      setActiveProfile(typeof data.userId === 'string' ? data.userId : null);
      void queryClient.invalidateQueries({ queryKey: SESSION_KEY });
      // Meta is never cancelled here, but make sure it can't be left without an answer.
      const meta = queryClient.getQueryState(META_KEY);
      if (meta && !meta.data && meta.fetchStatus === 'idle') {
        void queryClient.refetchQueries({ queryKey: META_KEY });
      }
    };
    return () => channel.close();
  }, [queryClient]);

  useEffect(() => {
    if (userId) removeOthersData(queryClient, userId);
  }, [queryClient, userId]);
}
