import { screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { mockSession, renderApp, signedIn } from '../test/renderApp';

function stubStandalone(standalone: boolean) {
  vi.stubGlobal(
    'matchMedia',
    vi.fn((query: string) => ({
      matches: standalone && query === '(display-mode: standalone)',
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('InstallPage', () => {
  it('shows the four steps in an ordered list when not installed', async () => {
    stubStandalone(false);
    mockSession(signedIn);
    renderApp('/install');

    expect(
      await screen.findByRole('heading', { level: 1, name: 'Get Date Planner on your iPhone' }),
    ).toBeInTheDocument();
    const list = screen.getByRole('list', { name: 'Install steps' });
    expect(list.tagName).toBe('OL');
    expect(
      screen.getByRole('heading', { name: /Step 1: Open Date Planner in Safari/ }),
    ).toBeVisible();
    expect(screen.getByRole('heading', { name: /Step 2: Trust the certificate/ })).toBeVisible();
    expect(
      screen.getByRole('heading', { name: /Step 3: Add it to your Home Screen/ }),
    ).toBeVisible();
    expect(
      screen.getByRole('heading', { name: /Step 4: Open it from the Home Screen/ }),
    ).toBeVisible();
    expect(screen.getByText(/root\.crt/, { selector: 'strong' })).toBeInTheDocument();
    expect(screen.queryByText(/You're all set/)).not.toBeInTheDocument();
  });

  it('says you are all set when already running from the Home Screen', async () => {
    stubStandalone(true);
    mockSession(signedIn);
    renderApp('/install');

    expect(await screen.findByRole('heading', { name: "You're all set 💘" })).toBeInTheDocument();
  });
});
