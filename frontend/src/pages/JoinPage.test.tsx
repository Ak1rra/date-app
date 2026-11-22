import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { getPendingInvite, setPendingInvite } from '../hooks/usePairing';
import { session } from '../test/fixtures';
import { mockApi, type Handler } from '../test/mockApi';
import { jsonResponse, renderApp } from '../test/renderApp';

const meta = (noLogin: boolean) => ({ demo_mode: false, version: 'test', no_login: noLogin });
const me = {
  id: 'u1',
  email: 'me@example.com',
  display_name: 'Sam',
  couple_id: 'c2',
  role: 'partner',
  timezone: 'UTC',
};
const apiError = (code: string, status: number) =>
  jsonResponse({ error: { code, message: `server says ${code}` } }, status);

function setup(routes: Record<string, Handler> = {}) {
  return mockApi({
    'GET /api/v1/auth/session': session('Sam', 'partner'),
    'GET /api/v1/meta': meta(false),
    'GET /api/v1/profiles': [],
    ...routes,
  });
}

beforeEach(() => {
  localStorage.clear();
  window.history.replaceState(null, '', '/');
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('JoinPage', () => {
  it('stores the token from the hash and strips it from the address bar', async () => {
    setup();
    window.history.replaceState(null, '', '/join#token=tok123');
    renderApp('/join#token=tok123');

    expect(await screen.findByRole('heading', { name: 'Join your partner' })).toBeInTheDocument();
    await waitFor(() => expect(window.location.hash).toBe(''));
    expect(window.location.pathname).toBe('/join');
    expect(getPendingInvite()).toEqual({ token: 'tok123' });
    expect(screen.getByText(/Your invite link is ready/)).toBeInTheDocument();
  });

  it('joins with the token, clears the pending invite and goes home', async () => {
    const api = setup({ 'POST /api/v1/couple/join': me });
    const router = renderApp('/join#token=tok123');

    await userEvent.click(await screen.findByRole('button', { name: 'Join' }));

    await waitFor(() => expect(router.state.location.pathname).toBe('/'));
    expect(api.body('POST', '/api/v1/couple/join')).toEqual({ token: 'tok123' });
    expect(getPendingInvite()).toBeNull();
  });

  it('submits a new link token, not an older stored code', async () => {
    const api = setup({ 'POST /api/v1/couple/join': me });
    setPendingInvite({ short_code: 'OLDCODE2' });
    renderApp('/join#token=newtok');

    expect(await screen.findByLabelText('Have a code?')).toHaveValue('');
    await userEvent.click(screen.getByRole('button', { name: 'Join' }));

    await waitFor(() => expect(api.find('POST', '/api/v1/couple/join')).toHaveLength(1));
    expect(api.body('POST', '/api/v1/couple/join')).toEqual({ token: 'newtok' });
  });

  it('submits a code the person typed even when a link token is pending', async () => {
    const api = setup({ 'POST /api/v1/couple/join': me });
    renderApp('/join#token=newtok');

    await userEvent.type(await screen.findByLabelText('Have a code?'), 'wxyz6789');
    await userEvent.click(screen.getByRole('button', { name: 'Join' }));

    await waitFor(() => expect(api.find('POST', '/api/v1/couple/join')).toHaveLength(1));
    expect(api.body('POST', '/api/v1/couple/join')).toEqual({ short_code: 'WXYZ6789' });
  });

  it('joins with a typed code, uppercased', async () => {
    const api = setup({ 'POST /api/v1/couple/join': me });
    renderApp('/join');

    await userEvent.type(await screen.findByLabelText('Have a code?'), 'abcd2345');
    expect(screen.getByLabelText('Have a code?')).toHaveValue('ABCD2345');
    await userEvent.click(screen.getByRole('button', { name: 'Join' }));

    await waitFor(() => expect(api.find('POST', '/api/v1/couple/join')).toHaveLength(1));
    expect(api.body('POST', '/api/v1/couple/join')).toEqual({ short_code: 'ABCD2345' });
  });

  it('explains an expired invite and clears the pending invite', async () => {
    setup({ 'POST /api/v1/couple/join': apiError('invite_invalid', 404) });
    const router = renderApp('/join#token=tok123');

    await userEvent.click(await screen.findByRole('button', { name: 'Join' }));

    expect(
      await screen.findByText(
        'This invite has expired or was already used. Ask your partner for a new one.',
      ),
    ).toBeInTheDocument();
    expect(getPendingInvite()).toBeNull();
    expect(router.state.location.pathname).toBe('/join');
    expect(screen.getByRole('button', { name: 'Join' })).toBeDisabled();
  });

  it('keeps the invite when the person is already paired elsewhere', async () => {
    setup({ 'POST /api/v1/couple/join': apiError('already_paired', 409) });
    setPendingInvite({ token: 'tok123' });
    renderApp('/join');

    await userEvent.click(await screen.findByRole('button', { name: 'Join' }));

    expect(
      await screen.findByText(
        "You're already paired with someone. Leave that space from your Profile first.",
      ),
    ).toBeInTheDocument();
    expect(getPendingInvite()).toEqual({ token: 'tok123' });
  });

  it('shows the server message for other errors', async () => {
    setup({ 'POST /api/v1/couple/join': apiError('rate_limited', 429) });
    setPendingInvite({ token: 'tok123' });
    renderApp('/join');

    await userEvent.click(await screen.findByRole('button', { name: 'Join' }));
    expect(await screen.findByText('server says rate_limited')).toBeInTheDocument();
  });

  it('sends a signed-out visitor to sign in, remembering the invite', async () => {
    setup({ 'GET /api/v1/auth/session': { authenticated: false, user: null } });
    const router = renderApp('/join#token=tok123');

    const link = await screen.findByRole('link', { name: 'Sign in to join your partner' });
    expect(screen.queryByRole('button', { name: 'Join' })).not.toBeInTheDocument();
    expect(getPendingInvite()).toEqual({ token: 'tok123' });
    await userEvent.click(link);

    await waitFor(() => expect(router.state.location.pathname).toBe('/signin'));
    expect(router.state.location.state).toEqual({ from: '/join' });
  });

  it('is not needed in NO_LOGIN mode', async () => {
    const api = setup({ 'GET /api/v1/meta': meta(true) });
    renderApp('/join');

    expect(
      await screen.findByText(
        "Joining isn't needed here. Everyone in the household already shares this space.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Go home' })).toHaveAttribute('href', '/');
    expect(screen.queryByRole('button', { name: 'Join' })).not.toBeInTheDocument();
    expect(api.find('POST', '/api/v1/couple/join')).toHaveLength(0);
  });
});

describe('HomePage pending-invite banner', () => {
  it('links to /join while an invite is pending, in email mode', async () => {
    setup();
    setPendingInvite({ short_code: 'ABCD2345' });
    renderApp('/');

    expect(
      await screen.findByRole('link', { name: 'Finish joining your partner' }),
    ).toHaveAttribute('href', '/join');
  });

  it('is absent without a pending invite', async () => {
    setup();
    renderApp('/');
    await screen.findByRole('link', { name: 'Plan a date' });
    expect(screen.queryByText('Finish joining your partner')).not.toBeInTheDocument();
  });

  it('is absent in NO_LOGIN mode even with a stale pending invite', async () => {
    const api = setup({ 'GET /api/v1/meta': meta(true) });
    setPendingInvite({ short_code: 'ABCD2345' });
    renderApp('/');
    await screen.findByRole('link', { name: 'Plan a date' });
    await waitFor(() => expect(api.find('GET', '/api/v1/meta').length).toBeGreaterThan(0));
    expect(screen.queryByText('Finish joining your partner')).not.toBeInTheDocument();
  });
});
