import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { couple, husbandProfile, session, wifeProfile } from '../test/fixtures';
import { mockApi } from '../test/mockApi';
import { jsonResponse, renderApp } from '../test/renderApp';

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

/** The session only has a name once PATCH /me has run, like the real API. */
function setupApi(extra: Record<string, unknown> = {}) {
  let named = false;
  return mockApi({
    'GET /api/v1/auth/session': () => session(named ? 'Sam' : null),
    'GET /api/v1/meta': { demo_mode: false, version: 'test', no_login: true },
    'GET /api/v1/profiles': [husbandProfile, wifeProfile],
    'PATCH /api/v1/me': () => {
      named = true;
      return { ...session('Sam').user };
    },
    'PUT /api/v1/profiles/p-husband': husbandProfile,
    'PUT /api/v1/profiles/p-wife': wifeProfile,
    'PUT /api/v1/couple/home-area': { label: 'Austin (demo)', lat: 30.27, lng: -97.74 },
    'PATCH /api/v1/couple': couple,
    ...extra,
  });
}

describe('SetupPage', () => {
  it('walks the three steps and lands on Home', async () => {
    const api = setupApi();
    localStorage.setItem('dateapp.setupSkipped', '1');
    const router = renderApp('/setup');

    const nameField = await screen.findByLabelText('Your first name');
    await userEvent.clear(nameField);
    await userEvent.type(nameField, 'Sam');
    await userEvent.click(screen.getByRole('button', { name: 'Next' }));

    await userEvent.type(await screen.findByLabelText("Your partner's first name"), 'Alex');
    expect(screen.getByText('Step 2 of 3')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Next' }));

    await userEvent.type(await screen.findByLabelText('Home area'), 'austin');
    await userEvent.click(screen.getByRole('button', { name: 'Finish' }));

    await waitFor(() => expect(router.state.location.pathname).toBe('/'));
    expect(api.body('PATCH', '/api/v1/me')).toEqual({ display_name: 'Sam' });
    expect(api.body('PUT', '/api/v1/profiles/p-husband')).toMatchObject({
      display_name: 'Sam',
    });
    expect(api.body('PUT', '/api/v1/profiles/p-wife')).toMatchObject({
      display_name: 'Alex',
    });
    expect(api.body('PUT', '/api/v1/couple/home-area')).toEqual({ query: 'austin' });
    expect(api.body('PATCH', '/api/v1/couple')).toEqual({
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    });
    expect(api.find('PATCH', '/api/v1/me')).toHaveLength(2);
    expect(localStorage.getItem('dateapp.setupSkipped')).toBeNull();
  });

  it('lets you skip the home area when lookup is unavailable', async () => {
    const api = setupApi({
      'PUT /api/v1/couple/home-area': jsonResponse(
        { error: { code: 'provider_unavailable', message: 'Location lookup is off.' } },
        503,
      ),
    });
    const router = renderApp('/setup');

    const nameField = await screen.findByLabelText('Your first name');
    await userEvent.clear(nameField);
    await userEvent.type(nameField, 'Sam');
    await userEvent.click(screen.getByRole('button', { name: 'Next' }));
    await userEvent.type(await screen.findByLabelText("Your partner's first name"), 'Alex');
    await userEvent.click(screen.getByRole('button', { name: 'Next' }));
    await userEvent.type(await screen.findByLabelText('Home area'), 'austin');
    await userEvent.click(screen.getByRole('button', { name: 'Finish' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(
      "Location lookup isn't available right now, you can add it later",
    );
    expect(router.state.location.pathname).toBe('/setup');
    await userEvent.click(screen.getByRole('button', { name: 'Skip for now' }));

    await waitFor(() => expect(router.state.location.pathname).toBe('/'));
    expect(api.find('PATCH', '/api/v1/couple')).toHaveLength(1);
    expect(localStorage.getItem('dateapp.setupSkipped')).toBe('1');
  });

  it("prefills the current user's name", async () => {
    setupApi();
    renderApp('/setup');

    expect(await screen.findByLabelText('Your first name')).toHaveValue('Sam');
  });

  it('disables Back while the partner save is pending, so it cannot jump ahead afterwards', async () => {
    let release!: () => void;
    const held = new Promise<void>((resolve) => (release = resolve));
    const api = setupApi({
      'PUT /api/v1/profiles/p-wife': async () => {
        await held;
        return wifeProfile;
      },
    });
    renderApp('/setup');

    await screen.findByLabelText('Your first name');
    await userEvent.click(screen.getByRole('button', { name: 'Next' }));
    await userEvent.type(await screen.findByLabelText("Your partner's first name"), 'Alex');
    await userEvent.click(screen.getByRole('button', { name: 'Next' }));

    await vi.waitFor(() => expect(api.find('PUT', '/api/v1/profiles/p-wife')).toHaveLength(1));
    expect(screen.getByRole('button', { name: 'Back' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Saving…' })).toBeDisabled();
    expect(screen.getByLabelText("Your partner's first name")).toBeDisabled();
    release();
    expect(await screen.findByLabelText('Home area')).toBeInTheDocument();
  });

  it("prefills the current user's name", async () => {
    setupApi();
    renderApp('/setup');

    expect(await screen.findByLabelText('Your first name')).toHaveValue('Sam');
  });

  it('disables Back while the partner save is pending, so it cannot jump ahead afterwards', async () => {
    let release!: () => void;
    const held = new Promise<void>((resolve) => (release = resolve));
    const api = setupApi({
      'PUT /api/v1/profiles/p-wife': async () => {
        await held;
        return wifeProfile;
      },
    });
    renderApp('/setup');

    await screen.findByLabelText('Your first name');
    await userEvent.click(screen.getByRole('button', { name: 'Next' }));
    await userEvent.type(await screen.findByLabelText("Your partner's first name"), 'Alex');
    await userEvent.click(screen.getByRole('button', { name: 'Next' }));

    await vi.waitFor(() => expect(api.find('PUT', '/api/v1/profiles/p-wife')).toHaveLength(1));
    expect(screen.getByRole('button', { name: 'Back' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Saving…' })).toBeDisabled();
    release();
    expect(await screen.findByLabelText('Home area')).toBeInTheDocument();
  });

  it('offers Skip for now on the home step straight away, without a lookup error', async () => {
    const api = setupApi();
    const router = renderApp('/setup');

    await screen.findByLabelText('Your first name');
    await userEvent.click(screen.getByRole('button', { name: 'Next' }));
    await userEvent.type(await screen.findByLabelText("Your partner's first name"), 'Alex');
    await userEvent.click(screen.getByRole('button', { name: 'Next' }));
    await screen.findByLabelText('Home area');
    expect(screen.getByRole('button', { name: 'Finish' })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Skip for now' }));

    await waitFor(() => expect(router.state.location.pathname).toBe('/'));
    expect(api.find('PUT', '/api/v1/couple/home-area')).toHaveLength(0);
    expect(localStorage.getItem('dateapp.setupSkipped')).toBe('1');
  });

  it('prefills from the session name when the profile has none yet', async () => {
    setupApi({
      'GET /api/v1/auth/session': session('Husband'),
      'GET /api/v1/profiles': [{ ...husbandProfile, display_name: null }, wifeProfile],
    });
    renderApp('/setup');

    expect(await screen.findByLabelText('Your first name')).toHaveValue('Husband');
  });
});
