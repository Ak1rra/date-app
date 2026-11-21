import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { isoToLocal } from './HintForm';
import { itineraryKey, type ItineraryOut } from '../../hooks/useItineraries';
import type { HintOut } from '../../hooks/useSurprise';
import { mockApi } from '../../test/mockApi';
import { renderApp, renderAppWithClient } from '../../test/renderApp';

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

const itinerary: ItineraryOut = {
  id: 'i1',
  idea_id: null,
  planner_user_id: 'u1',
  is_planner: true,
  title: 'A very good night',
  starts_at: '2030-12-10T18:00:00Z',
  ends_at: '2030-12-10T22:00:00Z',
  status: 'scheduled',
  announced_at: null,
  invitation_message: null,
  is_surprise: true,
  reveal_at: null,
  revealed_at: null,
  seen_by_partner_at: null,
  budget: null,
  est_total: null,
  currency: 'USD',
  weather: null,
  backup_plan: null,
  bring_wear: null,
  warnings: [],
  partner_reaction: null,
  created_at: '2026-11-20T00:00:00Z',
  updated_at: '2026-11-20T00:00:00Z',
  stops: [],
  checklist: [],
  attributions: [],
};

const released: HintOut = {
  id: 'h1',
  kind: 'dress_code',
  text: 'Wear something you can dance in',
  release_at: '2026-11-20T10:00:00Z',
  released_at: '2026-11-20T10:00:00Z',
};
const pending: HintOut = {
  id: 'h2',
  kind: 'bring',
  text: 'Bring a jacket',
  release_at: '2030-12-09T18:00:00Z',
  released_at: null,
};

function setup(
  data: ItineraryOut = itinerary,
  hints: HintOut[] = [released, pending],
  extra: Record<string, unknown> = {},
) {
  return mockApi({
    'GET /api/v1/auth/session': session,
    'GET /api/v1/itineraries/i1': data,
    'GET /api/v1/itineraries/i1/hints': hints,
    'GET /api/v1/deck': { cards: [], partner_joined: true },
    'POST /api/v1/itineraries/i1/hints': pending,
    'PATCH /api/v1/itineraries/i1/hints/h2': pending,
    'DELETE /api/v1/itineraries/i1/hints/h2': new Response(null, { status: 204 }),
    'PUT /api/v1/itineraries/i1/reveal-at': data,
    'POST /api/v1/itineraries/i1/reveal': {
      ...data,
      reveal_at: '2026-11-21T12:00:00Z',
      revealed_at: '2026-11-21T12:00:00Z',
    },
    ...extra,
  });
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

async function addForm() {
  await screen.findByRole('heading', { name: 'Add a hint' });
  return within(screen.getByRole('form', { name: 'Add a hint' }));
}

describe('SurpriseSection', () => {
  it('lists released and pending hints', async () => {
    setup();
    renderApp('/itineraries/i1');
    expect(await screen.findByRole('heading', { name: 'Surprise ✨' })).toBeInTheDocument();
    expect(await screen.findByText('Wear something you can dance in')).toBeInTheDocument();
    expect(
      within(screen.getByRole('list', { name: 'Hints' })).getByText('Dress code'),
    ).toBeInTheDocument();
    expect(screen.getByText(/^Released ✓/)).toBeInTheDocument();
    expect(screen.getByText('Bring a jacket')).toBeInTheDocument();
    expect(screen.getByText(/^Releases /)).toBeInTheDocument();
    expect(screen.getByText(/^Reveals at /)).toBeInTheDocument();
  });

  it('is not shown to the partner', async () => {
    setup({ ...itinerary, is_planner: false });
    renderApp('/itineraries/i1');
    await screen.findByRole('heading', { name: 'A very good night' });
    expect(screen.queryByRole('heading', { name: 'Surprise ✨' })).not.toBeInTheDocument();
  });

  it('is not shown for a date that is not a surprise', async () => {
    setup({ ...itinerary, is_surprise: false });
    renderApp('/itineraries/i1');
    await screen.findByRole('heading', { name: 'A very good night' });
    expect(screen.queryByRole('heading', { name: 'Surprise ✨' })).not.toBeInTheDocument();
  });

  it('adds a hint released now', async () => {
    const api = setup();
    renderApp('/itineraries/i1');
    const form = await addForm();
    await userEvent.click(form.getByRole('button', { name: 'Eating' }));
    await userEvent.type(form.getByLabelText('Hint'), '  Come hungry  ');
    await userEvent.click(form.getByRole('button', { name: 'Add hint' }));
    await waitFor(() =>
      expect(api.body('POST', '/api/v1/itineraries/i1/hints')).toEqual({
        kind: 'eat',
        text: 'Come hungry',
        release_at: null,
      }),
    );
  });

  it('adds a hint released a day before the start', async () => {
    const api = setup();
    renderApp('/itineraries/i1');
    const form = await addForm();
    await userEvent.type(form.getByLabelText('Hint'), 'Dress warm');
    await userEvent.selectOptions(form.getByLabelText('When to release'), '1 day before');
    await userEvent.click(form.getByRole('button', { name: 'Add hint' }));
    await waitFor(() =>
      expect(api.body('POST', '/api/v1/itineraries/i1/hints')).toEqual({
        kind: 'dress_code',
        text: 'Dress warm',
        release_at: '2030-12-09T18:00:00.000Z',
      }),
    );
  });

  it('adds a hint released at a picked time', async () => {
    const api = setup();
    renderApp('/itineraries/i1');
    const form = await addForm();
    await userEvent.type(form.getByLabelText('Hint'), 'Be ready early');
    await userEvent.selectOptions(form.getByLabelText('When to release'), 'Pick a time');
    expect(form.getByRole('button', { name: 'Add hint' })).toBeDisabled();
    fireEvent.change(form.getByLabelText('Release time'), {
      target: { value: '2030-12-08T10:00' },
    });
    await userEvent.click(form.getByRole('button', { name: 'Add hint' }));
    await waitFor(() =>
      expect(api.body('POST', '/api/v1/itineraries/i1/hints')).toEqual({
        kind: 'dress_code',
        text: 'Be ready early',
        release_at: new Date('2030-12-08T10:00').toISOString(),
      }),
    );
  });

  it('disables relative release times that are not possible', async () => {
    setup({ ...itinerary, starts_at: new Date(Date.now() + 5 * 3_600_000).toISOString() });
    renderApp('/itineraries/i1');
    const form = await addForm();
    expect(form.getByRole('option', { name: '2 hours before' })).toBeEnabled();
    expect(form.getByRole('option', { name: '6 hours before' })).toBeDisabled();
    expect(form.getByRole('option', { name: '1 day before' })).toBeDisabled();
  });

  it('hides edit for released hints and edits a pending one', async () => {
    const api = setup();
    renderApp('/itineraries/i1');
    await screen.findByText('Bring a jacket');
    expect(
      screen.queryByRole('button', { name: `Edit hint: ${released.text}` }),
    ).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Edit hint: Bring a jacket' }));
    const form = within(screen.getByRole('form', { name: 'Edit hint' }));
    const text = form.getByLabelText('Hint');
    await userEvent.clear(text);
    await userEvent.type(text, 'Bring a scarf');
    await userEvent.click(form.getByRole('button', { name: 'Save hint' }));
    await waitFor(() =>
      expect(api.body('PATCH', '/api/v1/itineraries/i1/hints/h2')).toEqual({
        kind: 'bring',
        text: 'Bring a scarf',
        release_at: '2030-12-09T18:00:00.000Z',
      }),
    );
  });

  it('asks before deleting a hint', async () => {
    const api = setup();
    renderApp('/itineraries/i1');
    await userEvent.click(
      await screen.findByRole('button', { name: 'Delete hint: Bring a jacket' }),
    );
    expect(api.find('DELETE', '/api/v1/itineraries/i1/hints/h2')).toHaveLength(0);
    await userEvent.click(screen.getByRole('button', { name: 'Keep it' }));
    expect(api.find('DELETE', '/api/v1/itineraries/i1/hints/h2')).toHaveLength(0);
    await userEvent.click(screen.getByRole('button', { name: 'Delete hint: Bring a jacket' }));
    await userEvent.click(screen.getByRole('button', { name: 'Yes, delete hint: Bring a jacket' }));
    await waitFor(() =>
      expect(api.find('DELETE', '/api/v1/itineraries/i1/hints/h2')).toHaveLength(1),
    );
  });

  it('hides the add form at 20 hints', async () => {
    const many = Array.from({ length: 20 }, (_, index) => ({
      ...pending,
      id: `m${index}`,
      text: `Hint ${index}`,
    }));
    setup(itinerary, many);
    renderApp('/itineraries/i1');
    expect(await screen.findByText(/That's 20 hints/)).toBeInTheDocument();
    expect(screen.queryByRole('form', { name: 'Add a hint' })).not.toBeInTheDocument();
  });

  it('reveals now after a confirm and shows Revealed', async () => {
    const api = setup();
    renderApp('/itineraries/i1');
    await userEvent.click(await screen.findByRole('button', { name: 'Reveal now' }));
    expect(screen.getByText("Reveal it now? They'll get a notification.")).toBeInTheDocument();
    expect(api.find('POST', '/api/v1/itineraries/i1/reveal')).toHaveLength(0);
    await userEvent.click(screen.getByRole('button', { name: 'Yes, reveal it' }));
    expect(await screen.findByText(/^Revealed ✓/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Reveal now' })).not.toBeInTheDocument();
  });

  it('offers no reveal now for a draft', async () => {
    setup({ ...itinerary, status: 'draft' });
    renderApp('/itineraries/i1');
    await screen.findByRole('heading', { name: 'The big reveal' });
    expect(screen.queryByRole('button', { name: 'Reveal now' })).not.toBeInTheDocument();
  });

  it('sets the reveal time, or null for the start', async () => {
    const api = setup({ ...itinerary, reveal_at: '2030-12-10T17:00:00Z' });
    renderApp('/itineraries/i1');
    const time = await screen.findByLabelText('Reveal time');
    fireEvent.change(time, { target: { value: '2030-12-10T16:30' } });
    await userEvent.click(screen.getByRole('button', { name: 'Set reveal time' }));
    await waitFor(() =>
      expect(api.body('PUT', '/api/v1/itineraries/i1/reveal-at')).toEqual({
        reveal_at: new Date('2030-12-10T16:30').toISOString(),
      }),
    );
    await userEvent.click(screen.getByRole('button', { name: 'At the start' }));
    await waitFor(() =>
      expect(api.find('PUT', '/api/v1/itineraries/i1/reveal-at')[1]?.body).toEqual({
        reveal_at: null,
      }),
    );
  });

  it('copies a share-ready line for released hints when the partner has not joined', async () => {
    setup(itinerary, [released, pending], {
      'GET /api/v1/deck': { cards: [], partner_joined: false },
    });
    const user = userEvent.setup();
    renderApp('/itineraries/i1');
    expect(await screen.findAllByRole('button', { name: /^Copy hint:/ })).toHaveLength(1);
    await user.click(screen.getByRole('button', { name: `Copy hint: ${released.text}` }));
    expect(await screen.findByRole('button', { name: /^Copied hint:/ })).toHaveTextContent(
      'Copied',
    );
    expect(screen.getByText('Copied ✓')).toBeInTheDocument();
    expect(await navigator.clipboard.readText()).toMatch(
      /^Hint for our date on .+: Wear something you can dance in$/,
    );
  });

  it('keeps a relative release at the same local hour across a DST change', async () => {
    vi.stubEnv('TZ', 'America/New_York');
    try {
      const startsAt = '2030-11-03T20:00:00Z';
      const api = setup({ ...itinerary, starts_at: startsAt });
      renderApp('/itineraries/i1');
      const form = await addForm();
      await userEvent.type(form.getByLabelText('Hint'), 'Dress warm');
      await userEvent.selectOptions(form.getByLabelText('When to release'), '1 day before');
      await userEvent.click(form.getByRole('button', { name: 'Add hint' }));
      await waitFor(() => expect(api.find('POST', '/api/v1/itineraries/i1/hints')).toHaveLength(1));
      const body = api.body('POST', '/api/v1/itineraries/i1/hints') as { release_at: string };
      expect(new Date(body.release_at).getHours()).toBe(new Date(startsAt).getHours());
      expect(new Date(body.release_at).getDate()).toBe(new Date(startsAt).getDate() - 1);
    } finally {
      vi.unstubAllEnvs();
    }
  });

  it('does not submit a relative time that passed while the form was open', async () => {
    vi.useFakeTimers({ toFake: ['Date'], now: new Date('2030-12-10T15:00:00Z') });
    const api = setup({ ...itinerary, starts_at: '2030-12-10T17:00:30Z' });
    renderApp('/itineraries/i1');
    const form = await addForm();
    await userEvent.type(form.getByLabelText('Hint'), 'Be ready');
    await userEvent.selectOptions(form.getByLabelText('When to release'), '2 hours before');
    vi.setSystemTime(new Date('2030-12-10T15:05:00Z'));
    await userEvent.click(form.getByRole('button', { name: 'Add hint' }));
    expect(
      await screen.findByText('That time has already passed — pick another.'),
    ).toBeInTheDocument();
    expect(api.find('POST', '/api/v1/itineraries/i1/hints')).toHaveLength(0);
  });

  it('greys out a relative option once its time passes while the form is open', async () => {
    vi.useFakeTimers({
      toFake: ['Date', 'setInterval', 'clearInterval'],
      now: new Date('2030-12-10T15:00:00Z'),
    });
    setup({ ...itinerary, starts_at: '2030-12-10T17:00:30Z' });
    renderApp('/itineraries/i1');
    const form = await addForm();
    expect(form.getByRole('option', { name: '2 hours before' })).toBeEnabled();
    act(() => {
      vi.advanceTimersByTime(60_000);
    });
    expect(form.getByRole('option', { name: '2 hours before' })).toBeDisabled();
  });

  it('disables the whole add form while a hint is being saved', async () => {
    let release: (value: HintOut) => void = () => {};
    setup(itinerary, [released, pending], {
      'POST /api/v1/itineraries/i1/hints': () =>
        new Promise<HintOut>((resolve) => {
          release = resolve;
        }),
    });
    renderApp('/itineraries/i1');
    const form = await addForm();
    await userEvent.type(form.getByLabelText('Hint'), 'Come hungry');
    await userEvent.click(form.getByRole('button', { name: 'Add hint' }));
    await waitFor(() => expect(form.getByRole('button', { name: 'Eating' })).toBeDisabled());
    expect(form.getByLabelText('Hint')).toBeDisabled();
    expect(form.getByLabelText('When to release')).toBeDisabled();
    release(pending);
  });

  it('refetches hints after the reveal time changes', async () => {
    const api = setup();
    renderApp('/itineraries/i1');
    await screen.findByText('Bring a jacket');
    const before = api.find('GET', '/api/v1/itineraries/i1/hints').length;
    await userEvent.click(screen.getByRole('button', { name: 'At the start' }));
    await waitFor(() =>
      expect(api.find('GET', '/api/v1/itineraries/i1/hints').length).toBeGreaterThan(before),
    );
  });

  it('shows the new reveal time after the itinerary changes', async () => {
    const revealAt = '2030-12-10T17:00:00Z';
    setup(itinerary, [released, pending], {
      'PUT /api/v1/itineraries/i1/reveal-at': { ...itinerary, reveal_at: revealAt },
    });
    renderApp('/itineraries/i1');
    const time = await screen.findByLabelText('Reveal time');
    fireEvent.change(time, { target: { value: '2030-12-10T17:00' } });
    await userEvent.click(screen.getByRole('button', { name: 'Set reveal time' }));
    await waitFor(() => expect(screen.getByText(/^Reveals at /)).toBeInTheDocument());
    expect(screen.getByLabelText('Reveal time')).toHaveValue(isoToLocal(revealAt));
  });

  it('does not submit a picked hint time that has already passed', async () => {
    const api = setup();
    renderApp('/itineraries/i1');
    const form = await addForm();
    await userEvent.type(form.getByLabelText('Hint'), 'Be ready early');
    await userEvent.selectOptions(form.getByLabelText('When to release'), 'Pick a time');
    fireEvent.change(form.getByLabelText('Release time'), {
      target: { value: '2020-01-01T10:00' },
    });
    await userEvent.click(form.getByRole('button', { name: 'Add hint' }));
    expect(
      await screen.findByText('That time has already passed — pick another.'),
    ).toBeInTheDocument();
    expect(api.find('POST', '/api/v1/itineraries/i1/hints')).toHaveLength(0);
  });

  it('does not submit a picked reveal time that has already passed', async () => {
    const api = setup();
    renderApp('/itineraries/i1');
    const time = await screen.findByLabelText('Reveal time');
    fireEvent.change(time, { target: { value: '2020-01-01T10:00' } });
    await userEvent.click(screen.getByRole('button', { name: 'Set reveal time' }));
    expect(
      await screen.findByText('That time has already passed — pick another.'),
    ).toBeInTheDocument();
    expect(api.find('PUT', '/api/v1/itineraries/i1/reveal-at')).toHaveLength(0);
  });

  it('disables the reveal time input while the reveal time is being saved', async () => {
    let release: (value: ItineraryOut) => void = () => {};
    setup(itinerary, [released, pending], {
      'PUT /api/v1/itineraries/i1/reveal-at': () =>
        new Promise<ItineraryOut>((resolve) => {
          release = resolve;
        }),
    });
    renderApp('/itineraries/i1');
    const time = await screen.findByLabelText('Reveal time');
    fireEvent.change(time, { target: { value: '2030-12-10T16:30' } });
    await userEvent.click(screen.getByRole('button', { name: 'Set reveal time' }));
    await waitFor(() => expect(screen.getByLabelText('Reveal time')).toBeDisabled());
    release(itinerary);
  });

  it('refetches hints after the itinerary is updated elsewhere', async () => {
    const api = setup();
    const { queryClient } = renderAppWithClient('/itineraries/i1');
    await screen.findByText('Bring a jacket');
    const before = api.find('GET', '/api/v1/itineraries/i1/hints').length;
    act(() => {
      queryClient.setQueryData(itineraryKey('u1', 'i1'), {
        ...itinerary,
        updated_at: '2026-11-21T09:00:00Z',
      });
    });
    await waitFor(() =>
      expect(api.find('GET', '/api/v1/itineraries/i1/hints').length).toBeGreaterThan(before),
    );
  });

  it('is not shown, and loads no hints, for a completed date', async () => {
    const api = setup({ ...itinerary, status: 'completed' });
    renderApp('/itineraries/i1');
    await screen.findByRole('heading', { name: 'A very good night' });
    expect(screen.queryByRole('heading', { name: 'Surprise ✨' })).not.toBeInTheDocument();
    expect(api.find('GET', '/api/v1/itineraries/i1/hints')).toHaveLength(0);
  });

  it('says so when copying fails', async () => {
    setup(itinerary, [released, pending], {
      'GET /api/v1/deck': { cards: [], partner_joined: false },
    });
    const user = userEvent.setup();
    vi.spyOn(navigator.clipboard, 'writeText').mockRejectedValue(new Error('denied'));
    renderApp('/itineraries/i1');
    await user.click(await screen.findByRole('button', { name: `Copy hint: ${released.text}` }));
    expect(
      await screen.findByText("Couldn't copy — select the text and copy it yourself."),
    ).toBeInTheDocument();
    expect(screen.queryByText('Copied ✓')).not.toBeInTheDocument();
  });

  it('shows no copy buttons once the partner has joined', async () => {
    setup();
    renderApp('/itineraries/i1');
    await screen.findByText('Bring a jacket');
    expect(screen.queryByRole('button', { name: /^Copy hint:/ })).not.toBeInTheDocument();
  });
});
