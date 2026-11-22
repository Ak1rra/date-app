import { useEffect, useLayoutEffect, useRef } from 'react';
import { Button } from '../../components/Button';
import { EmptyState } from '../../components/EmptyState';
import { ChatIcon } from '../../components/Icons';
import type { ChatMessageOut, ChatStream } from '../../hooks/useChat';
import { PERSON_CARD } from '../us/options';
import { ActionCard } from './ActionCard';
import { SUGGESTIONS } from './Composer';
import type { Person } from '../ideas/usePeople';

export interface ChatPeople {
  meName: string;
  partnerName: string;
  mePerson: Person | null;
  partnerPerson: Person | null;
}

const NEAR_BOTTOM_PX = 48;

const bubbleBase = 'max-w-[85%] rounded-2xl border px-3 py-2 whitespace-pre-wrap';
const ASSISTANT_BUBBLE = `${bubbleBase} self-start border-line bg-surface-2 text-ink`;
const MY_BUBBLE = `${bubbleBase} self-end border-accent bg-accent text-on-accent`;

function Bubble({
  message,
  shared,
  people,
}: {
  message: ChatMessageOut;
  shared: boolean;
  people: ChatPeople;
}) {
  if (message.role === 'assistant') {
    return (
      <div className="flex flex-col items-start gap-2">
        <div className={ASSISTANT_BUBBLE}>{message.content}</div>
        {message.actions.map((action) => (
          <ActionCard key={action.id} action={action} />
        ))}
      </div>
    );
  }
  if (!shared) return <div className={MY_BUBBLE}>{message.content}</div>;

  // In the shared thread each author gets their person colour (and name), mine on the right.
  const mine = message.author_is_me;
  const person = mine ? people.mePerson : people.partnerPerson;
  const name = mine ? people.meName : people.partnerName;
  return (
    <div className={`flex flex-col gap-1 ${mine ? 'items-end' : 'items-start'}`}>
      <span className="px-1 text-xs font-semibold text-muted">{name}</span>
      <div
        className={`${bubbleBase} text-ink ${mine ? 'self-end' : 'self-start'} ${
          person ? PERSON_CARD[person] : 'border-line bg-surface-2'
        }`}
      >
        {message.content}
      </div>
    </div>
  );
}

/**
 * The conversation, oldest to newest, with "Load earlier" on top. It follows new content to the
 * bottom unless the reader has scrolled up. The region is a polite live region marked busy while
 * a reply streams, so a screen reader hears the finished reply, not every token.
 */
export function MessageList({
  messages,
  hasEarlier,
  loadingEarlier,
  onLoadEarlier,
  stream,
  streaming,
  shared,
  people,
  onSuggest,
}: {
  messages: ChatMessageOut[];
  hasEarlier: boolean;
  loadingEarlier: boolean;
  onLoadEarlier: () => void;
  stream: ChatStream;
  streaming: boolean;
  shared: boolean;
  people: ChatPeople;
  onSuggest: (text: string) => void;
}) {
  const scroller = useRef<HTMLDivElement>(null);
  const following = useRef(true);
  const lastId = messages[messages.length - 1]?.id;

  useLayoutEffect(() => {
    const el = scroller.current;
    if (el && following.current) el.scrollTop = el.scrollHeight;
  }, [lastId, stream.pendingContent, stream.text, stream.status, stream.actions.length, streaming]);

  // The first render of a thread starts at the bottom.
  useEffect(() => {
    following.current = true;
  }, []);

  // A refetch (a realtime event, say) can bring in what the stream is about to report. Hide the
  // overlay once the fetched list has it, so nothing shows twice.
  const fresh = (m: ChatMessageOut) => !stream.baseIds.has(m.id);
  const sentIndex = streaming
    ? messages.findIndex((m) =>
        stream.ackId
          ? m.id === stream.ackId
          : fresh(m) && m.role === 'user' && m.author_is_me && m.content === stream.pendingContent,
      )
    : -1;
  const pendingPersisted = !!stream.pendingContent && sentIndex >= 0;
  const replyPersisted =
    sentIndex >= 0 && messages.slice(sentIndex + 1).some((m) => m.role === 'assistant' && fresh(m));
  const showPending = !!stream.pendingContent && !pendingPersisted;
  const showReply = streaming && !stream.pendingContent && !replyPersisted;

  const empty = messages.length === 0 && !showPending && !streaming;
  const awaitingText = showReply && !stream.text;

  return (
    <div
      ref={scroller}
      role="log"
      aria-label="Conversation"
      aria-live="polite"
      aria-busy={streaming}
      onScroll={(e) => {
        const el = e.currentTarget;
        following.current = el.scrollHeight - el.scrollTop - el.clientHeight <= NEAR_BOTTOM_PX;
      }}
      className="flex max-h-[60dvh] min-h-64 flex-col gap-3 overflow-y-auto p-1"
    >
      {hasEarlier ? (
        <Button
          variant="secondary"
          className="self-center"
          onClick={onLoadEarlier}
          disabled={loadingEarlier}
        >
          {loadingEarlier ? 'Loading…' : 'Load earlier'}
        </Button>
      ) : null}
      {empty ? (
        <EmptyState
          icon={<ChatIcon />}
          title="Let's plan something"
          action={
            <div className="flex flex-wrap justify-center gap-2">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => onSuggest(s)}
                  className="min-h-11 rounded-full border border-line bg-surface px-4 py-2 text-sm font-semibold text-ink hover:bg-surface-2 motion-safe:transition"
                >
                  {s}
                </button>
              ))}
            </div>
          }
        >
          {shared
            ? 'Ask for ideas, plans and times together. You both see this conversation.'
            : 'Ask for date ideas, plans and times. This conversation is just yours.'}
        </EmptyState>
      ) : null}
      {messages.map((message) => (
        <Bubble key={message.id} message={message} shared={shared} people={people} />
      ))}
      {showPending ? (
        <div className={`${MY_BUBBLE} opacity-70`}>
          {stream.pendingContent}
          <span className="sr-only"> (sending)</span>
        </div>
      ) : null}
      {showReply ? (
        <div className="flex flex-col items-start gap-2" data-testid="streaming-reply">
          {stream.status ? (
            <span className="rounded-full border border-line bg-accent-soft px-3 py-1 text-xs font-semibold text-accent">
              {stream.status}
            </span>
          ) : null}
          <div className={ASSISTANT_BUBBLE}>
            {stream.text || (awaitingText ? <span className="text-muted">Thinking…</span> : null)}
          </div>
          {stream.actions.map((action) => (
            <ActionCard key={action.id} action={action} />
          ))}
        </div>
      ) : null}
      <p role="status" className="sr-only">
        {stream.completed ? 'The assistant replied.' : ''}
      </p>
    </div>
  );
}
