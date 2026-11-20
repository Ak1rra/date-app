import { screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { mockSession, renderApp, signedIn } from '../test/renderApp';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('SessionBootstrap', () => {
  it('requests the session on public routes so the CSRF cookie is set', async () => {
    const fetchMock = mockSession({ authenticated: false, user: null });
    renderApp('/signin');
    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument();
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/v1/auth/session');
  });

  it('shares one session request with the route guard', async () => {
    const fetchMock = mockSession(signedIn);
    renderApp('/');
    await screen.findByRole('link', { name: 'Plan a date' });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
