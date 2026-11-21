import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mockSession, renderApp, signedIn } from '../test/renderApp';

const IPHONE_UA =
  'Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1';
const BANNER = /Add me to your Home Screen for the full experience/;

function stubEnv({ ua, standalone = false }: { ua: string; standalone?: boolean }) {
  vi.stubGlobal('navigator', { ...navigator, userAgent: ua, standalone, maxTouchPoints: 0 });
  vi.stubGlobal(
    'matchMedia',
    vi.fn((query: string) => ({
      matches: false,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  );
}

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('InstallBanner', () => {
  it('shows on iOS Safari, links to /install, and stays dismissed', async () => {
    stubEnv({ ua: IPHONE_UA });
    mockSession(signedIn);
    renderApp('/');

    const link = await screen.findByRole('link', { name: BANNER });
    expect(link).toHaveAttribute('href', '/install');

    await userEvent.click(screen.getByRole('button', { name: 'Dismiss install banner' }));
    expect(screen.queryByRole('link', { name: BANNER })).not.toBeInTheDocument();
    expect(localStorage.getItem('dateapp-install-banner-dismissed')).toBe('1');
  });

  it('stays hidden after a previous dismissal', async () => {
    stubEnv({ ua: IPHONE_UA });
    localStorage.setItem('dateapp-install-banner-dismissed', '1');
    mockSession(signedIn);
    renderApp('/');

    await screen.findByRole('navigation', { name: 'Main tabs' });
    expect(screen.queryByRole('link', { name: BANNER })).not.toBeInTheDocument();
  });

  it('is hidden on non-iOS browsers', async () => {
    stubEnv({ ua: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130.0 Safari/537.36' });
    mockSession(signedIn);
    renderApp('/');

    await screen.findByRole('navigation', { name: 'Main tabs' });
    expect(screen.queryByRole('link', { name: BANNER })).not.toBeInTheDocument();
  });

  it('is hidden once installed to the Home Screen', async () => {
    stubEnv({ ua: IPHONE_UA, standalone: true });
    mockSession(signedIn);
    renderApp('/');

    await screen.findByRole('navigation', { name: 'Main tabs' });
    expect(screen.queryByRole('link', { name: BANNER })).not.toBeInTheDocument();
  });
});
