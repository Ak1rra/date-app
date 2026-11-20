import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { jsonResponse, mockSession, renderApp, signedIn } from '../test/renderApp';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('RequireAuth', () => {
  it('redirects unauthenticated users to /signin', async () => {
    mockSession({ authenticated: false, user: null });
    const router = renderApp('/ideas');
    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe('/signin');
  });

  it('redirects a 401 from the API to /signin', async () => {
    mockSession(
      jsonResponse({ error: { code: 'unauthenticated', message: 'Please sign in.' } }, 401),
    );
    const router = renderApp('/');
    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe('/signin');
  });

  it('sends signed-in users without a display name to /setup', async () => {
    mockSession({ ...signedIn, user: { ...signedIn.user!, display_name: null } });
    const router = renderApp('/');
    expect(await screen.findByRole('heading', { name: "Let's get you set up" })).toBeVisible();
    expect(router.state.location.pathname).toBe('/setup');
  });

  it('shows a friendly retry state when the server is unreachable', async () => {
    const fetchMock = mockSession(new TypeError('Failed to fetch'));
    renderApp('/');
    expect(
      await screen.findByRole('heading', { name: "Can't reach the server" }),
    ).toBeInTheDocument();

    fetchMock.mockImplementation(async () => jsonResponse(signedIn));
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(await screen.findByRole('link', { name: 'Plan a date' })).toBeInTheDocument();
  });

  it('distinguishes a server error from an unreachable server', async () => {
    mockSession(jsonResponse({ error: { code: 'internal', message: 'Boom' } }, 503));
    renderApp('/');
    expect(
      await screen.findByRole('heading', { name: 'Something went wrong on our side' }),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
  });
});
