import { act, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SESSION_KEY } from '../hooks/useSession';
import { FakeWebSocket } from '../test/fakeWebSocket';
import { signedIn } from '../test/renderApp';
import { RealtimeProvider, useMatchCelebration, useRealtimeStatus } from './RealtimeProvider';

function Probe() {
  const status = useRealtimeStatus();
  const celebration = useMatchCelebration();
  return (
    <>
      <p>status:{status}</p>
      <button onClick={() => celebration.celebrate('i1', 'Pottery night')}>vote match</button>
    </>
  );
}

function setup(signedInUser = true) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  if (signedInUser) queryClient.setQueryData(SESSION_KEY, signedIn);
  const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
  const view = render(
    <QueryClientProvider client={queryClient}>
      <RealtimeProvider>
        <Probe />
      </RealtimeProvider>
    </QueryClientProvider>,
  );
  const socket = () => FakeWebSocket.instances[FakeWebSocket.instances.length - 1]!;
  return { queryClient, invalidate, socket, view };
}

const roots = (spy: ReturnType<typeof setup>['invalidate']) =>
  spy.mock.calls.map(([filters]) => filters?.queryKey?.[0]);

const match = (id: string, title: string) => ({
  event: 'match.created',
  data: { idea_id: id, title },
});

beforeEach(() => {
  vi.useFakeTimers();
  vi.spyOn(Math, 'random').mockReturnValue(0);
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('RealtimeProvider', () => {
  it('connects to the same-origin socket only while somebody is signed in', () => {
    const { socket } = setup();
    expect(socket().url).toBe(`ws://${location.host}/api/v1/ws`);
    expect(screen.getByText('status:connecting')).toBeInTheDocument();
    act(() => socket().open());
    expect(screen.getByText('status:open')).toBeInTheDocument();
  });

  it('opens no socket when signed out', () => {
    setup(false);
    expect(FakeWebSocket.instances).toHaveLength(0);
    expect(screen.getByText('status:closed')).toBeInTheDocument();
  });

  it('does nothing where WebSocket does not exist', () => {
    // @ts-expect-error simulating an environment without WebSocket
    delete globalThis.WebSocket;
    setup();
    expect(screen.getByText('status:closed')).toBeInTheDocument();
  });

  it.each([
    ['idea.created', { id: 'i1' }, ['ideas']],
    ['vote.updated', { idea_id: 'i1' }, ['ideas']],
    ['itinerary.updated', { id: 'it1' }, ['itinerary', 'itineraries', 'calendar', 'hints']],
    ['date.scheduled', { id: 'it1' }, ['itinerary', 'itineraries', 'calendar', 'hints']],
    ['date.cancelled', { id: 'it1' }, ['itinerary', 'itineraries', 'calendar', 'hints']],
    ['date.rescheduled', { id: 'it1' }, ['itinerary', 'itineraries', 'calendar', 'hints']],
    [
      'date.completed',
      { id: 'it1' },
      ['itinerary', 'itineraries', 'calendar', 'hints', 'past-dates', 'ratings'],
    ],
    ['rating.revealed', { itinerary_id: 'it1' }, ['ratings', 'past-dates']],
    ['calendar.changed', {}, ['calendar', 'surprises', 'surprise']],
    ['hint.released', {}, ['surprises', 'surprise']],
    ['surprise.revealed', { id: 's1' }, ['surprises', 'surprise', 'itineraries', 'calendar']],
  ])('%s refreshes the right queries', (event, data, expected) => {
    const { socket, invalidate } = setup();
    act(() => socket().open());
    act(() => socket().receive({ event, data }));
    const seen = roots(invalidate);
    for (const root of [...expected, 'notifications-unread-count']) expect(seen).toContain(root);
    if (!expected.includes('ideas')) expect(seen).not.toContain('ideas');
  });

  it('ignores pong and malformed messages', () => {
    const { socket, invalidate } = setup();
    act(() => socket().open());
    act(() => socket().receive('pong'));
    act(() => socket().receive('{not json'));
    expect(invalidate).not.toHaveBeenCalled();
  });

  it('reconnects with exponential backoff and refreshes everything once back', () => {
    const { socket, invalidate } = setup();
    act(() => socket().open());
    expect(invalidate).not.toHaveBeenCalled(); // the first open has nothing to catch up on

    act(() => socket().drop());
    expect(FakeWebSocket.instances).toHaveLength(1);
    act(() => void vi.advanceTimersByTime(999));
    expect(FakeWebSocket.instances).toHaveLength(1);
    act(() => void vi.advanceTimersByTime(1));
    expect(FakeWebSocket.instances).toHaveLength(2);

    // Still failing: 2s, then 4s.
    act(() => socket().drop());
    act(() => void vi.advanceTimersByTime(1999));
    expect(FakeWebSocket.instances).toHaveLength(2);
    act(() => void vi.advanceTimersByTime(1));
    expect(FakeWebSocket.instances).toHaveLength(3);
    act(() => socket().drop());
    act(() => void vi.advanceTimersByTime(4000));
    expect(FakeWebSocket.instances).toHaveLength(4);

    act(() => socket().open());
    expect(invalidate).toHaveBeenCalledTimes(1);
    expect(invalidate).toHaveBeenCalledWith(); // no filter: everything

    // A successful open resets the backoff to 1s.
    act(() => socket().drop());
    act(() => void vi.advanceTimersByTime(1000));
    expect(FakeWebSocket.instances).toHaveLength(5);
  });

  it('caps the backoff at 30 seconds', () => {
    const { socket } = setup();
    for (let i = 0; i < 8; i++) {
      act(() => socket().drop());
      act(() => void vi.advanceTimersByTime(30_000));
    }
    const count = FakeWebSocket.instances.length;
    act(() => socket().drop());
    act(() => void vi.advanceTimersByTime(30_000));
    expect(FakeWebSocket.instances).toHaveLength(count + 1);
  });

  it.each([4401, 4403])('does not reconnect after close code %i', (code) => {
    const { socket } = setup();
    act(() => socket().open());
    act(() => socket().drop(code));
    act(() => void vi.advanceTimersByTime(120_000));
    expect(FakeWebSocket.instances).toHaveLength(1);
    expect(screen.getByText('status:closed')).toBeInTheDocument();
  });

  it('pings every 25 seconds while open and stops after unmount', () => {
    const { socket, view } = setup();
    const ws = socket();
    act(() => ws.open());
    act(() => void vi.advanceTimersByTime(24_999));
    expect(ws.sent).toEqual([]);
    act(() => void vi.advanceTimersByTime(1));
    expect(ws.sent).toEqual(['ping']);
    act(() => void vi.advanceTimersByTime(25_000));
    expect(ws.sent).toEqual(['ping', 'ping']);

    view.unmount();
    expect(ws.closed).toBe(true);
    act(() => void vi.advanceTimersByTime(60_000));
    expect(ws.sent).toHaveLength(2);
  });

  it('closes the socket and opens a new one when the viewer changes', () => {
    const { socket, queryClient } = setup();
    const first = socket();
    act(() => first.open());
    act(() =>
      queryClient.setQueryData(SESSION_KEY, {
        ...signedIn,
        user: { ...signedIn.user!, id: 'u2' },
      }),
    );
    act(() => void vi.advanceTimersByTime(0)); // let the query cache notify its observers
    expect(first.closed).toBe(true);
    expect(FakeWebSocket.instances).toHaveLength(2);
  });

  it('shows the match celebration once, whichever of the vote and the socket gets there first', () => {
    const { socket } = setup();
    act(() => socket().open());
    act(() => socket().receive(match('i1', 'Pottery night')));
    expect(screen.getAllByRole('dialog')).toHaveLength(1);
    expect(screen.getByText('Pottery night')).toBeInTheDocument();

    // The local vote's response reports the same match: no second overlay, even after dismissal.
    act(() => screen.getByRole('button', { name: 'vote match' }).click());
    expect(screen.getAllByRole('dialog')).toHaveLength(1);
    act(() => screen.getByRole('button', { name: 'Keep swiping' }).click());
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    act(() => socket().receive(match('i1', 'Pottery night')));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

    // A different match is celebrated.
    act(() => socket().receive(match('i2', 'Hike')));
    expect(screen.getByText('Hike')).toBeInTheDocument();
  });
});
