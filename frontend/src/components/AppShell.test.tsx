import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { mockSession, renderApp, signedIn } from '../test/renderApp';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('AppShell navigation', () => {
  it('renders the six tabs with accessible names and marks the active one', async () => {
    mockSession(signedIn);
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
    mockSession(signedIn);
    renderApp('/ideas');
    expect(
      await screen.findByRole('button', { name: 'Invite your partner to swipe together' }),
    ).toBeInTheDocument();
    expect(screen.getByText('No ideas saved yet')).toBeInTheDocument();
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
});
