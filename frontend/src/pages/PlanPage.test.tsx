import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { mockApi, type Call, type Handler } from '../test/mockApi';
import { jsonResponse, renderApp, renderAppWithClient } from '../test/renderApp';

const session = {
  authenticated: true,
  user: {
    id: 'u1',
    email: 'me@example.com',
    display_name: 'Sam',
    timezone: 'UTC',
    couple_id: 'c1',
    role: 'owner',
  },
};

function option(n: number) {
  return {
    id: `o${n}`,
    title: `Option ${n}`,
    pitch: `Pitch ${n}`,
    why_for_them: `Because ${n}`,
    grounded: n % 2 === 1,
    est_cost: 40 * n,
    occasion: null,
    stops: [
      { kind: 'food', title: `Dinner ${n}`, evergreen: false, est_cost: 40 * n },
      { kind: 'outdoors', title: `Walk ${n}`, evergreen: true },
    ],
  };
}

function planSession(options: ReturnType<typeof option>[], extra: object = {}) {
  return {
    id: 'p1',
    status: 'open',
    created_at: '2026-11-20T00:00:00Z',
    inputs: {},
    chosen: null,
    itinerary_id: null,
    options,
    ...extra,
  };
}

const itinerary = { id: 'i1', title: 'A very good night', stops: [], checklist: [] };

function setup(routes: Record<string, Handler> = {}) {
  return mockApi({
    'GET /api/v1/auth/session': session,
    'GET /api/v1/plan/sessions': [],
    'POST /api/v1/plan/sessions': jsonResponse(planSession([]), 201),
    'POST /api/v1/plan/sessions/p1/options': {
      ...planSession([option(1), option(2), option(3)]),
      degraded: [],
    },
    'POST /api/v1/plan/sessions/p1/build': itinerary,
    'POST /api/v1/plan/sessions/p1/plan-all': itinerary,
    'GET /api/v1/itineraries/i1': {
      ...itinerary,
      status: 'draft',
      is_planner: true,
      warnings: [],
      attributions: [],
    },
    ...routes,
  });
}

function apiError(status: number, code: string, extra: object = {}) {
  return jsonResponse({ error: { code, message: code, ...extra } }, status);
}

afterEach(() => vi.restoreAllMocks());

async function fillAndShowOptions() {
  renderApp('/plan');
  await userEvent.type(await screen.findByLabelText(/What are you in the mood for/), 'cozy');
  await userEvent.click(screen.getByRole('button', { name: 'Show me 3 options' }));
}

describe('PlanPage', () => {
  it('sends the form, shows options, and builds the chosen one', async () => {
    const api = setup();
    const router = renderApp('/plan');
    await userEvent.type(await screen.findByLabelText(/What are you in the mood for/), 'cozy');
    await userEvent.type(screen.getByLabelText('Budget (USD)'), '150');
    await userEvent.click(screen.getByRole('button', { name: 'Make it a surprise' }));
    expect(screen.getByText('Surprise — only you can see this')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Show me 3 options' }));

    expect(await screen.findByRole('heading', { name: 'Option 1' })).toBeInTheDocument();
    const body = api.body('POST', '/api/v1/plan/sessions') as Record<string, unknown>;
    expect(body).toMatchObject({ budget_max: 150, steer: 'cozy', surprise: true });
    expect(body.window_start).toMatch(/Z$/);
    expect(body.window_end).toMatch(/Z$/);
    expect(api.body('POST', '/api/v1/plan/sessions/p1/options')).toEqual({ more: false });
    expect(screen.getByText('Dinner 1')).toBeInTheDocument();
    expect(screen.getAllByText('Grounded in real places')).toHaveLength(2);
    expect(screen.getByText('Evergreen idea')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('checkbox', { name: 'Select Option 2' }));
    await userEvent.click(screen.getByRole('button', { name: 'Build this date' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/itineraries/i1'));
    expect(api.body('POST', '/api/v1/plan/sessions/p1/build')).toEqual({
      option_ids: ['o2'],
      combine_note: null,
    });
  });

  it('plans the whole thing and navigates', async () => {
    const api = setup();
    const router = renderApp('/plan');
    await userEvent.click(await screen.findByRole('button', { name: 'Plan the whole thing' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/itineraries/i1'));
    expect(api.find('POST', '/api/v1/plan/sessions/p1/plan-all')).toHaveLength(1);
    expect(api.find('POST', '/api/v1/plan/sessions/p1/options')).toHaveLength(0);
  });

  it('applies a time preset', async () => {
    const api = setup();
    renderApp('/plan');
    await userEvent.click(await screen.findByRole('button', { name: 'Afternoon' }));
    expect(screen.getByLabelText('Start time')).toHaveValue('13:00');
    expect(screen.getByLabelText('End time')).toHaveValue('18:00');
    await userEvent.click(screen.getByRole('button', { name: 'Plan the whole thing' }));
    await waitFor(() => expect(api.find('POST', '/api/v1/plan/sessions')).toHaveLength(1));
    const body = api.body('POST', '/api/v1/plan/sessions') as {
      window_start: string;
      window_end: string;
    };
    expect(new Date(body.window_start).getHours()).toBe(13);
    expect(new Date(body.window_end).getHours()).toBe(18);
  });

  it('blocks an end time before the start without calling the API', async () => {
    const api = setup();
    renderApp('/plan');
    const end = await screen.findByLabelText('End time');
    await userEvent.clear(end);
    await userEvent.type(end, '17:00');
    await userEvent.click(screen.getByRole('button', { name: 'Show me 3 options' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/after the start time/);
    expect(api.find('POST', '/api/v1/plan/sessions')).toHaveLength(0);
  });

  it('explains an invalid budget and blocks both submit buttons', async () => {
    const api = setup();
    renderApp('/plan');
    const budget = await screen.findByLabelText('Budget (USD)');
    expect(screen.queryByText(/Enter a budget of 0 or more/)).not.toBeInTheDocument();
    await userEvent.type(budget, '-5');

    const message = await screen.findByText('Enter a budget of 0 or more, or leave it blank.');
    expect(message).toHaveAttribute('role', 'alert');
    expect(budget).toHaveAttribute('aria-invalid', 'true');
    expect(budget.getAttribute('aria-describedby')).toContain(message.id);
    await userEvent.type(screen.getByLabelText(/What are you in the mood for/), 'cozy');
    await userEvent.click(screen.getByRole('button', { name: 'Show me 3 options' }));
    await userEvent.click(screen.getByRole('button', { name: 'Plan the whole thing' }));
    expect(api.find('POST', '/api/v1/plan/sessions')).toHaveLength(0);

    await userEvent.clear(budget);
    expect(screen.queryByText(/Enter a budget of 0 or more/)).not.toBeInTheDocument();
  });

  it('builds several options with a combine note, capped at three', async () => {
    const api = setup({
      'POST /api/v1/plan/sessions/p1/options': {
        ...planSession([option(1), option(2), option(3), option(4)]),
        degraded: [],
      },
    });
    await fillAndShowOptions();
    await screen.findByRole('heading', { name: 'Option 1' });
    expect(screen.queryByLabelText(/How should we combine/)).not.toBeInTheDocument();
    for (const n of [1, 2, 3]) {
      await userEvent.click(screen.getByRole('checkbox', { name: `Select Option ${n}` }));
    }
    expect(screen.getByRole('checkbox', { name: 'Select Option 4' })).toBeDisabled();
    await userEvent.type(screen.getByLabelText(/How should we combine/), 'Dinner then the walk');
    await userEvent.click(screen.getByRole('button', { name: 'Build this date' }));
    await waitFor(() =>
      expect(api.body('POST', '/api/v1/plan/sessions/p1/build')).toEqual({
        option_ids: ['o1', 'o2', 'o3'],
        combine_note: 'Dinner then the walk',
      }),
    );
  });

  it('appends more options, asking the API for more', async () => {
    const api = setup({
      'POST /api/v1/plan/sessions/p1/options': (call: Call) => ({
        ...planSession(
          ((call.body as { more: boolean }).more ? [1, 2, 3, 4, 5, 6] : [1, 2, 3]).map(option),
        ),
        degraded: [],
      }),
    });
    await fillAndShowOptions();
    await screen.findByRole('heading', { name: 'Option 3' });
    await userEvent.click(screen.getByRole('button', { name: 'More options' }));
    expect(await screen.findByRole('heading', { name: 'Option 6' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Option 1' })).toBeInTheDocument();
    expect(api.find('POST', '/api/v1/plan/sessions/p1/options')[1]?.body).toEqual({ more: true });
  });

  it('shows a non-blocking notice when sources were degraded', async () => {
    setup({
      'POST /api/v1/plan/sessions/p1/options': {
        ...planSession([option(1)]),
        degraded: ['weather'],
      },
    });
    await fillAndShowOptions();
    expect(await screen.findByText(/Some sources were unavailable/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Build this date' })).toBeDisabled();
    expect(screen.getByRole('heading', { name: 'Option 1' })).toBeInTheDocument();
  });

  it('shows staged progress text while working', async () => {
    let release: (value: unknown) => void = () => {};
    setup({
      'POST /api/v1/plan/sessions/p1/options': () =>
        new Promise((resolve) => {
          release = resolve;
        }),
    });
    await fillAndShowOptions();
    const status = await screen.findByRole('status');
    expect(status).toHaveAttribute('aria-live', 'polite');
    expect(status).toHaveTextContent('Finding places near you…');
    release({ ...planSession([option(1)]), degraded: [] });
    expect(await screen.findByRole('heading', { name: 'Option 1' })).toBeInTheDocument();
  });

  it('offers to pick up an open session', async () => {
    setup({ 'GET /api/v1/plan/sessions': [planSession([option(1), option(2)])] });
    renderApp('/plan');
    await userEvent.click(
      await screen.findByRole('button', { name: 'Pick up where you left off' }),
    );
    expect(await screen.findByRole('heading', { name: 'Option 2' })).toBeInTheDocument();
  });

  it('does not offer a finished session', async () => {
    setup({ 'GET /api/v1/plan/sessions': [planSession([option(1)], { status: 'done' })] });
    renderApp('/plan');
    await screen.findByRole('button', { name: 'Show me 3 options' });
    expect(screen.queryByRole('button', { name: 'Pick up where you left off' })).toBeNull();
  });

  it('drops the previous viewer’s options and form when the profile changes', async () => {
    setup();
    const { queryClient } = renderAppWithClient('/plan');
    await userEvent.type(await screen.findByLabelText(/What are you in the mood for/), 'cozy');
    await userEvent.click(screen.getByRole('button', { name: 'Show me 3 options' }));
    expect(await screen.findByRole('heading', { name: 'Option 1' })).toBeInTheDocument();

    act(() => {
      queryClient.setQueryData(['session'], {
        ...session,
        user: { ...session.user, id: 'u2', display_name: 'Alex' },
      });
    });

    expect(await screen.findByRole('button', { name: 'Show me 3 options' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Option 1' })).not.toBeInTheDocument();
    expect(screen.getByLabelText(/What are you in the mood for/)).toHaveValue('');
  });

  it('keeps the form usable and offers a retry when the sessions list fails', async () => {
    let attempts = 0;
    const api = setup({
      'GET /api/v1/plan/sessions': () =>
        ++attempts === 1 ? apiError(500, 'boom') : [planSession([option(1)])],
    });
    renderApp('/plan');
    expect(await screen.findByText(/Couldn't check for a plan in progress/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Show me 3 options' })).toBeEnabled();
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(
      await screen.findByRole('button', { name: 'Pick up where you left off' }),
    ).toBeInTheDocument();
    expect(api.find('GET', '/api/v1/plan/sessions')).toHaveLength(2);
    expect(screen.queryByText(/Couldn't check for a plan in progress/)).not.toBeInTheDocument();
  });

  describe('errors', () => {
    it('falls back to a generic message for an unexpected failure', async () => {
      setup({ 'POST /api/v1/plan/sessions': apiError(500, 'boom') });
      await fillAndShowOptions();
      expect(await screen.findByRole('alert')).toHaveTextContent(
        "Something went wrong on our side. That wasn't your fault.",
      );
    });

    it('reuses the created session when retrying plan-all with unchanged inputs', async () => {
      let attempts = 0;
      const api = setup({
        'POST /api/v1/plan/sessions/p1/plan-all': () =>
          ++attempts === 1 ? apiError(502, 'ai_failed') : itinerary,
      });
      const router = renderApp('/plan');
      await userEvent.click(await screen.findByRole('button', { name: 'Plan the whole thing' }));
      expect(await screen.findByRole('alert')).toHaveTextContent("didn't come together");
      await userEvent.click(screen.getByRole('button', { name: 'Plan the whole thing' }));
      await waitFor(() => expect(router.state.location.pathname).toBe('/itineraries/i1'));
      expect(api.find('POST', '/api/v1/plan/sessions')).toHaveLength(1);
      expect(api.find('POST', '/api/v1/plan/sessions/p1/plan-all')).toHaveLength(2);
    });

    it('explains the daily cap with the local reset time', async () => {
      const reset = '2026-11-22T05:00:00+00:00';
      setup({ 'POST /api/v1/plan/sessions': apiError(429, 'ai_cap_reached', { reset_at: reset }) });
      await fillAndShowOptions();
      const alert = await screen.findByRole('alert');
      const local = new Intl.DateTimeFormat(undefined, {
        dateStyle: 'medium',
        timeStyle: 'short',
      }).format(new Date(reset));
      expect(alert).toHaveTextContent(/used up all the planning magic/);
      expect(alert).toHaveTextContent(local);
    });

    it('says the planner is resting on 503', async () => {
      setup({ 'POST /api/v1/plan/sessions': apiError(503, 'ai_unavailable') });
      await fillAndShowOptions();
      expect(await screen.findByRole('alert')).toHaveTextContent(
        'The planner is resting right now',
      );
    });

    it('invites a retry on 502 and keeps the form filled in', async () => {
      setup({ 'POST /api/v1/plan/sessions/p1/options': apiError(502, 'ai_failed') });
      await fillAndShowOptions();
      expect(await screen.findByRole('alert')).toHaveTextContent(
        "That one didn't come together — try again",
      );
      expect(screen.getByLabelText(/What are you in the mood for/)).toHaveValue('cozy');
    });

    it('links to the Profile tab when the home area is missing', async () => {
      setup({ 'POST /api/v1/plan/sessions': apiError(409, 'home_area_required') });
      await fillAndShowOptions();
      const alert = await screen.findByRole('alert');
      expect(
        within(alert).getByRole('link', { name: 'Set your home area on the Profile tab' }),
      ).toHaveAttribute('href', '/us');
    });

    it('shows the API message for any other 422', async () => {
      setup({ 'POST /api/v1/plan/sessions': apiError(422, 'validation_error') });
      await fillAndShowOptions();
      expect(await screen.findByRole('alert')).toHaveTextContent('validation_error');
    });

    it('reuses the created session when retrying with unchanged inputs', async () => {
      let attempts = 0;
      const api = setup({
        'POST /api/v1/plan/sessions/p1/options': () =>
          ++attempts === 1
            ? apiError(502, 'ai_failed')
            : { ...planSession([option(1)]), degraded: [] },
      });
      await fillAndShowOptions();
      await screen.findByRole('alert');
      await userEvent.click(screen.getByRole('button', { name: 'Show me 3 options' }));
      expect(await screen.findByRole('heading', { name: 'Option 1' })).toBeInTheDocument();
      expect(api.find('POST', '/api/v1/plan/sessions')).toHaveLength(1);
      expect(api.find('POST', '/api/v1/plan/sessions/p1/options')).toHaveLength(2);
    });

    it('creates a new session when an input changed between attempts', async () => {
      let attempts = 0;
      const api = setup({
        'POST /api/v1/plan/sessions/p1/options': () =>
          ++attempts === 1
            ? apiError(502, 'ai_failed')
            : { ...planSession([option(1)]), degraded: [] },
      });
      await fillAndShowOptions();
      await screen.findByRole('alert');
      await userEvent.type(screen.getByLabelText('Must include'), 'dessert');
      await userEvent.click(screen.getByRole('button', { name: 'Show me 3 options' }));
      expect(await screen.findByRole('heading', { name: 'Option 1' })).toBeInTheDocument();
      expect(api.find('POST', '/api/v1/plan/sessions')).toHaveLength(2);
    });

    it('shows an inline window error on 422', async () => {
      setup({ 'POST /api/v1/plan/sessions': apiError(422, 'invalid_window') });
      await fillAndShowOptions();
      expect(await screen.findByRole('alert')).toHaveTextContent(/time window doesn't work/);
    });
  });
});

describe('PlanPage prefill from the calendar', () => {
  it('uses a valid date, start and end from the URL', async () => {
    setup();
    renderApp('/plan?date=2026-12-24&start=14:30&end=16:00');
    expect(await screen.findByLabelText('Date')).toHaveValue('2026-12-24');
    expect(screen.getByLabelText('Start time')).toHaveValue('14:30');
    expect(screen.getByLabelText('End time')).toHaveValue('16:00');
  });

  it('ignores malformed values and keeps the defaults', async () => {
    setup();
    renderApp('/plan?date=tomorrow&start=7pm&end=25:00');
    expect(await screen.findByLabelText('Date')).not.toHaveValue('tomorrow');
    expect(screen.getByLabelText('Start time')).toHaveValue('18:00');
    expect(screen.getByLabelText('End time')).toHaveValue('23:00');
  });

  it('ignores an impossible date instead of rolling it over', async () => {
    setup();
    renderApp('/plan?date=2026-02-30&start=19:00&end=22:00');
    const date = await screen.findByLabelText('Date');
    expect(date).not.toHaveValue('2026-02-30');
    expect(date).not.toHaveValue('2026-03-02');
  });
});
