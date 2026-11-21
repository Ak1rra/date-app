import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { mockApi } from '../test/mockApi';
import { renderApp } from '../test/renderApp';

const session = {
  authenticated: true,
  user: {
    id: 'u1',
    email: 'me@example.com',
    display_name: 'Sam',
    timezone: 'UTC',
    couple_id: 'c1',
    role: 'owner',
  },
};

const notifications = [
  {
    id: 'n1',
    title: 'Your date is ready',
    body: 'The plan is waiting for you.',
    kind: 'itinerary_ready',
    data: { url: '/itineraries/i1' },
    created_at: new Date(Date.now() - 5 * 60_000).toISOString(),
    deliver_after: new Date(Date.now() - 5 * 60_000).toISOString(),
    pushed_at: null,
    read_at: null,
  },
  {
    id: 'n2',
    title: 'Old update',
    body: 'This one was already read.',
    kind: 'other',
    data: { url: 'https://example.com/nope' },
    created_at: new Date(Date.now() - 60 * 60_000).toISOString(),
    deliver_after: new Date(Date.now() - 60 * 60_000).toISOString(),
    pushed_at: null,
    read_at: new Date().toISOString(),
  },
];

afterEach(() => vi.restoreAllMocks());

describe('NotificationsPage', () => {
  it('lists notifications, marks an unread item read, and keeps navigation in-app', async () => {
    const api = mockApi({
      'GET /api/v1/auth/session': session,
      'GET /api/v1/notifications': notifications,
      'GET /api/v1/notifications/unread-count': { count: 1 },
      'POST /api/v1/notifications/n1/read': notifications[0],
    });
    const router = renderApp('/notifications');

    expect(await screen.findByRole('button', { name: /Your date is ready/ })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /Your date is ready/ }));

    await waitFor(() => expect(api.find('POST', '/api/v1/notifications/n1/read')).toHaveLength(1));
    expect(router.state.location.pathname).toBe('/itineraries/i1');
  });

  it('marks every notification read', async () => {
    const api = mockApi({
      'GET /api/v1/auth/session': session,
      'GET /api/v1/notifications': notifications,
      'GET /api/v1/notifications/unread-count': { count: 1 },
      'POST /api/v1/notifications/read-all': { updated: 2 },
    });
    renderApp('/notifications');
    await userEvent.click(await screen.findByRole('button', { name: 'Mark all read' }));
    await waitFor(() => expect(api.find('POST', '/api/v1/notifications/read-all')).toHaveLength(1));
  });

  it('marks an external notification read without leaving the app', async () => {
    const externalUnread = { ...notifications[1], read_at: null };
    const api = mockApi({
      'GET /api/v1/auth/session': session,
      'GET /api/v1/notifications': [externalUnread],
      'GET /api/v1/notifications/unread-count': { count: 1 },
      'POST /api/v1/notifications/n2/read': externalUnread,
    });
    const router = renderApp('/notifications');

    await userEvent.click(await screen.findByRole('button', { name: /Old update/ }));
    await waitFor(() => expect(api.find('POST', '/api/v1/notifications/n2/read')).toHaveLength(1));
    expect(router.state.location.pathname).toBe('/notifications');
  });

  it('shows the empty state when there are no notifications', async () => {
    mockApi({
      'GET /api/v1/auth/session': session,
      'GET /api/v1/notifications': [],
      'GET /api/v1/notifications/unread-count': { count: 0 },
    });
    renderApp('/notifications');
    expect(await screen.findByText("You're all caught up.")).toBeInTheDocument();
  });

  it('shows mark all read when the unread count is positive even if the loaded list is read', async () => {
    const api = mockApi({
      'GET /api/v1/auth/session': session,
      'GET /api/v1/notifications': [notifications[1]],
      'GET /api/v1/notifications/unread-count': { count: 2 },
      'POST /api/v1/notifications/read-all': { updated: 2 },
    });
    renderApp('/notifications');
    await userEvent.click(await screen.findByRole('button', { name: 'Mark all read' }));
    await waitFor(() => expect(api.find('POST', '/api/v1/notifications/read-all')).toHaveLength(1));
  });
});
