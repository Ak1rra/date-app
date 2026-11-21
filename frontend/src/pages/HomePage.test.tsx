import { screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { ItineraryOut, ItinerarySummary } from '../hooks/useItineraries';
import { countdownLabel, nextOccurrence } from '../hooks/useHome';
import type { ImportantDateOut } from '../hooks/useImportantDates';
import type { SurpriseOut } from '../hooks/useSurpriseView';
import { mockApi } from '../test/mockApi';
import { mockSession, renderApp, signedIn } from '../test/renderApp';

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

const scheduledDate: ItinerarySummary = {
  id: 'i1',
  title: 'Moonlit dinner',
  status: 'scheduled',
  starts_at: '2026-11-22T18:00:00Z',
  ends_at: '2026-11-22T21:00:00Z',
  stop_count: 2,
  announced: true,
  is_planner: true,
  is_surprise: false,
};

function setupHome({
  itineraries = [],
  dates = [],
  details = {},
  surprises,
}: {
  itineraries?: ItinerarySummary[];
  dates?: ImportantDateOut[];
  details?: Record<string, ItineraryOut>;
  surprises?: SurpriseOut[];
} = {}) {
  return mockApi({
    'GET /api/v1/auth/session': signedIn,
    'GET /api/v1/itineraries': itineraries,
    ...(surprises ? { 'GET /api/v1/surprises': surprises } : {}),
    'GET /api/v1/couple/important-dates': dates,
    ...Object.fromEntries(
      Object.entries(details).map(([id, detail]) => [`GET /api/v1/itineraries/${id}`, detail]),
    ),
  });
}

describe('Home date helpers', () => {
  it('finds this year and next year recurring occurrences', () => {
    const now = new Date(2026, 5, 15, 12);
    expect(nextOccurrence({ date: '2026-08-20', recurring: true }, now)).toEqual(
      new Date(2026, 7, 20),
    );
    expect(nextOccurrence({ date: '2026-06-01', recurring: true }, now)).toEqual(
      new Date(2027, 5, 1),
    );
  });

  it('moves February 29 to February 28 in a non-leap year', () => {
    expect(nextOccurrence({ date: '2024-02-29', recurring: true }, new Date(2027, 0, 1))).toEqual(
      new Date(2027, 1, 28),
    );
  });

  it('excludes a non-recurring date in the past', () => {
    expect(
      nextOccurrence({ date: '2026-06-01', recurring: false }, new Date(2026, 5, 15)),
    ).toBeNull();
  });

  it('keeps a non-recurring date that is today', () => {
    expect(
      nextOccurrence({ date: '2026-06-15', recurring: false }, new Date(2026, 5, 15, 18)),
    ).toEqual(new Date(2026, 5, 15));
  });

  it('labels tonight, tomorrow and later countdowns', () => {
    const now = new Date(2026, 10, 21, 12);
    expect(countdownLabel(new Date(2026, 10, 21, 20), now)).toBe('Tonight');
    expect(countdownLabel(new Date(2026, 10, 22, 20), now)).toBe('Tomorrow');
    expect(countdownLabel(new Date(2026, 10, 25, 20), now)).toBe('In 4 days');
  });
});

describe('HomePage', () => {
  it('leads with a "Plan a date" action that links to /plan', async () => {
    mockSession(signedIn);
    renderApp('/');

    const plan = await screen.findByRole('link', { name: 'Plan a date' });
    expect(plan).toHaveAttribute('href', '/plan');

    const main = screen.getByRole('main');
    const firstAction = main.querySelector<HTMLElement>('a[href], button');
    expect(firstAction).toBe(plan);
  });

  it('shows teaching empty states for the three Home cards', async () => {
    mockSession(signedIn);
    renderApp('/');
    for (const name of ['Next date', 'Finishing touches due', 'Upcoming occasions']) {
      expect(await screen.findByRole('region', { name })).toBeInTheDocument();
    }
  });

  it('lists upcoming surprises for the surprisee, first in the grid', async () => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date('2026-11-21T12:00:00Z'));
    setupHome({
      surprises: [
        {
          id: 's1',
          starts_at: '2026-11-23T18:00:00Z',
          ends_at: null,
          reveal_at: null,
          hints: [
            { id: 'h1', kind: 'bring', text: 'A scarf', released_at: '2026-11-20T10:00:00Z' },
            {
              id: 'h2',
              kind: 'eat',
              text: 'Bring an appetite',
              released_at: '2026-11-21T10:00:00Z',
            },
          ],
        },
      ],
    });
    renderApp('/');

    const card = await screen.findByRole('region', { name: 'A surprise for you ✨' });
    expect(within(card).getByRole('link', { name: /A surprise is coming/ })).toHaveAttribute(
      'href',
      '/surprise/s1',
    );
    expect(within(card).getByText('In 2 days')).toBeInTheDocument();
    expect(within(card).getByText('2 hints so far')).toBeInTheDocument();
    const regions = screen.getAllByRole('region');
    expect(regions[0]).toBe(card);
  });

  it('has no surprise card without surprises, even when the list fails to load', async () => {
    setupHome({ surprises: [] });
    renderApp('/');
    expect(await screen.findByRole('region', { name: 'Next date' })).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'A surprise for you ✨' })).not.toBeInTheDocument();
  });

  it('shows the soonest scheduled date and links to it', async () => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date('2026-11-21T12:00:00Z'));
    setupHome({
      itineraries: [
        { ...scheduledDate, id: 'later', title: 'Later date', starts_at: '2026-11-25T18:00:00Z' },
        scheduledDate,
      ],
    });
    renderApp('/');

    const link = await screen.findByRole('link', { name: /Moonlit dinner/ });
    expect(link).toHaveAttribute('href', '/itineraries/i1');
  });

  it('ignores draft and past dates', async () => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date('2026-11-21T12:00:00Z'));
    setupHome({
      itineraries: [
        { ...scheduledDate, id: 'draft', title: 'Draft date', status: 'draft' },
        { ...scheduledDate, id: 'past', title: 'Past date', starts_at: '2026-11-20T18:00:00Z' },
      ],
    });
    renderApp('/');

    expect(await screen.findByText('Nothing planned yet')).toBeInTheDocument();
    expect(screen.queryByText('Draft date')).not.toBeInTheDocument();
    expect(screen.queryByText('Past date')).not.toBeInTheDocument();
  });

  it('shows the private surprise badge', async () => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date('2026-11-21T12:00:00Z'));
    setupHome({ itineraries: [{ ...scheduledDate, is_surprise: true }] });
    renderApp('/');

    expect(await screen.findByText('Only you can see this')).toBeInTheDocument();
  });

  it('lists due unticked finishing touches and omits done or late ones', async () => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date('2026-11-21T12:00:00Z'));
    const detail = {
      id: 'i1',
      title: scheduledDate.title,
      checklist: [
        { id: 'keep', text: 'Reserve the table', done: false, due_at: '2026-11-24T12:00:00Z' },
        { id: 'done', text: 'Already handled', done: true, due_at: '2026-11-23T12:00:00Z' },
        { id: 'late', text: 'Book the flowers', done: false, due_at: '2026-12-01T12:00:00Z' },
      ],
    } as ItineraryOut;
    setupHome({ itineraries: [scheduledDate], details: { i1: detail } });
    renderApp('/');

    expect(await screen.findByText('Reserve the table')).toBeInTheDocument();
    expect(screen.queryByText('Already handled')).not.toBeInTheDocument();
    expect(screen.queryByText('Book the flowers')).not.toBeInTheDocument();
  });

  it('lists a nearby birthday but omits distant occasions', async () => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date('2026-11-21T12:00:00Z'));
    setupHome({
      dates: [
        {
          id: 'birthday',
          name: 'Alex birthday',
          kind: 'birthday',
          date: '2026-12-11',
          recurring: false,
          profile_id: 'u1',
        },
        {
          id: 'far',
          name: 'Far away day',
          kind: 'custom',
          date: '2027-02-19',
          recurring: false,
          profile_id: null,
        },
      ],
    });
    renderApp('/');

    expect(await screen.findByText('Alex birthday')).toBeInTheDocument();
    expect(screen.queryByText('Far away day')).not.toBeInTheDocument();
  });

  it('shows each card empty state when there is no data', async () => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date('2026-11-21T12:00:00Z'));
    setupHome();
    renderApp('/');

    expect(await screen.findByText('Nothing planned yet')).toBeInTheDocument();
    expect(await screen.findByText('All caught up')).toBeInTheDocument();
    expect(await screen.findByText('No occasions yet')).toBeInTheDocument();
  });
});
