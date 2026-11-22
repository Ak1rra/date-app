import { screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { jsonResponse, mockSession, renderApp } from '../test/renderApp';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('PrivacyPage', () => {
  it('renders without a session', async () => {
    mockSession({ authenticated: false, user: null });
    const router = renderApp('/privacy');

    expect(await screen.findByRole('heading', { level: 1, name: 'Privacy' })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe('/privacy');
    expect(screen.getByRole('link', { name: '← Back' })).toHaveAttribute('href', '/profile');
  });

  it('has a section for every recipient', async () => {
    mockSession({ authenticated: false, user: null });
    renderApp('/privacy');
    await screen.findByRole('heading', { level: 1, name: 'Privacy' });

    for (const name of [
      'Anthropic (Claude)',
      'Google Maps Platform',
      'Ticketmaster',
      'Open-Meteo',
      'Resend',
      'Web Push services',
      'Everything else stays on your own server',
    ]) {
      expect(screen.getByRole('heading', { level: 2, name })).toBeInTheDocument();
    }
    expect(screen.getByRole('link', { name: /Anthropic's privacy policy/ })).toHaveAttribute(
      'href',
      'https://www.anthropic.com/legal/privacy',
    );
  });

  it('explains the no-login trust note', async () => {
    mockSession({ authenticated: false, user: null });
    renderApp('/privacy');

    expect(
      await screen.findByRole('heading', { name: 'No login means trust' }),
    ).toBeInTheDocument();
    expect(screen.getByText(/relies on trust, not on locks/)).toBeInTheDocument();
  });

  it('is linked from the Profile page', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn<typeof fetch>(async (input) =>
        String(input).endsWith('/api/v1/meta')
          ? jsonResponse({ demo_mode: false, version: 'test', no_login: true })
          : jsonResponse({
              authenticated: true,
              user: { id: 'u1', role: 'owner', display_name: 'Sam', timezone: 'UTC' },
            }),
      ),
    );
    renderApp('/profile');

    expect(await screen.findByRole('link', { name: 'Privacy' })).toHaveAttribute(
      'href',
      '/privacy',
    );
  });
});
