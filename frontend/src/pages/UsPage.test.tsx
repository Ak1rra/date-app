import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  constraints,
  couple,
  husbandProfile,
  profile,
  session,
  wifeProfile,
} from '../test/fixtures';
import { mockApi, type Handler } from '../test/mockApi';
import { renderApp } from '../test/renderApp';

afterEach(() => {
  vi.unstubAllGlobals();
});

const noAvailability = { windows: [], blocks: [] };

function usApi(extra: Record<string, Handler> = {}) {
  return mockApi({
    'GET /api/v1/auth/session': session('Sam'),
    'GET /api/v1/meta': { demo_mode: false, version: 'test', no_login: true },
    'GET /api/v1/profiles': [husbandProfile, wifeProfile],
    'GET /api/v1/profiles/p-husband/availability': noAvailability,
    'GET /api/v1/profiles/p-wife/availability': noAvailability,
    'GET /api/v1/couple': couple,
    'GET /api/v1/couple/constraints': constraints,
    'GET /api/v1/couple/important-dates': [],
    'GET /api/v1/notes': [],
    ...extra,
  });
}

describe('UsPage', () => {
  it('shows both profiles with their own headings and person colours', async () => {
    usApi();
    renderApp('/us');

    const mine = (await screen.findByRole('heading', { name: 'You' })).closest('section')!;
    const theirs = screen
      .getByRole('heading', { name: "Here's what you know about Alex" })
      .closest('section')!;
    expect(mine).toHaveClass('bg-husband-soft', 'border-husband');
    expect(theirs).toHaveClass('bg-wife-soft', 'border-wife');
    expect(within(mine).getByText('Husband (you)')).toBeInTheDocument();
    expect(within(theirs).getByText('Wife')).toBeInTheDocument();
  });

  it('saves an edited profile with the whole body', async () => {
    const api = usApi({ 'PUT /api/v1/profiles/p-husband': husbandProfile });
    renderApp('/us');

    await userEvent.click(await screen.findByRole('button', { name: 'Edit my profile' }));
    const chip = screen.getAllByRole('button', { name: 'Food' })[0]!;
    expect(chip).toHaveAttribute('aria-pressed', 'false');
    await userEvent.click(chip);
    expect(chip).toHaveAttribute('aria-pressed', 'true');
    await userEvent.click(screen.getByRole('button', { name: 'Save profile' }));

    await vi.waitFor(() => expect(api.find('PUT', '/api/v1/profiles/p-husband')).toHaveLength(1));
    expect(api.body('PUT', '/api/v1/profiles/p-husband')).toMatchObject({
      display_name: 'Sam',
      liked_categories: ['food'],
    });
  });

  it('is read-only when the profile cannot be edited', async () => {
    usApi({
      'GET /api/v1/profiles': [
        husbandProfile,
        profile({ ...wifeProfile, can_edit: false, subject_joined: true, loves: 'Pottery' }),
      ],
    });
    renderApp('/us');

    const theirs = (
      await screen.findByRole('heading', { name: "Here's what you know about Alex" })
    ).closest('section')!;
    expect(within(theirs).getByText(/keeps their own profile/)).toBeInTheDocument();
    expect(within(theirs).getByText('Pottery')).toBeInTheDocument();
    expect(within(theirs).queryByRole('button')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Edit my profile' })).toBeInTheDocument();
  });

  it('marks notes as private and creates one about the other profile', async () => {
    const api = usApi({
      'POST /api/v1/notes': {
        id: 'n1',
        about_profile_id: 'p-wife',
        kind: 'note',
        text: 'Loves tulips',
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
        archived_at: null,
      },
    });
    renderApp('/us');

    const notes = (await screen.findByText('My notes about Alex')).closest('section')!;
    expect(within(notes).getByText('Only you can see this')).toBeInTheDocument();
    expect(
      within(screen.getByText('Ideas Alex mentioned').closest('section')!).getByText(
        'Only you can see this',
      ),
    ).toBeInTheDocument();

    await userEvent.type(within(notes).getByLabelText('Add a note about Alex'), 'Loves tulips');
    await userEvent.click(within(notes).getByRole('button', { name: 'Add' }));

    await vi.waitFor(() => expect(api.find('POST', '/api/v1/notes')).toHaveLength(1));
    expect(api.body('POST', '/api/v1/notes')).toEqual({
      about_profile_id: 'p-wife',
      kind: 'note',
      text: 'Loves tulips',
    });
  });

  it('adds an important date', async () => {
    const created = {
      id: 'd1',
      name: 'Our anniversary',
      date: '2020-06-01',
      kind: 'anniversary',
      recurring: true,
      profile_id: null,
    };
    let dates: unknown[] = [];
    const api = usApi({
      'GET /api/v1/couple/important-dates': () => dates,
      'POST /api/v1/couple/important-dates': () => {
        dates = [created];
        return created;
      },
    });
    renderApp('/us');

    await userEvent.click(await screen.findByRole('button', { name: 'Add an important date' }));
    await userEvent.type(screen.getByLabelText('What is it?'), 'Our anniversary');
    await userEvent.type(screen.getByLabelText('Date'), '2020-06-01');
    await userEvent.click(screen.getByRole('button', { name: 'Add date' }));

    expect(await screen.findByText('Our anniversary')).toBeInTheDocument();
    expect(api.body('POST', '/api/v1/couple/important-dates')).toEqual({
      name: 'Our anniversary',
      date: '2020-06-01',
      kind: 'anniversary',
      recurring: true,
      profile_id: null,
    });
  });

  it('rejects zero or out-of-range travel limits and sends valid whole numbers', async () => {
    const api = usApi({ 'PUT /api/v1/couple/constraints': constraints });
    renderApp('/us');

    const trip = await screen.findByLabelText('Longest trip (minutes)');
    const radius = screen.getByLabelText('Furthest from home (km)');
    expect(trip).toHaveAttribute('min', '1');
    expect(radius).toHaveAttribute('min', '1');
    const save = screen.getByRole('button', { name: 'Save shared limits' });

    await userEvent.type(trip, '0');
    await userEvent.click(save);
    expect(await screen.findByRole('alert')).toHaveTextContent('from 1 to 1440 minutes');
    expect(api.find('PUT', '/api/v1/couple/constraints')).toHaveLength(0);

    await userEvent.clear(trip);
    await userEvent.type(trip, '30');
    await userEvent.type(radius, '1001');
    await userEvent.click(save);
    expect(await screen.findByRole('alert')).toHaveTextContent('from 1 to 1000 km');
    expect(api.find('PUT', '/api/v1/couple/constraints')).toHaveLength(0);

    await userEvent.clear(radius);
    await userEvent.type(radius, '15');
    await userEvent.click(save);
    await vi.waitFor(() => expect(api.find('PUT', '/api/v1/couple/constraints')).toHaveLength(1));
    expect(api.body('PUT', '/api/v1/couple/constraints')).toMatchObject({
      max_travel_minutes: 30,
      max_radius_km: 15,
    });
  });
});
