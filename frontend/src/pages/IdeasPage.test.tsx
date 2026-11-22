import { act, fireEvent, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { Schemas } from '../api/client';
import { SESSION_KEY } from '../hooks/useSession';
import { husbandProfile, session, wifeProfile } from '../test/fixtures';
import { mockApi, type Handler } from '../test/mockApi';
import { renderApp, renderAppWithClient } from '../test/renderApp';

afterEach(() => {
  vi.unstubAllGlobals();
});

function idea(overrides: Partial<Schemas['IdeaOut']> = {}): Schemas['IdeaOut'] {
  return {
    id: 'i1',
    title: 'Pottery night',
    pitch: 'Get messy together.',
    est_cost: 60,
    currency: 'USD',
    tags: ['creative', 'cozy'],
    category: null,
    best_time: null,
    duration_minutes: null,
    indoor_outdoor: null,
    event: null,
    venue: null,
    grounded: false,
    may_have_changed: false,
    attributions: [],
    created_by_me: false,
    source: 'manual',
    status: 'active',
    visibility: 'shared',
    valid_until: null,
    votes: { mine: null, partner: null },
    ...overrides,
  };
}

const alexProfile = { ...wifeProfile, subject_joined: true };
const emptyLists = { matches: [], maybes: [], pending_partner: [], passed: [] };

function ideasApi(extra: Record<string, Handler> = {}) {
  return mockApi({
    'GET /api/v1/auth/session': session('Sam'),
    'GET /api/v1/meta': { demo_mode: false, version: 'test', no_login: true },
    'GET /api/v1/profiles': [husbandProfile, alexProfile],
    'GET /api/v1/deck': {
      cards: [idea(), idea({ id: 'i2', title: 'Rooftop movie', pitch: 'Blankets and popcorn.' })],
      partner_joined: true,
    },
    'GET /api/v1/ideas/lists': emptyLists,
    'GET /api/v1/ideas': [],
    ...extra,
  });
}

function voted(value: 'yes' | 'no' | 'maybe', partner: 'yes' | 'no' | 'maybe' | null = null) {
  return idea({ votes: { mine: value, partner } });
}

function mockReducedMotion() {
  vi.stubGlobal(
    'matchMedia',
    vi.fn(() => ({
      matches: true,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  );
}

describe('IdeasPage deck', () => {
  it('casts a vote with the buttons and moves to the next card', async () => {
    const api = ideasApi({ 'PUT /api/v1/ideas/i1/vote': voted('yes') });
    renderApp('/ideas');

    expect(await screen.findByRole('heading', { name: 'Pottery night' })).toBeInTheDocument();
    expect(screen.getByText('Get messy together.')).toBeInTheDocument();
    expect(screen.getByText('About $60')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Yes please' }));

    expect(await screen.findByRole('heading', { name: 'Rooftop movie' })).toBeInTheDocument();
    expect(api.body('PUT', '/api/v1/ideas/i1/vote')).toEqual({ value: 'yes' });
  });

  it('votes with the arrow keys: right yes, left no, up maybe', async () => {
    const api = ideasApi({
      'PUT /api/v1/ideas/i1/vote': voted('no'),
      'PUT /api/v1/ideas/i2/vote': voted('maybe'),
    });
    renderApp('/ideas');
    await screen.findByRole('heading', { name: 'Pottery night' });

    await userEvent.keyboard('{ArrowLeft}');
    await screen.findByRole('heading', { name: 'Rooftop movie' });
    await userEvent.keyboard('{ArrowUp}');
    await screen.findByText("You're all caught up");

    expect(api.body('PUT', '/api/v1/ideas/i1/vote')).toEqual({ value: 'no' });
    expect(api.body('PUT', '/api/v1/ideas/i2/vote')).toEqual({ value: 'maybe' });
  });

  it('votes yes with the right arrow key', async () => {
    const api = ideasApi({ 'PUT /api/v1/ideas/i1/vote': voted('yes') });
    renderApp('/ideas');
    await screen.findByRole('heading', { name: 'Pottery night' });

    await userEvent.keyboard('{ArrowRight}');
    await vi.waitFor(() => expect(api.find('PUT', '/api/v1/ideas/i1/vote')).toHaveLength(1));
    expect(api.body('PUT', '/api/v1/ideas/i1/vote')).toEqual({ value: 'yes' });
  });

  it('votes by dragging the card past the threshold, and ignores a short drag', async () => {
    const api = ideasApi({ 'PUT /api/v1/ideas/i1/vote': voted('maybe') });
    renderApp('/ideas');
    await screen.findByRole('heading', { name: 'Pottery night' });
    const card = screen.getByTestId('swipe-card');

    fireEvent.pointerDown(card, { clientX: 100, clientY: 100, pointerId: 1 });
    fireEvent.pointerMove(card, { clientX: 130, clientY: 100, pointerId: 1 });
    fireEvent.pointerUp(card, { clientX: 130, clientY: 100, pointerId: 1 });
    expect(api.find('PUT', '/api/v1/ideas/i1/vote')).toHaveLength(0);

    fireEvent.pointerDown(card, { clientX: 100, clientY: 200, pointerId: 1 });
    fireEvent.pointerMove(card, { clientX: 110, clientY: 90, pointerId: 1 });
    fireEvent.pointerUp(card, { clientX: 110, clientY: 90, pointerId: 1 });
    await vi.waitFor(() => expect(api.find('PUT', '/api/v1/ideas/i1/vote')).toHaveLength(1));
    expect(api.body('PUT', '/api/v1/ideas/i1/vote')).toEqual({ value: 'maybe' });
  });

  it('does not drag under reduced motion, but buttons still work', async () => {
    mockReducedMotion();
    const api = ideasApi({ 'PUT /api/v1/ideas/i1/vote': voted('yes') });
    renderApp('/ideas');
    await screen.findByRole('heading', { name: 'Pottery night' });
    const card = screen.getByTestId('swipe-card');

    fireEvent.pointerDown(card, { clientX: 100, clientY: 100, pointerId: 1 });
    fireEvent.pointerMove(card, { clientX: 300, clientY: 100, pointerId: 1 });
    fireEvent.pointerUp(card, { clientX: 300, clientY: 100, pointerId: 1 });
    expect(card.style.transform).toBe('');
    expect(api.find('PUT', '/api/v1/ideas/i1/vote')).toHaveLength(0);

    await userEvent.click(screen.getByRole('button', { name: 'Yes please' }));
    await vi.waitFor(() => expect(api.find('PUT', '/api/v1/ideas/i1/vote')).toHaveLength(1));
  });

  it('undoes the latest vote and brings the card back', async () => {
    const api = ideasApi({
      'PUT /api/v1/ideas/i1/vote': voted('no'),
      'POST /api/v1/deck/undo': idea(),
    });
    renderApp('/ideas');
    await screen.findByRole('heading', { name: 'Pottery night' });

    await userEvent.click(screen.getByRole('button', { name: 'Nope' }));
    await screen.findByRole('heading', { name: 'Rooftop movie' });
    await userEvent.click(screen.getByRole('button', { name: 'Undo' }));

    expect(await screen.findByRole('heading', { name: 'Pottery night' })).toBeInTheDocument();
    expect(api.find('POST', '/api/v1/deck/undo')).toHaveLength(1);
  });

  it('tells you when there is nothing to undo (a 204 with no body) and changes nothing', async () => {
    const api = ideasApi({ 'POST /api/v1/deck/undo': new Response(null, { status: 204 }) });
    renderApp('/ideas');
    await screen.findByRole('heading', { name: 'Pottery night' });

    await userEvent.click(screen.getByRole('button', { name: 'Undo' }));
    expect(await screen.findByRole('status')).toHaveTextContent('Nothing to take back');
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Pottery night' })).toBeInTheDocument();
    expect(api.find('POST', '/api/v1/deck/undo')).toHaveLength(1);
  });

  it('shows a retry message when the undo itself fails', async () => {
    ideasApi({
      'POST /api/v1/deck/undo': new Response(
        JSON.stringify({ error: { code: 'server_error', message: 'Boom.' } }),
        { status: 500, headers: { 'Content-Type': 'application/json' } },
      ),
    });
    renderApp('/ideas');
    await screen.findByRole('heading', { name: 'Pottery night' });

    await userEvent.click(screen.getByRole('button', { name: 'Undo' }));
    expect(await screen.findByRole('alert')).toHaveTextContent("We couldn't undo that");
  });

  it('celebrates a match, dismissibly, and keeps the keys quiet while it is open', async () => {
    const api = ideasApi({ 'PUT /api/v1/ideas/i1/vote': voted('yes', 'yes') });
    renderApp('/ideas');
    await screen.findByRole('heading', { name: 'Pottery night' });

    await userEvent.click(screen.getByRole('button', { name: 'Yes please' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByRole('heading', { name: /It's a match!/ })).toBeInTheDocument();
    expect(within(dialog).getByText('Pottery night')).toBeInTheDocument();
    expect(dialog.querySelector('.motion-safe\\:animate-bounce')).not.toBeNull();

    await userEvent.keyboard('{ArrowRight}');
    expect(api.find('PUT', '/api/v1/ideas/i2/vote')).toHaveLength(0);

    await userEvent.click(within(dialog).getByRole('button', { name: 'Keep swiping' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('shows a static, dismissible match overlay under reduced motion', async () => {
    mockReducedMotion();
    ideasApi({ 'PUT /api/v1/ideas/i1/vote': voted('yes', 'yes') });
    renderApp('/ideas');
    await screen.findByRole('heading', { name: 'Pottery night' });

    await userEvent.click(screen.getByRole('button', { name: 'Yes please' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('Pottery night')).toBeInTheDocument();
    // Every animation class is behind motion-safe:, so none applies when motion is reduced.
    const classes = Array.from(dialog.querySelectorAll('[class]')).flatMap((el) =>
      Array.from(el.classList),
    );
    expect(classes.filter((c) => c.includes('animate-'))).not.toHaveLength(0);
    expect(classes.filter((c) => c.includes('animate-') && !c.startsWith('motion-safe:'))).toEqual(
      [],
    );

    await userEvent.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('resets the match overlay and the deck when the profile changes', async () => {
    // Each profile has its own deck. The new one reuses id i1 for a different idea, so a voted set
    // carried over from the old profile would hide it.
    let viewer = 'u1';
    ideasApi({
      'GET /api/v1/deck': () => ({
        cards:
          viewer === 'u1'
            ? [idea(), idea({ id: 'i2', title: 'Rooftop movie' })]
            : [idea({ id: 'i1', title: 'Stargazing', pitch: 'Lie back and watch.' })],
        partner_joined: true,
      }),
      'PUT /api/v1/ideas/i1/vote': voted('yes', 'yes'),
      'PUT /api/v1/ideas/i2/vote': idea({ id: 'i2', votes: { mine: 'yes', partner: 'yes' } }),
    });
    const { queryClient } = renderAppWithClient('/ideas');
    await screen.findByRole('heading', { name: 'Pottery night' });

    await userEvent.click(screen.getByRole('button', { name: 'Yes please' }));
    await screen.findByRole('dialog');
    await userEvent.click(screen.getByRole('button', { name: 'Keep swiping' }));
    await userEvent.click(screen.getByRole('button', { name: 'Yes please' }));
    await screen.findByRole('dialog');

    const next = session('Alex');
    viewer = 'u2';
    act(() => {
      queryClient.setQueryData(SESSION_KEY, { ...next, user: { ...next.user!, id: 'u2' } });
    });

    await vi.waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    // The new viewer starts from their own deck, with nothing marked as voted from before.
    expect(await screen.findByRole('heading', { name: 'Stargazing' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Pottery night' })).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Rooftop movie' })).not.toBeInTheDocument();
  });

  it('does not celebrate when only I said yes', async () => {
    ideasApi({ 'PUT /api/v1/ideas/i1/vote': voted('yes') });
    renderApp('/ideas');
    await screen.findByRole('heading', { name: 'Pottery night' });
    await userEvent.click(screen.getByRole('button', { name: 'Yes please' }));
    await screen.findByRole('heading', { name: 'Rooftop movie' });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('shows the invite call to action until the partner joins', async () => {
    ideasApi({ 'GET /api/v1/deck': { cards: [], partner_joined: false } });
    renderApp('/ideas');
    expect(await screen.findByText('Swipe on ideas together')).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Invite your partner to swipe together' }),
    ).toBeInTheDocument();
  });

  it('says so when the deck is empty', async () => {
    ideasApi({ 'GET /api/v1/deck': { cards: [], partner_joined: true } });
    renderApp('/ideas');
    expect(await screen.findByText("You're all caught up")).toBeInTheDocument();
  });
});

describe('IdeasPage lists', () => {
  const lists = {
    matches: [idea({ id: 'm1', title: 'Wine tasting', votes: { mine: 'yes', partner: 'yes' } })],
    maybes: [idea({ id: 'y1', title: 'Escape room', votes: { mine: 'maybe', partner: null } })],
    pending_partner: [
      idea({ id: 'p1', title: 'Cooking class', votes: { mine: 'yes', partner: null } }),
    ],
    passed: [idea({ id: 'x1', title: 'Bowling', votes: { mine: 'no', partner: 'yes' } })],
  };

  it('names each list, shows counts, and reveals the partner vote only when present', async () => {
    ideasApi({ 'GET /api/v1/ideas/lists': lists });
    renderApp('/ideas');

    const waiting = await screen.findByRole('tab', { name: 'Waiting on Alex (1)' });
    expect(screen.getByRole('tab', { name: 'Matches (1)' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'My ideas' })).toBeInTheDocument();

    await userEvent.click(screen.getByRole('tab', { name: 'Matches (1)' }));
    expect(await screen.findByRole('heading', { name: 'Wine tasting' })).toBeInTheDocument();
    expect(screen.getByText('You: Yes')).toBeInTheDocument();
    expect(screen.getByText('Alex: Yes')).toBeInTheDocument();

    await userEvent.click(waiting);
    expect(await screen.findByRole('heading', { name: 'Cooking class' })).toBeInTheDocument();
    expect(screen.getByText('You: Yes')).toBeInTheDocument();
    expect(screen.queryByText(/Alex: /)).not.toBeInTheDocument();
    expect(screen.getByText('About $60')).toBeInTheDocument();
    expect(screen.getByText('cozy')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('tab', { name: 'Maybes (1)' }));
    expect(await screen.findByRole('heading', { name: 'Escape room' })).toBeInTheDocument();
  });

  it('offers no re-vote on expired maybes and passed ideas, and says they expired', async () => {
    ideasApi({
      'GET /api/v1/ideas/lists': {
        ...emptyLists,
        maybes: [idea({ id: 'e1', title: 'Fireworks', status: 'expired' })],
        passed: [idea({ id: 'e2', title: 'Parade', status: 'expired' })],
      },
    });
    renderApp('/ideas');

    await userEvent.click(await screen.findByRole('tab', { name: 'Maybes (1)' }));
    await screen.findByRole('heading', { name: 'Fireworks' });
    expect(screen.getByText('Expired')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /for Fireworks/ })).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('tab', { name: 'Passed (1)' }));
    await userEvent.click(await screen.findByText('Show 1 passed idea'));
    expect(screen.getByText('Expired')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /for Parade/ })).not.toBeInTheDocument();
  });

  it('shows the estimated cost in the idea’s own currency', async () => {
    ideasApi({
      'GET /api/v1/ideas/lists': {
        ...emptyLists,
        maybes: [idea({ id: 'e3', title: 'Paris picnic', est_cost: 40, currency: 'EUR' })],
      },
    });
    renderApp('/ideas');
    await userEvent.click(await screen.findByRole('tab', { name: 'Maybes (1)' }));
    await screen.findByRole('heading', { name: 'Paris picnic' });
    expect(screen.getByText(/About .*40/)).toHaveTextContent('€');
    expect(screen.getByText(/About .*40/)).not.toHaveTextContent('$');
  });

  it('lets me change my vote from a match row and from a waiting row', async () => {
    const api = ideasApi({
      'GET /api/v1/ideas/lists': lists,
      'PUT /api/v1/ideas/m1/vote': idea({ id: 'm1', votes: { mine: 'no', partner: 'yes' } }),
      'PUT /api/v1/ideas/p1/vote': idea({ id: 'p1', votes: { mine: 'maybe', partner: null } }),
    });
    renderApp('/ideas');

    await userEvent.click(await screen.findByRole('tab', { name: 'Matches (1)' }));
    expect(screen.queryByRole('button', { name: 'Yes please for Wine tasting' })).toBeNull();
    await userEvent.click(await screen.findByRole('button', { name: 'Nope for Wine tasting' }));
    await vi.waitFor(() => expect(api.find('PUT', '/api/v1/ideas/m1/vote')).toHaveLength(1));
    expect(api.body('PUT', '/api/v1/ideas/m1/vote')).toEqual({ value: 'no' });

    await userEvent.click(screen.getByRole('tab', { name: 'Waiting on Alex (1)' }));
    await userEvent.click(await screen.findByRole('button', { name: 'Maybe for Cooking class' }));
    await vi.waitFor(() => expect(api.find('PUT', '/api/v1/ideas/p1/vote')).toHaveLength(1));
    expect(api.body('PUT', '/api/v1/ideas/p1/vote')).toEqual({ value: 'maybe' });
  });

  it('says votes are locked, and refetches, when the vote is refused with a 409', async () => {
    const api = ideasApi({
      'GET /api/v1/ideas/lists': lists,
      'PUT /api/v1/ideas/m1/vote': new Response(
        JSON.stringify({ error: { code: 'idea_locked', message: 'Scheduled.' } }),
        { status: 409, headers: { 'Content-Type': 'application/json' } },
      ),
    });
    renderApp('/ideas');
    await userEvent.click(await screen.findByRole('tab', { name: 'Matches (1)' }));
    await screen.findByRole('heading', { name: 'Wine tasting' });
    const before = api.find('GET', '/api/v1/ideas/lists').length;

    await userEvent.click(await screen.findByRole('button', { name: 'Nope for Wine tasting' }));
    expect(await screen.findByRole('status')).toHaveTextContent(
      "This one's already on the calendar — votes are locked.",
    );
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    await vi.waitFor(() =>
      expect(api.find('GET', '/api/v1/ideas/lists').length).toBeGreaterThan(before),
    );
  });

  it('keeps passed ideas collapsed until asked', async () => {
    ideasApi({ 'GET /api/v1/ideas/lists': lists });
    renderApp('/ideas');
    await userEvent.click(await screen.findByRole('tab', { name: 'Passed (1)' }));

    const summary = await screen.findByText('Show 1 passed idea');
    const details = summary.closest('details')!;
    expect(details).not.toHaveAttribute('open');
    await userEvent.click(summary);
    expect(details).toHaveAttribute('open');
    expect(within(details).getByText('Alex: Yes')).toBeInTheDocument();
  });

  it('lets me change a maybe to a yes, which can be a match', async () => {
    const api = ideasApi({
      'GET /api/v1/ideas/lists': lists,
      'PUT /api/v1/ideas/y1/vote': idea({
        id: 'y1',
        title: 'Escape room',
        votes: { mine: 'yes', partner: 'yes' },
      }),
    });
    renderApp('/ideas');
    await userEvent.click(await screen.findByRole('tab', { name: 'Maybes (1)' }));
    await userEvent.click(
      await screen.findByRole('button', { name: 'Yes please for Escape room' }),
    );

    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    expect(api.body('PUT', '/api/v1/ideas/y1/vote')).toEqual({ value: 'yes' });
  });

  it('edits a shared idea', async () => {
    const api = ideasApi({
      'GET /api/v1/ideas/lists': lists,
      'PATCH /api/v1/ideas/m1': lists.matches[0]!,
    });
    renderApp('/ideas');
    await userEvent.click(await screen.findByRole('tab', { name: 'Matches (1)' }));
    await userEvent.click(await screen.findByRole('button', { name: 'Edit Wine tasting' }));
    const title = screen.getByLabelText('Title');
    await userEvent.clear(title);
    await userEvent.type(title, 'Wine and cheese');
    await userEvent.click(screen.getByRole('button', { name: 'Save changes' }));

    await vi.waitFor(() => expect(api.find('PATCH', '/api/v1/ideas/m1')).toHaveLength(1));
    expect(api.body('PATCH', '/api/v1/ideas/m1')).toMatchObject({
      title: 'Wine and cheese',
      est_cost: 60,
      tags: ['creative', 'cozy'],
    });
  });

  it('makes a match a date at the first free time, and navigates to it', async () => {
    const api = ideasApi({
      'GET /api/v1/ideas/lists': lists,
      'POST /api/v1/ideas/m1/itinerary': new Response(JSON.stringify({ id: 'it9' }), {
        status: 201,
        headers: { 'Content-Type': 'application/json' },
      }),
      'GET /api/v1/itineraries/it9': { id: 'it9', title: 'Wine night' },
    });
    const router = renderApp('/ideas');
    await userEvent.click(await screen.findByRole('tab', { name: 'Matches (1)' }));
    await userEvent.click(await screen.findByRole('button', { name: 'Make Wine tasting a date' }));
    expect(screen.getByRole('link', { name: 'Find a time' })).toHaveAttribute(
      'href',
      '/calendar?idea=m1',
    );
    await userEvent.selectOptions(screen.getByLabelText('Stops'), '2');
    await userEvent.type(screen.getByLabelText('Budget (optional)'), '80');
    await userEvent.click(screen.getByRole('button', { name: 'Make it a date' }));

    await vi.waitFor(() => expect(router.state.location.pathname).toBe('/itineraries/it9'));
    expect(api.body('POST', '/api/v1/ideas/m1/itinerary')).toEqual({
      stop_count: 2,
      budget_max: 80,
    });
  });

  it('shows staged progress while making it a date and removes it afterwards', async () => {
    let release: (response: Response) => void = () => {};
    ideasApi({
      'GET /api/v1/ideas/lists': lists,
      'POST /api/v1/ideas/m1/itinerary': () =>
        new Promise<Response>((resolve) => {
          release = resolve;
        }),
    });
    renderApp('/ideas');
    await userEvent.click(await screen.findByRole('tab', { name: 'Matches (1)' }));
    await userEvent.click(await screen.findByRole('button', { name: 'Make Wine tasting a date' }));
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Make it a date' }));
    expect(await screen.findByRole('status')).toHaveTextContent('Checking venues…');
    release(
      new Response(JSON.stringify({ error: { code: 'ai_failed', message: 'x' } }), {
        status: 502,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    await screen.findByRole('alert');
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  it('sends the picked time as ISO strings', async () => {
    const api = ideasApi({
      'GET /api/v1/ideas/lists': lists,
      'POST /api/v1/ideas/m1/itinerary': new Response(JSON.stringify({ id: 'it9' }), {
        status: 201,
        headers: { 'Content-Type': 'application/json' },
      }),
      'GET /api/v1/itineraries/it9': { id: 'it9', title: 'Wine night' },
    });
    renderApp('/ideas');
    await userEvent.click(await screen.findByRole('tab', { name: 'Matches (1)' }));
    await userEvent.click(await screen.findByRole('button', { name: 'Make Wine tasting a date' }));
    await userEvent.click(screen.getByRole('button', { name: 'Pick a time' }));
    const submit = screen.getByRole('button', { name: 'Make it a date' });
    expect(submit).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Starts'), { target: { value: '2026-12-12T19:00' } });
    fireEvent.change(screen.getByLabelText('Ends (optional)'), {
      target: { value: '2026-12-12T22:00' },
    });
    await userEvent.click(submit);

    await vi.waitFor(() => expect(api.find('POST', '/api/v1/ideas/m1/itinerary')).toHaveLength(1));
    expect(api.body('POST', '/api/v1/ideas/m1/itinerary')).toEqual({
      starts_at: new Date('2026-12-12T19:00').toISOString(),
      ends_at: new Date('2026-12-12T22:00').toISOString(),
    });
  });

  it("shows the planner's message on a 503 and offers no button on expired ideas", async () => {
    ideasApi({
      'GET /api/v1/ideas/lists': {
        ...lists,
        passed: [idea({ id: 'x1', title: 'Bowling', status: 'expired' })],
      },
      'POST /api/v1/ideas/m1/itinerary': new Response(
        JSON.stringify({ error: { code: 'ai_unavailable', message: 'The planner is resting.' } }),
        { status: 503, headers: { 'Content-Type': 'application/json' } },
      ),
    });
    renderApp('/ideas');
    await userEvent.click(await screen.findByRole('tab', { name: 'Matches (1)' }));
    await userEvent.click(await screen.findByRole('button', { name: 'Make Wine tasting a date' }));
    await userEvent.click(screen.getByRole('button', { name: 'Make it a date' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('The planner is resting.');
    await userEvent.click(screen.getByRole('button', { name: 'Close make it a date' }));

    await userEvent.click(screen.getByRole('tab', { name: 'Passed (1)' }));
    await userEvent.click(await screen.findByText(/Show 1 passed idea/));
    expect(screen.queryByRole('button', { name: 'Make Bowling a date' })).not.toBeInTheDocument();
  });

  it('shows friendly empty states', async () => {
    ideasApi();
    renderApp('/ideas');
    await userEvent.click(await screen.findByRole('tab', { name: 'Matches (0)' }));
    expect(await screen.findByText('No matches yet')).toBeInTheDocument();
  });
});

describe('IdeasPage my ideas', () => {
  const mine = idea({
    id: 'k1',
    title: 'Moonlit walk',
    visibility: 'private',
    created_by_me: true,
  });

  it('creates a private idea from the form', async () => {
    const api = ideasApi({ 'POST /api/v1/ideas': mine });
    renderApp('/ideas');
    await userEvent.click(await screen.findByRole('tab', { name: 'My ideas' }));

    expect(await screen.findByText('Only you can see this')).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText('Title'), 'Moonlit walk');
    await userEvent.type(screen.getByLabelText('Description'), 'Down by the river');
    await userEvent.type(screen.getByLabelText('Estimated cost (USD)'), '15.50');
    await userEvent.type(screen.getByLabelText('Tags'), 'romantic{Enter}');
    await userEvent.click(screen.getByRole('button', { name: 'Save idea' }));

    await vi.waitFor(() => expect(api.find('POST', '/api/v1/ideas')).toHaveLength(1));
    expect(api.body('POST', '/api/v1/ideas')).toEqual({
      title: 'Moonlit walk',
      pitch: 'Down by the river',
      est_cost: 15.5,
      tags: ['romantic'],
    });
    await vi.waitFor(() => expect(screen.getByLabelText('Title')).toHaveValue(''));
  });

  it('stops adding tags at the API limit of ten', async () => {
    ideasApi();
    renderApp('/ideas');
    await userEvent.click(await screen.findByRole('tab', { name: 'My ideas' }));

    const tags = await screen.findByLabelText('Tags');
    for (let n = 1; n <= 10; n += 1) await userEvent.type(tags, `tag${n}{Enter}`);
    expect(screen.getByRole('button', { name: 'Remove tag10 from Tags' })).toBeInTheDocument();
    expect(tags).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Add to Tags' })).toBeDisabled();
  });

  it('asks before sharing, then shares', async () => {
    const api = ideasApi({
      'GET /api/v1/ideas': [mine],
      'POST /api/v1/ideas/k1/share': { ...mine, visibility: 'shared' },
    });
    renderApp('/ideas');
    await userEvent.click(await screen.findByRole('tab', { name: 'My ideas' }));

    await userEvent.click(await screen.findByRole('button', { name: 'Share Moonlit walk' }));
    expect(screen.getByText(/can't go back to private/)).toBeInTheDocument();
    expect(api.find('POST', '/api/v1/ideas/k1/share')).toHaveLength(0);

    await userEvent.click(screen.getByRole('button', { name: 'Not yet' }));
    expect(api.find('POST', '/api/v1/ideas/k1/share')).toHaveLength(0);

    await userEvent.click(screen.getByRole('button', { name: 'Share Moonlit walk' }));
    await userEvent.click(screen.getByRole('button', { name: 'Yes, share it' }));
    await vi.waitFor(() => expect(api.find('POST', '/api/v1/ideas/k1/share')).toHaveLength(1));
  });

  it('makes one of my private ideas a date', async () => {
    const api = ideasApi({
      'GET /api/v1/ideas': [mine],
      'POST /api/v1/ideas/k1/itinerary': new Response(JSON.stringify({ id: 'it2' }), {
        status: 201,
        headers: { 'Content-Type': 'application/json' },
      }),
      'GET /api/v1/itineraries/it2': { id: 'it2', title: 'Walk' },
    });
    const router = renderApp('/ideas');
    await userEvent.click(await screen.findByRole('tab', { name: 'My ideas' }));
    await userEvent.click(await screen.findByRole('button', { name: 'Make Moonlit walk a date' }));
    await userEvent.click(screen.getByRole('button', { name: 'Make it a date' }));
    await vi.waitFor(() => expect(router.state.location.pathname).toBe('/itineraries/it2'));
    expect(api.body('POST', '/api/v1/ideas/k1/itinerary')).toEqual({});
  });

  it('archives an idea', async () => {
    const api = ideasApi({
      'GET /api/v1/ideas': [mine],
      'DELETE /api/v1/ideas/k1': new Response(null, { status: 204 }),
    });
    renderApp('/ideas');
    await userEvent.click(await screen.findByRole('tab', { name: 'My ideas' }));
    await userEvent.click(await screen.findByRole('button', { name: 'Archive Moonlit walk' }));
    await vi.waitFor(() => expect(api.find('DELETE', '/api/v1/ideas/k1')).toHaveLength(1));
  });

  it('edits one of my ideas', async () => {
    const api = ideasApi({
      'GET /api/v1/ideas': [mine],
      'PATCH /api/v1/ideas/k1': mine,
    });
    renderApp('/ideas');
    await userEvent.click(await screen.findByRole('tab', { name: 'My ideas' }));
    await userEvent.click(await screen.findByRole('button', { name: 'Edit Moonlit walk' }));
    const [, editTitle] = screen.getAllByLabelText('Title');
    await userEvent.type(editTitle!, '!');
    await userEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    await vi.waitFor(() => expect(api.find('PATCH', '/api/v1/ideas/k1')).toHaveLength(1));
    expect(api.body('PATCH', '/api/v1/ideas/k1')).toMatchObject({ title: 'Moonlit walk!' });
  });
});
