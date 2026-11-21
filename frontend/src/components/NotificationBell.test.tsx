import { screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { mockApi } from '../test/mockApi';
import { renderApp, signedIn } from '../test/renderApp';

afterEach(() => vi.unstubAllGlobals());

function renderWithCount(count: number) {
  mockApi({
    'GET /api/v1/auth/session': signedIn,
    'GET /api/v1/notifications/unread-count': { count },
    'GET /api/v1/notifications': [],
  });
  renderApp('/notifications');
}

describe('NotificationBell', () => {
  it('hides the badge when there are no unread notifications', async () => {
    renderWithCount(0);
    expect(await screen.findAllByRole('link', { name: 'Notifications, 0 unread' })).toHaveLength(2);
    expect(screen.queryByText('0')).not.toBeInTheDocument();
  });

  it('shows the unread count and includes it in the accessible label', async () => {
    renderWithCount(3);
    expect(await screen.findAllByText('3')).toHaveLength(2);
    expect(screen.getAllByRole('link', { name: 'Notifications, 3 unread' })).toHaveLength(2);
  });

  it('caps large counts at 9+ and links to the notifications page', async () => {
    renderWithCount(12);
    expect(await screen.findAllByText('9+')).toHaveLength(2);
    expect(screen.getAllByRole('link', { name: 'Notifications, 12 unread' })).toHaveLength(2);
    expect(screen.getAllByRole('link', { name: 'Notifications, 12 unread' })[0]).toHaveAttribute(
      'href',
      '/notifications',
    );
  });
});
