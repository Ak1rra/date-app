import type { Schemas } from './client';
import { CSRF_HEADER, getActiveProfile, readCsrfToken, toApiError } from './http';
import { PROFILE_HEADER } from '../lib/offlineApi';

export type ChatMessageOut = Schemas['ChatMessageOut'];
export type ChatActionOut = Schemas['ChatActionOut'];

/** What the server may say while a reply streams (POST /chat/threads/{id}/messages). */
export interface ChatStreamHandlers {
  /** The stored copy of the message that was just sent. */
  onAck?: (message: ChatMessageOut) => void;
  /** The next piece of the assistant's reply text. */
  onDelta?: (text: string) => void;
  /** What the assistant is doing right now ("Searching nearby places…"). */
  onStatus?: (label: string) => void;
  /** A suggestion the assistant proposes; the user applies it explicitly. */
  onAction?: (action: ChatActionOut) => void;
  /** The reply is complete and stored. */
  onDone?: (done: { message_id: string; message: ChatMessageOut }) => void;
  /** The stream failed after it started. */
  onError?: (error: { code: string; message: string }) => void;
}

export interface SseParser {
  /** Feed the next decoded chunk; it may end anywhere, even between "\r" and "\n". */
  push(chunk: string): void;
  /** The stream ended: handle a final line and event that had no trailing blank line. */
  end(): void;
}

/**
 * Incremental text/event-stream parser: `event:` / `data:` fields, events ended by a blank line,
 * lines ended by \n, \r\n or \r, `:` comments ignored. Multiple `data:` lines join with "\n".
 */
export function createSseParser(onEvent: (event: string, data: string) => void): SseParser {
  let buffer = '';
  let eventName = '';
  let dataLines: string[] = [];

  const dispatch = () => {
    if (dataLines.length > 0) onEvent(eventName || 'message', dataLines.join('\n'));
    eventName = '';
    dataLines = [];
  };

  const handleLine = (line: string) => {
    if (line === '') return dispatch();
    if (line.startsWith(':')) return;
    const colon = line.indexOf(':');
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? '' : line.slice(colon + 1);
    if (value.startsWith(' ')) value = value.slice(1);
    if (field === 'event') eventName = value;
    else if (field === 'data') dataLines.push(value);
  };

  return {
    push(chunk) {
      buffer += chunk;
      for (;;) {
        const match = /\r\n|\n|\r/.exec(buffer);
        if (!match) return;
        // A "\r" at the very end may be the first half of "\r\n": wait for the next chunk.
        if (match[0] === '\r' && match.index === buffer.length - 1) return;
        handleLine(buffer.slice(0, match.index));
        buffer = buffer.slice(match.index + match[0].length);
      }
    },
    end() {
      // A held CR was a complete line terminator after all.
      if (buffer.endsWith('\r')) buffer = buffer.slice(0, -1);
      if (buffer !== '') handleLine(buffer);
      buffer = '';
      dispatch();
    },
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function route(handlers: ChatStreamHandlers, event: string, raw: string) {
  let data: unknown;
  try {
    data = JSON.parse(raw);
  } catch {
    return; // not ours to guess at
  }
  if (!isRecord(data)) return;
  switch (event) {
    case 'ack':
      return handlers.onAck?.(data.message as ChatMessageOut);
    case 'delta':
      return handlers.onDelta?.(String(data.text ?? ''));
    case 'status':
      return handlers.onStatus?.(String(data.label ?? ''));
    case 'action':
      return handlers.onAction?.(data as unknown as ChatActionOut);
    case 'done':
      return handlers.onDone?.(data as unknown as { message_id: string; message: ChatMessageOut });
    case 'error':
      return handlers.onError?.({
        code: typeof data.code === 'string' ? data.code : 'unknown_error',
        message: typeof data.message === 'string' ? data.message : 'Something went wrong',
      });
  }
}

/**
 * POST /chat/threads/{id}/messages and feed the server-sent events to `handlers`. Rejects with an
 * ApiError when the request fails before streaming starts (401/404/422/429 as the usual JSON
 * envelope). Resolves when the stream ends; aborting via `signal` also resolves, quietly.
 */
export async function streamChat(
  threadId: string,
  content: string,
  handlers: ChatStreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const headers = new Headers({ 'Content-Type': 'application/json', Accept: 'text/event-stream' });
  const csrf = readCsrfToken();
  if (csrf) headers.set(CSRF_HEADER, csrf);
  const profile = getActiveProfile();
  if (profile) headers.set(PROFILE_HEADER, profile);

  let reader: ReadableStreamDefaultReader<Uint8Array> | undefined;
  try {
    const res = await fetch(`/api/v1/chat/threads/${encodeURIComponent(threadId)}/messages`, {
      method: 'POST',
      headers,
      body: JSON.stringify({ content }),
      credentials: 'same-origin',
      signal,
    });
    if (!res.ok) throw await toApiError(res);
    if (!res.body) return;

    reader = res.body.getReader();
    const stop = () => void reader?.cancel().catch(() => undefined);
    signal?.addEventListener('abort', stop, { once: true });
    const decoder = new TextDecoder();
    const parser = createSseParser((event, raw) => {
      if (!signal?.aborted) route(handlers, event, raw);
    });
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      parser.push(decoder.decode(value, { stream: true }));
    }
    parser.push(decoder.decode());
    parser.end();
  } catch (error) {
    if (signal?.aborted) return;
    throw error;
  }
}
