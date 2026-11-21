import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mockApi, type Handler } from '../test/mockApi';
import { jsonResponse, renderApp } from '../test/renderApp';

const session = {
  authenticated: true,
  user: {
    id: 'u1',
    email: 'me@example.com',
    display_name: 'Sam',
    timezone: 'UTC',
    couple_id: 'c1',
    role: 'planner',
  },
};

/** A local wall-clock time as the ISO string the API sends. */
const at = (y: number, m: number, d: number, h = 0, min = 0) =>
  new Date(y, m - 1, d, h, min).toISOString();

const entries = [
  {
    id: 'e1',
    kind: 'date',
    title: 'Sushi night',
    starts_at: at(2026, 11, 20, 19),
    ends_at: at(2026, 11, 20, 22),
    all_day: false,
    announced: true,
    is_surprise: false,
    status: 'scheduled',
    itinerary_id: 'i1',
  },
  {
    id: 'e2',
    kind: 'date',
    title: 'Cancelled movie',
    starts_at: at(2026, 11, 20, 20),
    ends_at: at(2026, 11, 20, 22),
    all_day: false,
    announced: true,
    status: 'cancelled',
    itinerary_id: 'i3',
  },
  {
    id: 'e3',
    kind: 'date',
    title: 'Late dessert',
    starts_at: at(2026, 11, 20, 22),
    ends_at: null,
    all_day: false,
  },
  {
    id: 'e4',
    kind: 'occasion',
    title: 'Alex birthday',
    starts_at: at(2026, 11, 22),
    ends_at: at(2026, 11, 23),
    all_day: true,
  },
  {
    id: 'e5',
    kind: 'date',
    title: 'Secret rooftop',
    starts_at: at(2026, 11, 25, 18),
    ends_at: at(2026, 11, 25, 21),
    all_day: false,
    announced: false,
    is_surprise: true,
    status: 'scheduled',
    itinerary_id: 'i2',
  },
  {
    id: 'e6',
    kind: 'date',
    title: 'Christmas dinner',
    starts_at: at(2026, 12, 25, 18),
    ends_at: at(2026, 12, 25, 21),
    all_day: false,
    announced: true,
    status: 'scheduled',
    itinerary_id: 'i9',
  },
];

const slots = [
  {
    starts_at: at(2026, 11, 27, 19),
    ends_at: at(2026, 11, 27, 22),
    reason: 'Both of you are free on Friday evening',
  },
  {
    starts_at: at(2026, 11, 28, 14),
    ends_at: at(2026, 11, 28, 17),
    reason: 'A free Saturday afternoon',
  },
];

function setup(routes: Record<string, Handler> = {}) {
  return mockApi({
    'GET /api/v1/auth/session': session,
    'GET /api/v1/calendar': entries,
    'POST /api/v1/calendar/suggest-times': { slots },
    'GET /api/v1/itineraries/i1': { id: 'i1', title: 'Sushi night' },
    'GET /api/v1/itineraries/i2': { id: 'i2', title: 'Secret rooftop' },
    ...routes,
  });
}

const monthLabel = (y: number, m: number) =>
  new Intl.DateTimeFormat(undefined, { month: 'long', year: 'numeric' }).format(
    new Date(y, m - 1, 1),
  );
const dayCell = (y: number, m: number, d: number) =>
  screen.getByRole('button', {
    name: new Intl.DateTimeFormat(undefined, { dateStyle: 'full' }).format(new Date(y, m - 1, d)),
  });

/** The `from`/`to` of every GET /calendar so far. */
function calendarRanges(api: ReturnType<typeof setup>) {
  return api.fetchMock.mock.calls
    .map(
      ([input]) =>
        new URL(input instanceof Request ? input.url : String(input), 'http://localhost'),
    )
    .filter((url) => url.pathname === '/api/v1/calendar')
    .map((url) => [url.searchParams.get('from'), url.searchParams.get('to')]);
}

beforeEach(() => {
  // Only Date is faked, so timers, waitFor and userEvent keep working.
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date(2026, 10, 18, 12, 0));
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('CalendarPage', () => {
  it('shows the visible month with entries on the right days', async () => {
    const api = setup();
    renderApp('/calendar');
    expect(await screen.findByRole('heading', { name: monthLabel(2026, 11) })).toBeInTheDocument();

    const sushiDay = dayCell(2026, 11, 20);
    await within(sushiDay).findByText('Sushi night');
    expect(within(sushiDay).getByText('Cancelled movie')).toHaveClass('line-through');
    expect(within(sushiDay).queryByText('Late dessert')).not.toBeInTheDocument();
    expect(within(sushiDay).getByText('+1')).toBeInTheDocument();
    expect(within(dayCell(2026, 11, 22)).getByText('Alex birthday')).toBeInTheDocument();
    expect(within(dayCell(2026, 11, 21)).queryByText('Sushi night')).not.toBeInTheDocument();
    expect(dayCell(2026, 11, 18)).toHaveAttribute('aria-current', 'date');

    // Monday-first: November 2026 starts on a Sunday, so the grid begins on Monday 26 October.
    expect(calendarRanges(api)).toContainEqual([
      at(2026, 10, 26),
      new Date(2026, 11, 7).toISOString(),
    ]);
  });

  it('moves between months and refetches with the new range', async () => {
    const api = setup();
    renderApp('/calendar');
    await within(await screen.findByRole('button', { name: /November 20/ })).findByText(
      'Sushi night',
    );

    await userEvent.click(screen.getByRole('button', { name: 'Next month' }));
    expect(await screen.findByRole('heading', { name: monthLabel(2026, 12) })).toBeInTheDocument();
    await within(dayCell(2026, 12, 25)).findByText('Christmas dinner');
    expect(calendarRanges(api)).toContainEqual([
      at(2026, 11, 30),
      new Date(2027, 0, 4).toISOString(),
    ]);

    await userEvent.click(screen.getByRole('button', { name: 'Previous month' }));
    await userEvent.click(screen.getByRole('button', { name: 'Previous month' }));
    expect(await screen.findByRole('heading', { name: monthLabel(2026, 10) })).toBeInTheDocument();
    await waitFor(() =>
      expect(calendarRanges(api)).toContainEqual([
        at(2026, 9, 28),
        new Date(2026, 10, 2).toISOString(),
      ]),
    );
  });

  it('lists the upcoming entries, links dates to their itinerary, and shows all-day occasions', async () => {
    setup();
    renderApp('/calendar');
    const agenda = await screen.findByRole('region', { name: 'Agenda' });
    const link = await within(agenda).findByRole('link', { name: 'Sushi night' });
    expect(link).toHaveAttribute('href', '/itineraries/i1');
    expect(within(agenda).getByText(/All day/)).toBeInTheDocument();
    expect(within(agenda).queryByText('Christmas dinner')).not.toBeInTheDocument();
  });

  it('links a surprise for the viewer to its surprise page, with no private badge', async () => {
    setup({
      'GET /api/v1/calendar': [
        ...entries,
        {
          id: 'e7',
          kind: 'date',
          title: 'Mystery evening',
          starts_at: at(2026, 11, 26, 18),
          ends_at: null,
          all_day: false,
          announced: false,
          is_surprise: true,
          status: 'scheduled',
          itinerary_id: 's9',
        },
      ],
      'GET /api/v1/surprises': [
        { id: 's9', starts_at: at(2026, 11, 26, 18), ends_at: null, reveal_at: null, hints: [] },
      ],
    });
    renderApp('/calendar');
    const agenda = await screen.findByRole('region', { name: 'Agenda' });

    const mystery = await within(agenda).findByRole('link', { name: 'Mystery evening' });
    expect(mystery).toHaveAttribute('href', '/surprise/s9');
    const mysteryItem = mystery.closest('li')!;
    expect(within(mysteryItem).getByText('Surprise ✨')).toBeInTheDocument();
    expect(within(mysteryItem).queryByText('Only you can see this')).not.toBeInTheDocument();

    // The planner's own surprise is unchanged.
    const own = within(agenda).getByRole('link', { name: 'Secret rooftop' });
    expect(own).toHaveAttribute('href', '/itineraries/i2');
    const ownItem = own.closest('li')!;
    expect(within(ownItem).getByText('Only you can see this')).toBeInTheDocument();
    expect(within(ownItem).queryByText('Surprise ✨')).not.toBeInTheDocument();
  });

  it('treats a failed surprises list as empty: no surprise link or pill', async () => {
    setup({
      'GET /api/v1/calendar': [
        ...entries,
        {
          id: 'e7',
          kind: 'date',
          title: 'Mystery evening',
          starts_at: at(2026, 11, 26, 18),
          ends_at: null,
          all_day: false,
          announced: false,
          is_surprise: true,
          status: 'scheduled',
          itinerary_id: 's9',
        },
      ],
      'GET /api/v1/surprises': () =>
        jsonResponse({ error: { code: 'internal_error', message: 'Boom.' } }, 500),
    });
    renderApp('/calendar');
    const agenda = await screen.findByRole('region', { name: 'Agenda' });

    const mystery = await within(agenda).findByRole('link', { name: 'Mystery evening' });
    expect(mystery).toHaveAttribute('href', '/itineraries/s9');
    expect(within(agenda).queryByText('Surprise ✨')).not.toBeInTheDocument();
  });

  it('shows the entries of a clicked day, with the surprise badge', async () => {
    setup();
    renderApp('/calendar');
    await within(await screen.findByRole('button', { name: /November 25/ })).findByText(
      'Secret rooftop',
    );

    await userEvent.click(dayCell(2026, 11, 25));
    const agenda = screen.getByRole('region', { name: 'Agenda' });
    expect(within(agenda).getByRole('link', { name: 'Secret rooftop' })).toHaveAttribute(
      'href',
      '/itineraries/i2',
    );
    expect(within(agenda).getByText('Only you can see this')).toBeInTheDocument();
    expect(within(agenda).queryByText('Sushi night')).not.toBeInTheDocument();

    await userEvent.click(dayCell(2026, 11, 21));
    expect(within(agenda).getByText('Nothing planned here.')).toBeInTheDocument();
  });

  it('prefetches itineraries for dates in the next 30 days', async () => {
    const api = setup();
    renderApp('/calendar');
    await waitFor(() => expect(api.find('GET', '/api/v1/itineraries/i1')).toHaveLength(1));
    expect(api.find('GET', '/api/v1/itineraries/i2')).toHaveLength(1);
    // Cancelled, past the 30 days, and without an itinerary: none of these are fetched.
    expect(api.find('GET', '/api/v1/itineraries/i3')).toHaveLength(0);
    expect(api.find('GET', '/api/v1/itineraries/i9')).toHaveLength(0);
  });
});

describe('Suggest a time', () => {
  it('posts the defaults and renders the slots with Plan this links', async () => {
    const api = setup();
    renderApp('/calendar');
    await userEvent.click(await screen.findByRole('button', { name: 'Find free time' }));

    expect(await screen.findByText('Both of you are free on Friday evening')).toBeInTheDocument();
    expect(api.body('POST', '/api/v1/calendar/suggest-times')).toEqual({
      duration_minutes: 180,
      weeks: 3,
      best_time: 'any',
    });
    const links = screen.getAllByRole('link', { name: 'Plan this' });
    expect(links[0]).toHaveAttribute('href', '/plan?date=2026-11-27&start=19:00&end=22:00');
    expect(links[1]).toHaveAttribute('href', '/plan?date=2026-11-28&start=14:00&end=17:00');
  });

  it('sends the chosen options and the idea from the URL', async () => {
    const api = setup();
    renderApp('/calendar?idea=idea-7');
    await userEvent.selectOptions(await screen.findByLabelText('How long'), '120');
    await userEvent.selectOptions(screen.getByLabelText('When'), 'evening');
    await userEvent.selectOptions(screen.getByLabelText('Look ahead (weeks)'), '5');
    await userEvent.click(screen.getByRole('button', { name: 'Find free time' }));

    await screen.findAllByRole('button', { name: /^Make it a date/ });
    expect(screen.queryByRole('link', { name: 'Plan this' })).not.toBeInTheDocument();
    expect(api.body('POST', '/api/v1/calendar/suggest-times')).toEqual({
      duration_minutes: 120,
      weeks: 5,
      best_time: 'evening',
      idea_id: 'idea-7',
    });
  });

  it('makes the idea a date in the chosen slot and opens the itinerary', async () => {
    const api = setup({
      'POST /api/v1/ideas/idea-7/itinerary': jsonResponse({ id: 'new-it' }, 201),
      'GET /api/v1/itineraries/new-it': { id: 'new-it', title: 'Fresh plan' },
    });
    const router = renderApp('/calendar?idea=idea-7');
    await userEvent.click(await screen.findByRole('button', { name: 'Find free time' }));
    const buttons = await screen.findAllByRole('button', { name: /^Make it a date/ });
    await userEvent.click(buttons[1]!);

    await waitFor(() => expect(router.state.location.pathname).toBe('/itineraries/new-it'));
    expect(api.body('POST', '/api/v1/ideas/idea-7/itinerary')).toEqual({
      starts_at: slots[1]!.starts_at,
      ends_at: slots[1]!.ends_at,
    });
  });

  it("shows the server's message when the planner is unavailable", async () => {
    setup({
      'POST /api/v1/ideas/idea-7/itinerary': jsonResponse(
        { error: { code: 'ai_unavailable', message: 'The planner is resting.' } },
        503,
      ),
    });
    renderApp('/calendar?idea=idea-7');
    await userEvent.click(await screen.findByRole('button', { name: 'Find free time' }));
    const [first] = await screen.findAllByRole('button', { name: /^Make it a date/ });
    await userEvent.click(first!);
    expect(await screen.findByRole('alert')).toHaveTextContent('The planner is resting.');
  });

  it('says so when there is no free time, and shows errors', async () => {
    let calls = 0;
    setup({
      'POST /api/v1/calendar/suggest-times': () =>
        ++calls === 1
          ? { slots: [] }
          : jsonResponse({ error: { code: 'server_error', message: 'boom' } }, 500),
    });
    renderApp('/calendar');
    await userEvent.click(await screen.findByRole('button', { name: 'Find free time' }));
    expect(
      await screen.findByText('No free time found. Try more weeks or a shorter date.'),
    ).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Find free time' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/went wrong on our side/);
  });
});
