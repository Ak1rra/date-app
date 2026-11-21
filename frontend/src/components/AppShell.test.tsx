import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { mockApi } from '../test/mockApi';
import { mockSession, renderApp, signedIn } from '../test/renderApp';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('AppShell navigation', () => {
  it('renders the six tabs with accessible names and marks the active one', async () => {
    // Every other route answers with real shapes: mockSession would hand Ideas the session
    // object as its profile list, and the page would crash as soon as that reply landed.
    mockApi({
      'GET /api/v1/auth/session': signedIn,
      'GET /api/v1/profiles': [],
      'GET /api/v1/deck': { cards: [], partner_joined: false },
      'GET /api/v1/ideas/lists': { matches: [], maybes: [], pending_partner: [], passed: [] },
    });
    renderApp('/ideas');

    const tabs = await screen.findByRole('navigation', { name: 'Main tabs' });
    const links = within(tabs).getAllByRole('link');
    expect(links.map((l) => l.textContent)).toEqual([
      'Home',
      'Ideas',
      'Calendar',
      'Chat',
      'Us',
      'Profile',
    ]);

    expect(within(tabs).getByRole('link', { name: 'Ideas' })).toHaveAttribute(
      'aria-current',
      'page',
    );
    expect(within(tabs).getByRole('link', { name: 'Home' })).not.toHaveAttribute('aria-current');

    const top = screen.getByRole('navigation', { name: 'Main menu', hidden: true });
    expect(within(top).getAllByRole('link', { hidden: true })).toHaveLength(6);
  });

  it('shows a teaching empty state on Ideas with the invite CTA', async () => {
    mockApi({
      'GET /api/v1/auth/session': signedIn,
      'GET /api/v1/profiles': [],
      'GET /api/v1/deck': { cards: [], partner_joined: false },
      'GET /api/v1/ideas/lists': { matches: [], maybes: [], pending_partner: [], passed: [] },
    });
    renderApp('/ideas');
    expect(
      await screen.findByRole('button', { name: 'Invite your partner to swipe together' }),
    ).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'My ideas' })).toBeInTheDocument();
  });

  it('skip link moves keyboard focus to the main landmark', async () => {
    mockSession(signedIn);
    renderApp('/');
    const skip = await screen.findByRole('link', { name: 'Skip to content' });
    const main = screen.getByRole('main');
    expect(main).toHaveAttribute('tabindex', '-1');
    await userEvent.click(skip);
    expect(main).toHaveFocus();
  });

  it('renders notification bells in both desktop and mobile headers', async () => {
    mockApi({
      'GET /api/v1/auth/session': signedIn,
      'GET /api/v1/notifications/unread-count': { count: 2 },
      'GET /api/v1/notifications': [],
    });
    renderApp('/');

    expect(await screen.findAllByRole('link', { name: 'Notifications, 2 unread' })).toHaveLength(2);
  });
});
