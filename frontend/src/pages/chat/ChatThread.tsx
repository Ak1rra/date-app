import { useState } from 'react';
import { LoadError, Skeleton } from '../../components/forms';
import { useMessages, useSendMessage, type ChatThreadOut } from '../../hooks/useChat';
import { useProfiles } from '../../hooks/useProfiles';
import { personOf } from '../us/options';
import { Composer } from './Composer';
import { MessageList, type ChatPeople } from './MessageList';

/** One conversation: its messages, the streaming reply, and the composer. Keyed by thread id. */
export function ChatThread({ thread }: { thread: ChatThreadOut }) {
  const messages = useMessages(thread.id);
  const { send, stop, stream, streaming } = useSendMessage(thread.id);
  const profiles = useProfiles().data;
  const [draft, setDraft] = useState('');

  const me = profiles?.find((p) => p.is_me);
  const partner = profiles?.find((p) => !p.is_me);
  const people: ChatPeople = {
    meName: me?.display_name || 'You',
    partnerName: partner?.display_name || 'Your partner',
    mePerson: me ? personOf(me.slot) : null,
    partnerPerson: partner ? personOf(partner.slot) : null,
  };

  return (
    <div className="flex flex-col gap-4">
      {messages.isPending ? (
        <Skeleton label="Loading the conversation…" />
      ) : messages.isError ? (
        <LoadError error={messages.error} onRetry={() => void messages.refetch()} />
      ) : (
        <MessageList
          messages={messages.messages}
          hasEarlier={messages.hasNextPage}
          loadingEarlier={messages.isFetchingNextPage}
          onLoadEarlier={() => void messages.fetchNextPage()}
          stream={stream}
          streaming={streaming}
          shared={thread.scope === 'shared'}
          people={people}
          onSuggest={setDraft}
        />
      )}
      <Composer
        draft={draft}
        onDraft={setDraft}
        onSend={(content) =>
          send(content, (unsent) =>
            setDraft((d) =>
              d
                ? `${unsent}
${d}`
                : unsent,
            ),
          )
        }
        onStop={stop}
        streaming={streaming}
        ready={messages.isSuccess}
        error={stream.error}
      />
    </div>
  );
}
