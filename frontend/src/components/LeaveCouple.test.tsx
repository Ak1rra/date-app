import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { couple, session } from '../test/fixtures';
import { mockApi, type Handler } from '../test/mockApi';
import { jsonResponse, renderApp, renderAppWithClient } from '../test/renderApp';

const meta = (noLogin: boolean) => ({ demo_mode: false, version: 'test', no_login: noLogin });
const paired = { ...couple, partner_joined: true };

function setup(routes: Record<string, Handler> = {}) {
  return mockApi({
    'GET /api/v1/auth/session': session('Sam'),
    'GET /api/v1/meta': meta(false),
    'GET /api/v1/couple': paired,
    'GET /api/v1/notifications/push-config': { public_key: 'AQID-_8' },
    ...routes,
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('LeaveCouple', () => {
  it('only sends the request after a second click, and returns focus on cancel', async () => {
    const api = setup({ 'POST /api/v1/couple/leave': new Response(null, { status: 204 }) });
    renderApp('/profile');

    await userEvent.click(await screen.findByRole('button', { name: 'Leave our shared space' }));
    const dialog = await screen.findByRole('alertdialog');
    expect(dialog).toHaveTextContent(/own space back/);
    expect(dialog).toHaveTextContent(/deleted after 30 days/);
    expect(screen.getByRole('button', { name: 'Yes, leave' })).toHaveFocus();

    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Leave our shared space' })).toHaveFocus();
    expect(api.find('POST', '/api/v1/couple/leave')).toHaveLength(0);
  });

  it('leaves, refreshes everything and goes home', async () => {
    let left = false;
    const api = setup({
      'GET /api/v1/couple': () => (left ? couple : paired),
      'POST /api/v1/couple/leave': () => {
        left = true;
        return new Response(null, { status: 204 });
      },
    });
    const router = renderApp('/profile');

    await userEvent.click(await screen.findByRole('button', { name: 'Leave our shared space' }));
    await userEvent.click(await screen.findByRole('button', { name: 'Yes, leave' }));

    await waitFor(() => expect(router.state.location.pathname).toBe('/'));
    expect(api.find('POST', '/api/v1/couple/leave')).toHaveLength(1);
    // Invalidating everything re-fetches the session too.
    expect(api.find('GET', '/api/v1/auth/session').length).toBeGreaterThan(1);
  });

  it('resets every cached query and the offline cache after leaving', async () => {
    const deleteCache = vi.fn(async () => true);
    vi.stubGlobal('caches', { delete: deleteCache });
    let left = false;
    setup({
      'GET /api/v1/couple': () => (left ? couple : paired),
      'POST /api/v1/couple/leave': () => {
        left = true;
        return new Response(null, { status: 204 });
      },
    });
    const { router, queryClient } = renderAppWithClient('/profile', (client) =>
      // An inactive entry, as if from a page visited earlier: invalidation would leave it.
      client.setQueryData(['ideas', 'u1'], [{ id: 'old' }]),
    );

    await userEvent.click(await screen.findByRole('button', { name: 'Leave our shared space' }));
    await userEvent.click(await screen.findByRole('button', { name: 'Yes, leave' }));

    await waitFor(() => expect(router.state.location.pathname).toBe('/'));
    expect(queryClient.getQueryData(['ideas', 'u1'])).toBeUndefined();
    expect(deleteCache).toHaveBeenCalledWith('api-offline');
  });

  it('shows the error and stays in the confirmation when leaving fails', async () => {
    setup({
      'POST /api/v1/couple/leave': jsonResponse(
        { error: { code: 'couple_changed', message: 'Your space just changed.' } },
        409,
      ),
    });
    const router = renderApp('/profile');

    await userEvent.click(await screen.findByRole('button', { name: 'Leave our shared space' }));
    await userEvent.click(await screen.findByRole('button', { name: 'Yes, leave' }));

    expect(await screen.findByText('Your space just changed.')).toBeInTheDocument();
    expect(screen.getByRole('alertdialog')).toBeInTheDocument();
    expect(router.state.location.pathname).toBe('/profile');
  });

  it('is hidden in NO_LOGIN mode', async () => {
    setup({ 'GET /api/v1/meta': meta(true) });
    renderApp('/profile');
    await screen.findByRole('button', { name: "Reset this profile's data" });
    expect(
      screen.queryByRole('button', { name: 'Leave our shared space' }),
    ).not.toBeInTheDocument();
  });

  it('is hidden while the couple has no partner', async () => {
    setup({ 'GET /api/v1/couple': couple });
    renderApp('/profile');
    await screen.findByRole('button', { name: 'Delete my account' });
    expect(
      screen.queryByRole('button', { name: 'Leave our shared space' }),
    ).not.toBeInTheDocument();
  });
});
