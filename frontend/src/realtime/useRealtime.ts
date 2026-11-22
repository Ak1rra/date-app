import { useEffect, useRef, useState } from 'react';
import { useQueryClient, type QueryClient } from '@tanstack/react-query';
import { useViewerId } from '../hooks/useSession';

export type RealtimeStatus = 'connecting' | 'open' | 'closed';

export interface RealtimeMessage {
  event: string;
  data: Record<string, unknown>;
}

export interface MatchCreated {
  ideaId: string;
  title: string;
}

const PING_MS = 25_000;
const BACKOFF_BASE_MS = 1_000;
const BACKOFF_MAX_MS = 30_000;
const JITTER_MS = 1_000;
/** Not signed in / bad origin: reconnecting cannot help until something else changes. */
const NO_RECONNECT_CODES = new Set([4401, 4403]);

/** Delay before reconnect attempt number `attempt` (0-based): 1s, 2s, 4s, ... plus jitter, never above 30s. */
export function backoffDelay(attempt: number): number {
  return Math.min(BACKOFF_MAX_MS, BACKOFF_BASE_MS * 2 ** attempt + Math.random() * JITTER_MS);
}

export function realtimeUrl(): string {
  const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
  return `${scheme}://${location.host}/api/v1/ws`;
}

/** Query-key roots (the first element of the per-viewer keys in hooks/) each event makes stale. */
export function rootsFor(event: string): string[] {
  const roots = ['notifications-unread-count'];
  if (/^(idea|vote|match)\./.test(event)) roots.push('ideas');
  if (event === 'chat.message') roots.push('chat');
  if (event === 'itinerary.updated' || /^date\./.test(event)) {
    roots.push('itinerary', 'itineraries', 'calendar', 'hints');
  }
  switch (event) {
    case 'calendar.changed':
      roots.push('calendar', 'surprises', 'surprise');
      break;
    case 'hint.released':
      roots.push('surprises', 'surprise');
      break;
    case 'surprise.revealed':
      roots.push('surprises', 'surprise', 'itineraries', 'calendar');
      break;
  }
  return roots;
}

function invalidateFor(queryClient: QueryClient, event: string) {
  for (const root of rootsFor(event)) {
    void queryClient.invalidateQueries({ queryKey: [root] });
  }
}

export function parseMessage(raw: unknown): RealtimeMessage | null {
  if (typeof raw !== 'string') return null;
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object') return null;
    const { event, data } = parsed as { event?: unknown; data?: unknown };
    if (typeof event !== 'string') return null;
    const body = data && typeof data === 'object' ? (data as Record<string, unknown>) : {};
    return { event, data: body };
  } catch {
    return null; // "pong" is plain text
  }
}

/**
 * One WebSocket to /api/v1/ws while somebody is signed in. Server events refresh the matching
 * queries; a dropped socket reconnects with backoff and, once back, refreshes everything because
 * events may have been missed. Does nothing where WebSocket does not exist.
 */
export function useRealtime(onMatch: (match: MatchCreated) => void): RealtimeStatus {
  const queryClient = useQueryClient();
  const viewerId = useViewerId();
  const [status, setStatus] = useState<RealtimeStatus>('closed');
  const onMatchRef = useRef(onMatch);

  useEffect(() => {
    onMatchRef.current = onMatch;
  });

  useEffect(() => {
    if (!viewerId || typeof WebSocket === 'undefined') return;

    let socket: WebSocket | null = null;
    let stopped = false;
    let attempt = 0;
    let hasOpened = false;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    let pingTimer: ReturnType<typeof setInterval> | undefined;

    const connect = () => {
      setStatus('connecting');
      const ws = new WebSocket(realtimeUrl());
      socket = ws;

      ws.onopen = () => {
        attempt = 0;
        setStatus('open');
        // The first open has nothing to catch up on; after a reconnect, anything may have changed.
        if (hasOpened) void queryClient.invalidateQueries();
        hasOpened = true;
        pingTimer = setInterval(() => ws.send('ping'), PING_MS);
      };

      ws.onmessage = (message: MessageEvent) => {
        const parsed = parseMessage(message.data);
        if (!parsed) return;
        invalidateFor(queryClient, parsed.event);
        if (parsed.event === 'match.created') {
          const { idea_id: ideaId, title } = parsed.data;
          if (typeof ideaId === 'string' && typeof title === 'string') {
            onMatchRef.current({ ideaId, title });
          }
        }
      };

      ws.onclose = (close: CloseEvent) => {
        clearInterval(pingTimer);
        if (stopped) return;
        setStatus('closed');
        if (NO_RECONNECT_CODES.has(close.code)) return;
        retryTimer = setTimeout(connect, backoffDelay(attempt));
        attempt += 1;
      };
    };

    connect();

    return () => {
      stopped = true;
      clearTimeout(retryTimer);
      clearInterval(pingTimer);
      if (socket) {
        socket.onclose = null;
        socket.close();
      }
    };
  }, [viewerId, queryClient]);

  return viewerId ? status : 'closed';
}
