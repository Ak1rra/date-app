import { useState } from 'react';
import { Link } from 'react-router';
import { Card, EmptyState } from '../components/EmptyState';
import { LoadError, Segmented, Skeleton } from '../components/forms';
import { ChatIcon } from '../components/Icons';
import { useThreads } from '../hooks/useChat';
import { useEmailMode } from '../hooks/usePairing';
import { useViewerId } from '../hooks/useSession';
import { ChatThread } from './chat/ChatThread';

type Which = 'mine' | 'together';

/** Keyed by the viewer, so a profile switch remounts the body: the thread, draft and stream reset. */
export function ChatPage() {
  const viewerId = useViewerId();
  return <ChatBody key={viewerId ?? 'none'} />;
}

function ChatBody() {
  const threads = useThreads();
  const emailMode = useEmailMode();
  const [which, setWhich] = useState<Which>('mine');

  const mine = threads.data?.find((t) => t.scope === 'private');
  const shared = threads.data?.find((t) => t.scope === 'shared');
  const thread = which === 'together' && shared ? shared : mine;

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-2xl font-bold">Chat</h1>
      {threads.isPending ? (
        <Skeleton label="Loading your chats…" />
      ) : threads.isError ? (
        <LoadError error={threads.error} onRetry={() => void threads.refetch()} />
      ) : (
        <>
          {shared ? (
            <Segmented
              label="Conversation"
              options={[
                { value: 'mine', label: 'Mine' },
                { value: 'together', label: 'Together' },
              ]}
              value={which}
              onChange={setWhich}
            />
          ) : (
            <p className="text-sm text-muted">
              Invite your partner to unlock a shared chat you can both see.
              {emailMode ? (
                <>
                  {' '}
                  <Link to="/us#invite" className="underline">
                    Invite them
                  </Link>
                </>
              ) : null}
            </p>
          )}
          <Card>
            {thread ? (
              <ChatThread key={thread.id} thread={thread} />
            ) : (
              <EmptyState icon={<ChatIcon />} title="Chat isn't ready yet">
                Try again in a moment.
              </EmptyState>
            )}
          </Card>
        </>
      )}
    </div>
  );
}
