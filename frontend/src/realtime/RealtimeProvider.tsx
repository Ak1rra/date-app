import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from 'react';
import { MatchCelebration } from '../pages/ideas/MatchCelebration';
import { useViewerId } from '../hooks/useSession';
import { useRealtime, type RealtimeStatus } from './useRealtime';

interface MatchCelebrationApi {
  /** True while the overlay is showing. */
  active: boolean;
  /** Shows the overlay unless this idea's match was already celebrated in this app session. */
  celebrate: (ideaId: string, title: string) => void;
}

const StatusContext = createContext<RealtimeStatus>('closed');
const CelebrationContext = createContext<MatchCelebrationApi | null>(null);

/** Whether the app-wide socket is 'connecting', 'open' or 'closed'. */
export function useRealtimeStatus(): RealtimeStatus {
  return useContext(StatusContext);
}

/** The one place a match celebration is shown, whether the local vote or the socket found it. */
export function useMatchCelebration(): MatchCelebrationApi {
  const api = useContext(CelebrationContext);
  if (!api) throw new Error('useMatchCelebration must be used inside RealtimeProvider');
  return api;
}

export function RealtimeProvider({ children }: { children: ReactNode }) {
  const viewerId = useViewerId();
  const [shown, setShown] = useState<{ viewerId: string | undefined; title: string } | null>(null);
  const celebrated = useRef(new Set<string>());

  // A different person must never see the previous profile's overlay.
  if (shown && shown.viewerId !== viewerId) setShown(null);

  const celebrate = useCallback(
    (ideaId: string, title: string) => {
      // A match happens once per idea, so remembering it for the app session is enough.
      const key = `${viewerId}:${ideaId}`;
      if (celebrated.current.has(key)) return;
      celebrated.current.add(key);
      setShown({ viewerId, title });
    },
    [viewerId],
  );

  const status = useRealtime(({ ideaId, title }) => celebrate(ideaId, title));
  const close = useCallback(() => setShown(null), []);

  return (
    <StatusContext value={status}>
      <CelebrationContext value={{ active: shown !== null, celebrate }}>
        {children}
        {shown ? <MatchCelebration idea={{ title: shown.title }} onClose={close} /> : null}
      </CelebrationContext>
    </StatusContext>
  );
}
