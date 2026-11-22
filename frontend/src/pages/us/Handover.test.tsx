import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { Schemas } from '../../api/client';
import {
  constraints,
  couple,
  husbandProfile,
  profile,
  session,
  wifeProfile,
} from '../../test/fixtures';
import { mockApi, type Handler } from '../../test/mockApi';
import { jsonResponse, renderApp } from '../../test/renderApp';

afterEach(() => {
  vi.unstubAllGlobals();
});

const noAvailability = { windows: [], blocks: [] };
// The session user is u1; "u2" is the partner.
const handedOver = profile({ ...husbandProfile, edited_by_user_id: 'u2' });

function suggestion(
  overrides: Partial<Schemas['ProfileSuggestionOut']> = {},
): Schemas['ProfileSuggestionOut'] {
  return {
    id: 's1',
    profile_id: 'p-husband',
    author_user_id: 'u2',
    mine: false,
    changes: { loves: 'Pottery', liked_categories: ['food', 'arts'] },
    note: null,
    status: 'pending',
    created_at: '2026-01-02T00:00:00Z',
    decided_at: null,
    ...overrides,
  };
}

function api(extra: Record<string, Handler> = {}) {
  return mockApi({
    'GET /api/v1/auth/session': session('Sam'),
    'GET /api/v1/meta': { demo_mode: false, version: 'test', no_login: true },
    'GET /api/v1/profiles': [husbandProfile, wifeProfile],
    'GET /api/v1/profiles/p-husband/suggestions': [],
    'GET /api/v1/profiles/p-wife/suggestions': [],
    'GET /api/v1/profiles/p-husband/availability': noAvailability,
    'GET /api/v1/profiles/p-wife/availability': noAvailability,
    'GET /api/v1/couple': couple,
    'GET /api/v1/couple/constraints': constraints,
    'GET /api/v1/couple/important-dates': [],
    'GET /api/v1/notes': [],
    ...extra,
  });
}

const BANNER = 'Alex filled this in for you. Look it over and make it yours.';

describe('hand-over banner', () => {
  it('shows on my own profile when my partner filled it in and I have not confirmed', async () => {
    api({ 'GET /api/v1/profiles': [handedOver, wifeProfile] });
    renderApp('/us');
    const mine = (await screen.findByRole('heading', { name: 'You' })).closest('section')!;
    expect(await within(mine).findByText(BANNER)).toBeInTheDocument();
    expect(within(mine).getByRole('button', { name: 'Looks right' })).toBeInTheDocument();
    expect(screen.getAllByText(BANNER)).toHaveLength(1);
  });

  it.each([
    ['I edited it myself', { edited_by_user_id: 'u1' }],
    ['nobody edited it', { edited_by_user_id: null }],
    ['it is already confirmed', { confirmed_by_subject_at: '2026-01-03T00:00:00Z' }],
  ])('stays hidden when %s', async (_label, flags) => {
    api({ 'GET /api/v1/profiles': [profile({ ...handedOver, ...flags }), wifeProfile] });
    renderApp('/us');
    await screen.findByRole('heading', { name: 'You' });
    expect(screen.queryByText(BANNER)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Looks right' })).not.toBeInTheDocument();
  });

  it('is not shown on the partner profile even if someone else edited it', async () => {
    api({
      'GET /api/v1/profiles': [
        husbandProfile,
        profile({ ...wifeProfile, edited_by_user_id: 'u2', subject_joined: true }),
      ],
    });
    renderApp('/us');
    await screen.findByRole('heading', { name: 'You' });
    expect(screen.queryByRole('button', { name: 'Looks right' })).not.toBeInTheDocument();
  });

  it('confirms through the route and disappears after the refetch', async () => {
    let confirmed = false;
    const confirmedProfile = profile({
      ...handedOver,
      confirmed_by_subject_at: '2026-01-03T00:00:00Z',
    });
    const mock = api({
      'GET /api/v1/profiles': () => [confirmed ? confirmedProfile : handedOver, wifeProfile],
      'POST /api/v1/profiles/p-husband/confirm': () => {
        confirmed = true;
        return confirmedProfile;
      },
    });
    renderApp('/us');
    await userEvent.click(await screen.findByRole('button', { name: 'Looks right' }));
    await vi.waitFor(() =>
      expect(mock.find('POST', '/api/v1/profiles/p-husband/confirm')).toHaveLength(1),
    );
    await vi.waitFor(() => expect(screen.queryByText(BANNER)).not.toBeInTheDocument());
  });

  it('"Edit" opens the profile form and the banner stays until confirmed', async () => {
    const mock = api({
      'GET /api/v1/profiles': [handedOver, wifeProfile],
      'PUT /api/v1/profiles/p-husband': handedOver,
    });
    renderApp('/us');
    const mine = (await screen.findByRole('heading', { name: 'You' })).closest('section')!;
    await userEvent.click(await within(mine).findByRole('button', { name: 'Edit' }));
    await userEvent.click(screen.getByRole('button', { name: 'Save profile' }));
    await vi.waitFor(() => expect(mock.find('PUT', '/api/v1/profiles/p-husband')).toHaveLength(1));
    expect(await screen.findByText(BANNER)).toBeInTheDocument();
    expect(mock.find('POST', '/api/v1/profiles/p-husband/confirm')).toHaveLength(0);
  });
});

describe('suggestions review', () => {
  it('renders each change as "Field: old → new" with the note', async () => {
    api({
      'GET /api/v1/profiles': [
        profile({ ...husbandProfile, liked_categories: ['food'] }),
        wifeProfile,
      ],
      'GET /api/v1/profiles/p-husband/suggestions': [suggestion({ note: 'You love clay' })],
    });
    renderApp('/us');
    expect(await screen.findByText('Loves: Not set → Pottery')).toBeInTheDocument();
    expect(screen.getByText('Likes: Food → Food, Arts')).toBeInTheDocument();
    expect(screen.getByText(/You love clay/)).toBeInTheDocument();
  });

  it('is hidden when there are no pending suggestions', async () => {
    api({
      'GET /api/v1/profiles/p-husband/suggestions': [suggestion({ status: 'declined' })],
    });
    renderApp('/us');
    await screen.findByRole('heading', { name: 'You' });
    expect(screen.queryByRole('heading', { name: /Suggested changes/ })).not.toBeInTheDocument();
  });

  it('accepts through the accept route and disables the buttons while it is pending', async () => {
    let release: (v: unknown) => void = () => {};
    const held = new Promise((resolve) => (release = resolve));
    const path = '/api/v1/profiles/p-husband/suggestions/s1/accept';
    const mock = api({
      'GET /api/v1/profiles/p-husband/suggestions': [suggestion()],
      [`POST ${path}`]: () => held,
    });
    renderApp('/us');
    await userEvent.click(await screen.findByRole('button', { name: 'Accept' }));
    await vi.waitFor(() => expect(mock.find('POST', path)).toHaveLength(1));
    expect(screen.getByRole('button', { name: 'Accept' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Decline' })).toBeDisabled();
    release(husbandProfile);
    await vi.waitFor(() => expect(screen.getByRole('button', { name: 'Accept' })).toBeEnabled());
  });

  it('declines through the decline route', async () => {
    const path = '/api/v1/profiles/p-husband/suggestions/s1/decline';
    const mock = api({
      'GET /api/v1/profiles/p-husband/suggestions': [suggestion()],
      [`POST ${path}`]: suggestion({ status: 'declined' }),
    });
    renderApp('/us');
    await userEvent.click(await screen.findByRole('button', { name: 'Decline' }));
    await vi.waitFor(() => expect(mock.find('POST', path)).toHaveLength(1));
    expect(mock.find('POST', '/api/v1/profiles/p-husband/suggestions/s1/accept')).toHaveLength(0);
  });
});

const joinedPartner = profile({ ...wifeProfile, can_edit: false, subject_joined: true });

describe('suggest a change', () => {
  it('shows for a joined partner profile I cannot edit', async () => {
    api({ 'GET /api/v1/profiles': [husbandProfile, joinedPartner] });
    renderApp('/us');
    expect(await screen.findByRole('button', { name: 'Suggest a change' })).toBeInTheDocument();
  });

  it.each([
    ['can edit it', profile({ ...wifeProfile, can_edit: true, subject_joined: true })],
    ['has not joined', profile({ ...wifeProfile, can_edit: false, subject_joined: false })],
  ])('is hidden when the partner %s', async (_label, partner) => {
    api({ 'GET /api/v1/profiles': [husbandProfile, partner] });
    renderApp('/us');
    await screen.findByRole('heading', { name: 'You' });
    expect(screen.queryByRole('button', { name: 'Suggest a change' })).not.toBeInTheDocument();
  });

  it('sends only the changed fields, with the note', async () => {
    const path = '/api/v1/profiles/p-wife/suggestions';
    const mock = api({
      'GET /api/v1/profiles': [husbandProfile, profile({ ...joinedPartner, loves: 'Tulips' })],
      [`POST ${path}`]: jsonResponse(suggestion({ profile_id: 'p-wife', mine: true }), 201),
    });
    renderApp('/us');
    await userEvent.click(await screen.findByRole('button', { name: 'Suggest a change' }));
    const dialog = screen.getByRole('dialog', { name: 'Suggest a change for Alex' });
    expect(within(dialog).getByRole('button', { name: 'Close suggestion' })).toHaveFocus();
    const send = within(dialog).getByRole('button', { name: 'Send suggestion' });
    expect(send).toBeDisabled();

    await userEvent.click(within(dialog).getAllByRole('button', { name: 'Food' })[0]!);
    await userEvent.type(within(dialog).getByLabelText('Note (optional)'), 'She told me');
    await userEvent.click(send);

    await vi.waitFor(() => expect(mock.find('POST', path)).toHaveLength(1));
    expect(mock.body('POST', path)).toEqual({
      changes: { liked_categories: ['food'] },
      note: 'She told me',
    });
    await vi.waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });

  it('lists my pending suggestions as waiting on the partner', async () => {
    api({
      'GET /api/v1/profiles': [husbandProfile, joinedPartner],
      'GET /api/v1/profiles/p-wife/suggestions': [
        suggestion({ profile_id: 'p-wife', mine: true, changes: { loves: 'Tulips' } }),
      ],
    });
    renderApp('/us');
    expect(await screen.findByText('Waiting on Alex')).toBeInTheDocument();
    expect(screen.getByText('Loves: Not set → Tulips')).toBeInTheDocument();
  });

  it('on 409 edit_directly refetches the profiles and says I can edit directly', async () => {
    let direct = false;
    const mock = api({
      'GET /api/v1/profiles': () => [
        husbandProfile,
        direct ? profile({ ...joinedPartner, can_edit: true }) : joinedPartner,
      ],
      'POST /api/v1/profiles/p-wife/suggestions': () => {
        direct = true;
        return jsonResponse(
          { error: { code: 'edit_directly', message: 'You can edit this profile directly.' } },
          409,
        );
      },
    });
    renderApp('/us');
    await userEvent.click(await screen.findByRole('button', { name: 'Suggest a change' }));
    const dialog = screen.getByRole('dialog');
    await userEvent.click(within(dialog).getAllByRole('button', { name: 'Food' })[0]!);
    await userEvent.click(within(dialog).getByRole('button', { name: 'Send suggestion' }));

    expect(await screen.findByText('You can edit this directly now.')).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Suggest a change' })).not.toBeInTheDocument();
    expect(await screen.findByRole('button', { name: "Edit Alex's profile" })).toBeInTheDocument();
    expect(mock.find('GET', '/api/v1/profiles').length).toBeGreaterThan(1);
  });
});
