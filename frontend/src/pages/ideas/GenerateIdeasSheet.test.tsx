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
    await userEvent.click(sheet.getByRole('button', { name: 'Indoor' }));
    await userEvent.type(sheet.getByLabelText('Budget max, USD (optional)'), '50');
    await userEvent.selectOptions(sheet.getByLabelText('How many ideas'), '6');
    await userEvent.click(sheet.getByRole('checkbox', { name: 'Surprise me' }));
    const decks = mock.find('GET', '/api/v1/deck').length;
    await userEvent.click(sheet.getByRole('button', { name: 'Generate ideas' }));

    await waitFor(() =>
      expect(mock.body('POST', '/api/v1/ideas/generate')).toEqual({
        mode: 'plan',
        vibe: 'cozy',
        category: 'food',
        indoor_outdoor: 'indoor',
        budget_max: 50,
        count: 6,
        surprise_me: true,
      }),
    );
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    await waitFor(() => expect(mock.find('GET', '/api/v1/deck').length).toBeGreaterThan(decks));
  });

  it('offers 5 to 10 ideas, matching what the planner accepts', async () => {
    api();
    renderApp('/ideas');
    const sheet = await openSheet();
    const options = within(sheet.getByLabelText('How many ideas')).getAllByRole('option');
    expect(options.map((o) => o.textContent)).toEqual(['5', '6', '8', '10']);
    expect(sheet.getByLabelText('How many ideas')).toHaveValue('8');
  });

  it('sends null for anything left open, with either meaning no preference', async () => {
    const mock = api();
    renderApp('/ideas');
    const sheet = await openSheet();
    await userEvent.click(sheet.getByRole('button', { name: 'Generate ideas' }));
    await waitFor(() =>
      expect(mock.body('POST', '/api/v1/ideas/generate')).toEqual({
        mode: 'match',
        vibe: null,
        category: null,
        indoor_outdoor: null,
        budget_max: null,
        count: 8,
        surprise_me: false,
      }),
    );
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
});
