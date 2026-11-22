import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
  type InfiniteData,
} from '@tanstack/react-query';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { client, unwrap, type Schemas } from '../api/client';
import { ApiError } from '../api/http';
import { plannerError } from '../api/plannerError';
import { streamChat, type ChatActionOut, type ChatMessageOut } from '../api/sse';
import { friendlyMessage } from '../components/forms';
import { useViewerId } from './useSession';

export type { ChatActionOut, ChatMessageOut };
export type ChatThreadOut = Schemas['ChatThreadOut'];
export type ChatMessagesOut = Schemas['ChatMessagesOut'];
export type ChatActionApplyOut = Schemas['ChatActionApplyOut'];

export const CHAT_MAX_LENGTH = 2000;

const chatKey = (userId: string | undefined) => ['chat', userId] as const;
const messagesKey = (userId: string | undefined, threadId: string | undefined) =>
  [...chatKey(userId), 'messages', threadId] as const;

type MessagesData = InfiniteData<ChatMessagesOut, string | undefined>;

/** GET /chat/threads: my private thread, plus the shared one once my partner has joined. */
export function useThreads() {
  const userId = useViewerId();
  return useQuery({
    queryKey: [...chatKey(userId), 'threads'],
    enabled: !!userId,
    queryFn: async () => unwrap(await client.GET('/api/v1/chat/threads')),
  });
}

/**
 * GET /chat/threads/{id}/messages, newest page first: each page is oldest-to-newest and, while
 * `has_more`, its first id is the `before` of the next (older) page. `messages` is every loaded
 * page joined oldest to newest.
 */
export function useMessages(threadId: string | undefined) {
  const userId = useViewerId();
  const query = useInfiniteQuery({
    queryKey: messagesKey(userId, threadId),
    enabled: !!userId && !!threadId,
    initialPageParam: undefined as string | undefined,
    queryFn: async ({ pageParam }) =>
      unwrap(
        await client.GET('/api/v1/chat/threads/{thread_id}/messages', {
          params: { path: { thread_id: threadId! }, query: { before: pageParam } },
        }),
      ),
    getNextPageParam: (lastPage) => (lastPage.has_more ? lastPage.messages[0]?.id : undefined),
  });
  const pages = query.data?.pages;
  const messages = useMemo(
    () => (pages ? [...pages].reverse().flatMap((p) => p.messages) : []),
    [pages],
  );
  return { ...query, messages };
}

/** Roots an applied suggestion can change: the ideas, itineraries and calendar it created or edited. */
const APPLY_ROOTS = ['ideas', 'itineraries', 'itinerary', 'calendar', 'hints'];

/** POST /chat/actions/{id}/apply: applies what the assistant proposed (once; repeats are no-ops). */
export function useApplyAction() {
  const userId = useViewerId();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (actionId: string) =>
      unwrap(
        await client.POST('/api/v1/chat/actions/{action_id}/apply', {
          params: { path: { action_id: actionId } },
        }),
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: chatKey(userId) });
      for (const root of APPLY_ROOTS) void queryClient.invalidateQueries({ queryKey: [root] });
    },
  });
}

/** A sentence for a failed apply: the planner-only rule gets its own words. */
export function describeApplyError(error: unknown): string {
  if (error instanceof ApiError && error.status === 403) {
    return 'Only the planner can change this date.';
  }
  return friendlyMessage(plannerError(error));
}

/** A sentence for a failed send, before or during the stream. */
export function describeSendError(error: unknown): string {
  if (error instanceof ApiError && (error.status === 429 || error.code === 'rate_limited')) {
    return "You've used today's chat messages. Try again tomorrow.";
  }
  return friendlyMessage(plannerError(error));
}

/** What the page shows for the reply being streamed right now. */
export interface ChatStream {
  /** Waiting for the server's copy of what I just sent (shown as a pending bubble). */
  pendingContent: string | null;
  /** The reply so far. */
  text: string;
  status: string | null;
  actions: ChatActionOut[];
  /** From the last send: shown under the composer until the next send. */
  error: string | null;
  /** Set when the last reply finished, so it can be announced once. */
  completed: boolean;
  /** The stored copy of my message, once acknowledged. */
  ackId: string | null;
  /** Ids already in the thread when the send began (to tell what the server added since). */
  baseIds: ReadonlySet<string>;
}

const IDLE: ChatStream = {
  pendingContent: null,
  text: '',
  status: null,
  actions: [],
  error: null,
  completed: false,
  ackId: null,
  baseIds: new Set(),
};

interface Run extends ChatStream {
  threadId: string;
  streaming: boolean;
}

function append(data: MessagesData | undefined, message: ChatMessageOut): MessagesData | undefined {
  if (!data || data.pages.some((p) => p.messages.some((m) => m.id === message.id))) return data;
  const [newest, ...older] = data.pages;
  if (!newest) return data;
  return { ...data, pages: [{ ...newest, messages: [...newest.messages, message] }, ...older] };
}

/**
 * Sends a message to `threadId` and streams the reply. Switching threads or unmounting aborts the
 * stream; `stop()` aborts it on purpose. The acked and final messages are merged into the messages
 * query, which is then refetched so the cache ends up exactly as the server has it.
 */
export function useSendMessage(threadId: string | undefined) {
  const userId = useViewerId();
  const queryClient = useQueryClient();
  const [run, setRun] = useState<Run | null>(null);
  const controller = useRef<AbortController | null>(null);

  // Leaving the thread (or the page) ends its stream; `run` is only shown for its own thread.
  useEffect(
    () => () => {
      controller.current?.abort();
      controller.current = null;
    },
    [threadId],
  );

  /**
   * Starts a send. Returns false, and does nothing, while a send is still in flight (so the caller
   * keeps the draft). `onFailed` gets the text back if the send fails before the server acknowledged
   * it, so it can be restored to the composer.
   */
  const send = useCallback(
    (content: string, onFailed?: (content: string) => void): boolean => {
      if (!threadId || controller.current) return false;
      const ctrl = new AbortController();
      controller.current = ctrl;
      const key = messagesKey(userId, threadId);
      const baseIds = new Set(
        queryClient
          .getQueryData<MessagesData>(key)
          ?.pages.flatMap((p) => p.messages.map((m) => m.id)),
      );
      let ended = false;
      let acked = false;
      const live = () => !ended && !ctrl.signal.aborted;
      const update = (patch: (r: Run) => Partial<Run>) => {
        if (live()) setRun((r) => (r && r.threadId === threadId ? { ...r, ...patch(r) } : r));
      };
      // The one place a send ends: Send re-enables and the in-flight guard releases together.
      const end = (patch: Partial<Run>, failed: boolean) => {
        if (!live()) return;
        ended = true;
        controller.current = null;
        setRun((r) =>
          r && r.threadId === threadId ? { ...r, ...IDLE, ...patch, streaming: false } : r,
        );
        ctrl.abort(); // close the connection; later frames are ignored
        // Once acked the message is stored and visible, so there is nothing to put back.
        if (failed && !acked) onFailed?.(content);
      };
      const fail = (message: string) => end({ error: message }, true);
      setRun({ ...IDLE, threadId, streaming: true, pendingContent: content, baseIds });
      void (async () => {
        try {
          await streamChat(
            threadId,
            content,
            {
              onAck: (message) => {
                if (!live()) return;
                acked = true;
                queryClient.setQueryData<MessagesData>(key, (data) => append(data, message));
                update(() => ({ pendingContent: null, ackId: message.id }));
              },
              onDelta: (text) => update((r) => ({ text: r.text + text, status: null })),
              onStatus: (label) => update(() => ({ status: label })),
              onAction: (action) => update((r) => ({ actions: [...r.actions, action] })),
              onDone: ({ message }) => {
                if (!live()) return;
                queryClient.setQueryData<MessagesData>(key, (data) => append(data, message));
                end({ completed: true }, false);
              },
              onError: (error) => {
                const status = error.code === 'rate_limited' ? 429 : 422;
                fail(describeSendError(new ApiError(status, error.code, error.message)));
              },
            },
            ctrl.signal,
          );
          fail('The reply was interrupted. Please try again.');
        } catch (error) {
          fail(describeSendError(error));
        } finally {
          if (controller.current === ctrl) controller.current = null;
          void queryClient.invalidateQueries({ queryKey: key });
        }
      })();
      return true;
    },
    [queryClient, threadId, userId],
  );

  /** Stops the reply on purpose; the user's message stays. */
  const stop = useCallback(() => {
    controller.current?.abort();
    controller.current = null;
    setRun(null);
  }, []);

  const mine = run && run.threadId === threadId ? run : null;
  const stream: ChatStream = mine ?? IDLE;
  return { send, stop, stream, streaming: mine?.streaming ?? false };
}
