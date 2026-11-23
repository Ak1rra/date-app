import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { husbandProfile, session, wifeProfile } from '../../test/fixtures';
import { mockApi, type Handler } from '../../test/mockApi';
import { jsonResponse, renderApp } from '../../test/renderApp';

afterEach(() => {
  vi.unstubAllGlobals();
});

const emptyLists = { matches: [], maybes: [], pending_partner: [], passed: [] };

function api(extra: Record<string, Handler> = {}, partnerJoined = true) {
  return mockApi({
    'GET /api/v1/auth/session': session('Sam'),
    'GET /api/v1/meta': { demo_mode: false, version: 'test', no_login: true },
    'GET /api/v1/profiles': [husbandProfile, { ...wifeProfile, subject_joined: true }],
    'GET /api/v1/deck': { cards: [], partner_joined: partnerJoined },
    'GET /api/v1/ideas/lists': emptyLists,
    'GET /api/v1/ideas': [],
    'POST /api/v1/ideas/generate': { ideas: [], degraded: [] },
    ...extra,
  });
}

/** The header button comes first; the empty deck has a second one of the same name. */
function headerButton() {
  const [first] = screen.getAllByRole('button', { name: 'Generate ideas' });
  if (!first) throw new Error('No Generate ideas button');
  return first;
}

async function openSheet() {
  await waitFor(() => expect(headerButton()).toBeEnabled());
  await userEvent.click(headerButton());
  return within(await screen.findByRole('dialog', { name: 'Generate ideas' }));
}

const apiError = (status: number, code: string, message = 'The planner is resting right now.') =>
  jsonResponse({ error: { code, message } }, status);

const FILTERED = "The planner couldn't come up with ideas that fit. Try loosening the filters.";

describe('GenerateIdeasSheet', () => {
  it('sends the chosen filters, refreshes the ideas and closes', async () => {
    const mock = api();
    renderApp('/ideas');
    const sheet = await openSheet();
    expect(sheet.getByRole('button', { name: 'For us to swipe' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );

    await userEvent.click(sheet.getByRole('button', { name: 'Just for me' }));
    await userEvent.type(sheet.getByLabelText('Vibe (optional)'), '  cozy  ');
    await userEvent.selectOptions(sheet.getByLabelText('Category'), 'food');
    await userEvent.click(sheet.getByRole('button', { name: 'Outdoor' }));
    await userEvent.type(sheet.getByLabelText('Budget max, USD (optional)'), '50');
    await userEvent.clear(sheet.getByLabelText('How many ideas'));
    await userEvent.type(sheet.getByLabelText('How many ideas'), '6');
    await userEvent.click(sheet.getByRole('checkbox', { name: 'Surprise me' }));
    const decks = mock.find('GET', '/api/v1/deck').length;
    await userEvent.click(sheet.getByRole('button', { name: 'Generate ideas' }));

    await waitFor(() =>
      expect(mock.body('POST', '/api/v1/ideas/generate')).toEqual({
        mode: 'plan',
        vibe: 'cozy',
        category: 'food',
        where: ['indoor'],
        budget_max: 50,
        count: 6,
        surprise_me: true,
      }),
    );
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    await waitFor(() => expect(mock.find('GET', '/api/v1/deck').length).toBeGreaterThan(decks));
  });

  it('starts the count at 8 as a number box', async () => {
    api();
    renderApp('/ideas');
    const sheet = await openSheet();
    expect(sheet.getByLabelText('How many ideas')).toHaveValue(8);
  });

  it('sends a typed count of 15', async () => {
    const mock = api();
    renderApp('/ideas');
    const sheet = await openSheet();
    const box = sheet.getByLabelText('How many ideas');
    await userEvent.clear(box);
    await userEvent.type(box, '15');
    await userEvent.click(sheet.getByRole('button', { name: 'Generate ideas' }));
    await waitFor(() =>
      expect(mock.body('POST', '/api/v1/ideas/generate')).toMatchObject({ count: 15 }),
    );
  });

  it.each(['25', '0', '', '2.5'])('blocks a count of "%s" with a hint', async (value) => {
    const mock = api();
    renderApp('/ideas');
    const sheet = await openSheet();
    const box = sheet.getByLabelText('How many ideas');
    await userEvent.clear(box);
    if (value) await userEvent.type(box, value);
    expect(sheet.getByText('Enter a whole number from 1 to 20.')).toBeInTheDocument();
    expect(sheet.getByRole('button', { name: 'Generate ideas' })).toBeDisabled();
    expect(mock.find('POST', '/api/v1/ideas/generate')).toHaveLength(0);
  });

  it('sends null for anything left open and defaults to indoor and outdoor', async () => {
    const mock = api();
    renderApp('/ideas');
    const sheet = await openSheet();
    await userEvent.click(sheet.getByRole('button', { name: 'Generate ideas' }));
    await waitFor(() =>
      expect(mock.body('POST', '/api/v1/ideas/generate')).toEqual({
        mode: 'match',
        vibe: null,
        category: null,
        where: ['indoor', 'outdoor'],
        budget_max: null,
        count: 8,
        surprise_me: false,
      }),
    );
  });

  it('shows Indoor and Outdoor on by default, with no Either', async () => {
    api();
    renderApp('/ideas');
    const sheet = await openSheet();
    expect(sheet.getByRole('button', { name: 'Indoor' })).toHaveAttribute('aria-pressed', 'true');
    expect(sheet.getByRole('button', { name: 'Outdoor' })).toHaveAttribute('aria-pressed', 'true');
    expect(sheet.getByRole('button', { name: 'At home' })).toHaveAttribute('aria-pressed', 'false');
    expect(sheet.queryByRole('button', { name: 'Either' })).not.toBeInTheDocument();
    expect(sheet.getByText("We'll get you out of the house.")).toBeInTheDocument();
  });

  it('sends At home when it is selected', async () => {
    const mock = api();
    renderApp('/ideas');
    const sheet = await openSheet();
    await userEvent.click(sheet.getByRole('button', { name: 'At home' }));
    expect(sheet.queryByText("We'll get you out of the house.")).not.toBeInTheDocument();
    await userEvent.click(sheet.getByRole('button', { name: 'Generate ideas' }));
    await waitFor(() =>
      expect(mock.body('POST', '/api/v1/ideas/generate')).toMatchObject({
        where: ['indoor', 'outdoor', 'at_home'],
      }),
    );
  });

  it('blocks an empty Where with a hint', async () => {
    const mock = api();
    renderApp('/ideas');
    const sheet = await openSheet();
    await userEvent.click(sheet.getByRole('button', { name: 'Indoor' }));
    await userEvent.click(sheet.getByRole('button', { name: 'Outdoor' }));
    expect(sheet.getByText('Pick at least one.')).toBeInTheDocument();
    expect(sheet.getByRole('button', { name: 'Generate ideas' })).toBeDisabled();
    expect(mock.find('POST', '/api/v1/ideas/generate')).toHaveLength(0);
  });

  it('defaults to just-for-me until the partner has joined', async () => {
    api({}, false);
    renderApp('/ideas');
    const sheet = await openSheet();
    expect(sheet.getByRole('button', { name: 'Just for me' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
  });

  it('says so while it is working', async () => {
    api({ 'POST /api/v1/ideas/generate': () => new Promise<never>(() => {}) });
    renderApp('/ideas');
    const sheet = await openSheet();
    await userEvent.click(sheet.getByRole('button', { name: 'Generate ideas' }));
    expect(await sheet.findByRole('button', { name: 'Dreaming up ideas…' })).toBeDisabled();
  });

  it('shows staged progress while generating and removes it afterwards', async () => {
    let release: (response: Response) => void = () => {};
    api({
      'POST /api/v1/ideas/generate': () =>
        new Promise<Response>((resolve) => {
          release = resolve;
        }),
    });
    renderApp('/ideas');
    const sheet = await openSheet();
    expect(sheet.queryByRole('status')).not.toBeInTheDocument();
    await userEvent.click(sheet.getByRole('button', { name: 'Generate ideas' }));
    expect(await sheet.findByRole('status')).toHaveTextContent('Finding spots…');
    release(apiError(503, 'provider_unavailable'));
    await sheet.findByRole('alert');
    expect(sheet.queryByRole('status')).not.toBeInTheDocument();
  });

  it.each([
    ['daily', 8, /You've used today's AI planning\. It resets /],
    ['monthly', 240, /You've used this month's AI planning\. It resets /],
  ])('explains a %s cap with the reset time', async (_name, hours, message) => {
    const reset = new Date(Date.now() + hours * 3_600_000).toISOString();
    api({
      'POST /api/v1/ideas/generate': () =>
        jsonResponse({ error: { code: 'ai_cap_reached', message: 'raw', reset_at: reset } }, 429),
    });
    renderApp('/ideas');
    const sheet = await openSheet();
    await userEvent.click(sheet.getByRole('button', { name: 'Generate ideas' }));
    expect(await sheet.findByRole('alert')).toHaveTextContent(message);
  });

  it.each(['0', '-5', '10001'])('blocks a budget of %s with a hint', async (value) => {
    const mock = api();
    renderApp('/ideas');
    const sheet = await openSheet();
    await userEvent.type(sheet.getByLabelText('Budget max, USD (optional)'), value);
    expect(
      sheet.getByText('Enter more than 0 and up to 10,000, or leave it blank.'),
    ).toBeInTheDocument();
    expect(sheet.getByRole('button', { name: 'Generate ideas' })).toBeDisabled();
    expect(mock.find('POST', '/api/v1/ideas/generate')).toHaveLength(0);
  });

  it('shows a rough-details notice before closing when something was degraded', async () => {
    api({ 'POST /api/v1/ideas/generate': { ideas: [], degraded: ['places'] } });
    renderApp('/ideas');
    const sheet = await openSheet();
    await userEvent.click(sheet.getByRole('button', { name: 'Generate ideas' }));
    expect(
      await sheet.findByText(
        "Some live details (venues, weather or events) couldn't be checked, so a few ideas may be rough.",
      ),
    ).toBeInTheDocument();
    await userEvent.click(sheet.getByRole('button', { name: 'Done' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it.each([
    [503, 'provider_unavailable', /The planner is resting right now/],
    [502, 'ai_failed', /Try loosening the filters/],
    [429, 'rate_limited', /You've hit the AI limit for now/],
  ])('shows a friendly message for a %i %s', async (status, code, message) => {
    api({
      'POST /api/v1/ideas/generate': () =>
        apiError(status, code, code === 'ai_failed' ? FILTERED : undefined),
    });
    renderApp('/ideas');
    const sheet = await openSheet();
    await userEvent.click(sheet.getByRole('button', { name: 'Generate ideas' }));
    const alert = await sheet.findByRole('alert');
    expect(alert).toHaveTextContent(message);
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });

  it('opens from the empty deck in match mode', async () => {
    api();
    renderApp('/ideas');
    const panel = within(await screen.findByRole('tabpanel'));
    await screen.findByText("You're all caught up");
    await userEvent.click(panel.getByRole('button', { name: 'Generate ideas' }));
    const sheet = within(await screen.findByRole('dialog', { name: 'Generate ideas' }));
    expect(sheet.getByRole('button', { name: 'For us to swipe' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    await userEvent.click(sheet.getByRole('button', { name: 'Close generate ideas' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('stays open, with its degraded notice, when the deck refetch after generating fails', async () => {
    let deckCalls = 0;
    api({
      'GET /api/v1/deck': () => {
        deckCalls += 1;
        return deckCalls === 1
          ? { cards: [], partner_joined: true }
          : apiError(500, 'internal_error');
      },
      'POST /api/v1/ideas/generate': { ideas: [], degraded: ['places'] },
    });
    renderApp('/ideas');
    const sheet = await openSheet();
    await userEvent.click(sheet.getByRole('button', { name: 'Generate ideas' }));
    await waitFor(() => expect(deckCalls).toBeGreaterThan(1));
    // The failed refetch replaces the deck with its error, but not the sheet.
    expect(await screen.findByRole('button', { name: 'Try again' })).toBeInTheDocument();
    expect(
      screen.getByText(
        "Some live details (venues, weather or events) couldn't be checked, so a few ideas may be rough.",
      ),
    ).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Done' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('offers the header button only once the deck is known, then opens in match mode', async () => {
    let release: () => void = () => {};
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    api({
      'GET /api/v1/deck': async () => {
        await gate;
        return { cards: [], partner_joined: true };
      },
    });
    renderApp('/ideas');
    const header = await screen.findByRole('button', { name: 'Generate ideas' });
    expect(header).toBeDisabled();
    await userEvent.click(header);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

    release();
    await waitFor(() => expect(headerButton()).toBeEnabled());
    const sheet = await openSheet();
    expect(sheet.getByRole('button', { name: 'For us to swipe' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
  });

  const made = (n: number) => Array.from({ length: n }, (_, i) => ({ id: `new-${i}` }));

  it('switches to My ideas and says where "Just for me" ideas went', async () => {
    api({ 'POST /api/v1/ideas/generate': { ideas: made(3), degraded: [] } });
    renderApp('/ideas');
    const sheet = await openSheet();
    await userEvent.click(sheet.getByRole('button', { name: 'Just for me' }));
    await userEvent.click(sheet.getByRole('button', { name: 'Generate ideas' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(screen.getByRole('status')).toHaveTextContent('Added 3 ideas to My ideas.');
    expect(screen.getByRole('tab', { name: 'My ideas' })).toHaveAttribute('aria-selected', 'true');
  });

  it('stays on the Deck and says so for ideas to swipe, singular for one', async () => {
    api({ 'POST /api/v1/ideas/generate': { ideas: made(1), degraded: [] } });
    renderApp('/ideas');
    const sheet = await openSheet();
    await userEvent.click(sheet.getByRole('button', { name: 'Generate ideas' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(screen.getByRole('status')).toHaveTextContent('Added 1 idea to the Deck.');
    expect(screen.getByRole('tab', { name: 'Deck' })).toHaveAttribute('aria-selected', 'true');
  });

  it('dismisses the confirmation, and clears it when generating again', async () => {
    api({ 'POST /api/v1/ideas/generate': { ideas: made(2), degraded: [] } });
    renderApp('/ideas');
    let sheet = await openSheet();
    await userEvent.click(sheet.getByRole('button', { name: 'Generate ideas' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: 'Dismiss notice' }));
    expect(screen.queryByRole('status')).not.toBeInTheDocument();

    await userEvent.click(headerButton());
    await screen.findByRole('dialog', { name: 'Generate ideas' });
    await userEvent.click(screen.getByRole('button', { name: 'Close generate ideas' }));
    sheet = await openSheet();
    await userEvent.click(sheet.getByRole('button', { name: 'Generate ideas' }));
    await screen.findByRole('button', { name: 'Dismiss notice' });
    await userEvent.click(headerButton());
    await screen.findByRole('dialog', { name: 'Generate ideas' });
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  it('shows the added count in the degraded panel', async () => {
    api({ 'POST /api/v1/ideas/generate': { ideas: made(4), degraded: ['places'] } });
    renderApp('/ideas');
    const sheet = await openSheet();
    await userEvent.click(sheet.getByRole('button', { name: 'Generate ideas' }));
    expect(await sheet.findByText('Added 4 ideas to the Deck.')).toBeInTheDocument();
    expect(sheet.getByRole('button', { name: 'Done' })).toBeInTheDocument();
  });

  it('explains where the ideas go, following the mode', async () => {
    api();
    renderApp('/ideas');
    const sheet = await openSheet();
    expect(sheet.getByText('They go to the Deck for you both to swipe.')).toBeInTheDocument();
    await userEvent.click(sheet.getByRole('button', { name: 'Just for me' }));
    expect(sheet.getByText('They go to My ideas, only you can see them.')).toBeInTheDocument();
    expect(sheet.queryByText('They go to the Deck for you both to swipe.')).not.toBeInTheDocument();
  });
});
