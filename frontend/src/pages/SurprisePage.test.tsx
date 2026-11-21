import { act, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mockApi } from '../test/mockApi';
import { jsonResponse, renderApp, signedIn } from '../test/renderApp';

const surprise = {
  id: 's1',
  starts_at: '2026-11-23T15:30:00Z',
  ends_at: '2026-11-23T18:00:00Z',
  reveal_at: '2026-11-23T15:30:00Z',
  hints: [
    { id: 'h1', kind: 'dress_code', text: 'Something cosy', released_at: '2026-11-20T10:00:00Z' },
    { id: 'h2', kind: 'bring', text: 'A scarf', released_at: '2026-11-21T09:00:00Z' },
  ],
};

const notFound = () =>
  jsonResponse({ error: { code: 'not_found', message: 'No such surprise.' } }, 404);

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date('2026-11-21T12:00:00Z'));
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('SurprisePage', () => {
  it('shows the countdown and the released hints, newest first', async () => {
    mockApi({
      'GET /api/v1/auth/session': signedIn,
      'GET /api/v1/surprises/s1': surprise,
    });
    renderApp('/surprise/s1');

    expect(
      await screen.findByRole('heading', { name: 'A surprise is coming ✨' }),
    ).toBeInTheDocument();
    expect(screen.getByText('2 days · 3 hours · 30 minutes')).toBeInTheDocument();
    expect(screen.getByText(/until about/)).toBeInTheDocument();
    expect(screen.queryByText(/All will be revealed/)).not.toBeInTheDocument();

    const items = within(screen.getByRole('main')).getAllByRole('listitem');
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent('Bring along');
    expect(items[0]).toHaveTextContent('A scarf');
    expect(items[1]).toHaveTextContent('What to wear');
    expect(items[1]).toHaveTextContent('Something cosy');
  });

  it('says when everything will be revealed if that is later than the start', async () => {
    mockApi({
      'GET /api/v1/auth/session': signedIn,
      'GET /api/v1/surprises/s1': { ...surprise, reveal_at: '2026-11-23T17:00:00Z' },
    });
    renderApp('/surprise/s1');

    expect(await screen.findByText(/All will be revealed/)).toBeInTheDocument();
  });

  it('says "It\'s time!" once the surprise has started', async () => {
    mockApi({
      'GET /api/v1/auth/session': signedIn,
      'GET /api/v1/surprises/s1': { ...surprise, starts_at: '2026-11-21T11:00:00Z' },
    });
    renderApp('/surprise/s1');

    expect(await screen.findByText("It's time!")).toBeInTheDocument();
  });

  it('teaches the empty state when no hints are out yet', async () => {
    mockApi({
      'GET /api/v1/auth/session': signedIn,
      'GET /api/v1/surprises/s1': { ...surprise, hints: [] },
    });
    renderApp('/surprise/s1');

    expect(
      await screen.findByText('No hints yet — your partner is keeping you guessing.'),
    ).toBeInTheDocument();
  });

  it('sends the surprisee to the itinerary once the surprise is revealed', async () => {
    mockApi({
      'GET /api/v1/auth/session': signedIn,
      'GET /api/v1/surprises/s1': notFound,
      'GET /api/v1/itineraries/s1': {
        id: 's1',
        title: 'Rooftop reveal',
        stops: [],
        checklist: [],
        warnings: [],
        weather: {},
        attributions: [],
      },
    });
    renderApp('/surprise/s1');

    expect(await screen.findByRole('heading', { name: 'Rooftop reveal' })).toBeInTheDocument();
    expect(screen.queryByText("This surprise isn't here")).not.toBeInTheDocument();
  });

  it('shows an empty state when the surprise is not there and the itinerary is hidden', async () => {
    mockApi({
      'GET /api/v1/auth/session': signedIn,
      'GET /api/v1/surprises/s1': notFound,
      'GET /api/v1/itineraries/s1': notFound,
    });
    renderApp('/surprise/s1');

    expect(await screen.findByText("This surprise isn't here")).toBeInTheDocument();
  });

  it('does not say "It\'s time!" in the last minute before the start', async () => {
    mockApi({
      'GET /api/v1/auth/session': signedIn,
      'GET /api/v1/surprises/s1': { ...surprise, starts_at: '2026-11-21T12:00:30Z' },
    });
    renderApp('/surprise/s1');

    expect(await screen.findByText('0 days · 0 hours · 1 minute')).toBeInTheDocument();
    expect(screen.queryByText("It's time!")).not.toBeInTheDocument();
  });

  it('refetches once the reveal passes while the page is open, then redirects', async () => {
    vi.useRealTimers();
    vi.useFakeTimers({ toFake: ['Date', 'setInterval', 'clearInterval'] });
    vi.setSystemTime(new Date('2026-11-21T12:00:00Z'));
    let revealed = false;
    mockApi({
      'GET /api/v1/auth/session': signedIn,
      'GET /api/v1/surprises/s1': () =>
        revealed
          ? notFound()
          : { ...surprise, starts_at: '2026-11-21T12:10:00Z', reveal_at: '2026-11-21T12:01:00Z' },
      'GET /api/v1/itineraries/s1': {
        id: 's1',
        title: 'Rooftop reveal',
        stops: [],
        checklist: [],
        warnings: [],
        weather: {},
        attributions: [],
      },
    });
    renderApp('/surprise/s1');

    expect(
      await screen.findByRole('heading', { name: 'A surprise is coming ✨' }),
    ).toBeInTheDocument();
    revealed = true;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(await screen.findByRole('heading', { name: 'Rooftop reveal' })).toBeInTheDocument();
  });

  it('offers a retry, not "isn\'t here", when the itinerary check fails', async () => {
    mockApi({
      'GET /api/v1/auth/session': signedIn,
      'GET /api/v1/surprises/s1': notFound,
      'GET /api/v1/itineraries/s1': () =>
        jsonResponse({ error: { code: 'internal_error', message: 'Boom.' } }, 500),
    });
    renderApp('/surprise/s1');

    // The itinerary query keeps its own retry policy, so a 500 settles after its backoff.
    expect(
      await screen.findByRole('button', { name: 'Try again' }, { timeout: 10_000 }),
    ).toBeInTheDocument();
    expect(screen.queryByText("This surprise isn't here")).not.toBeInTheDocument();
  });
});
