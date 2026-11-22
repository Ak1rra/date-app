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

const settings = { kinds: { itinerary_ready: true }, quiet_start: '22:00', quiet_end: '08:00' };

function setupPush({
  permission = 'default',
  requestPermission = vi.fn(async () => 'granted' as NotificationPermission),
  subscription = null,
}: {
  permission?: NotificationPermission;
  requestPermission?: ReturnType<typeof vi.fn>;
  subscription?: PushSubscription | null;
} = {}) {
  const subscribe = vi.fn(async (options: PushSubscriptionOptions) => {
    void options;
    return subscriptionForTest;
  });
  const getSubscription = vi.fn(async () => subscription);
  const pushManager = { getSubscription, subscribe };
  vi.stubGlobal('PushManager', class PushManager {});
  Object.defineProperty(window, 'PushManager', { configurable: true, value: PushManager });
  Object.defineProperty(navigator, 'serviceWorker', {
    configurable: true,
    value: { ready: Promise.resolve({ pushManager }) },
  });
  vi.stubGlobal('Notification', { permission, requestPermission });
  return { getSubscription, subscribe, pushManager };
}

const subscriptionForTest = {
  endpoint: 'https://push.example/subscription-1',
  unsubscribe: vi.fn(async () => true),
  toJSON: () => ({
    endpoint: 'https://push.example/subscription-1',
    keys: { p256dh: 'p256dh-key', auth: 'auth-key' },
  }),
} as unknown as PushSubscription;

function pushApi(overrides: Record<string, unknown> = {}) {
  return mockApi({
    'GET /api/v1/auth/session': session,
    'GET /api/v1/notifications/push-config': { public_key: 'AQID-_8' },
    'GET /api/v1/me/notification-settings': settings,
    'PUT /api/v1/me/notification-settings': settings,
    ...overrides,
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('PushSettings', () => {
  it('saves notification settings and explains denied push permission', async () => {
    const api = pushApi();
    const { subscribe } = setupPush({
      requestPermission: vi.fn(async () => 'denied' as NotificationPermission),
    });

    renderApp('/profile');
    const start = await screen.findByLabelText('Quiet hours start');
    await userEvent.clear(start);
    await userEvent.type(start, '21:30');
    await userEvent.click(screen.getByRole('button', { name: 'Save settings' }));
    await waitFor(() =>
      expect(api.body('PUT', '/api/v1/me/notification-settings')).toMatchObject({
        quiet_start: '21:30',
      }),
    );
    await userEvent.click(screen.getByRole('button', { name: 'Enable notifications' }));
    expect(await screen.findByText(/Notifications are blocked/)).toBeInTheDocument();
    expect(subscribe).not.toHaveBeenCalled();
    expect(api.find('POST', '/api/v1/me/push-subscriptions')).toHaveLength(0);
  });

  it('lists every notification kind with a readable label', async () => {
    const labels = {
      reminder_day_before: 'Day-before reminder',
      reminder_hour_before: 'Hour-before reminder',
      checklist_due: 'Checklist nudges',
      match: "It's a match",
      ideas_added: 'New shared ideas',
      date_announced: 'New dates',
      date_rescheduled: 'Date moved',
      date_cancelled: 'Date cancelled',
      hint_released: 'Surprise hints',
      surprise_revealed: 'Surprise revealed',
      partner_reaction: 'Partner reactions',
      rate_prompt: 'Rate your date',
    };
    const kinds = Object.keys(labels);
    pushApi({
      'GET /api/v1/me/notification-settings': {
        ...settings,
        kinds: Object.fromEntries(kinds.map((kind) => [kind, true])),
      },
    });
    setupPush();
    renderApp('/profile');
    for (const label of Object.values(labels)) {
      expect(await screen.findByLabelText(label)).toBeInTheDocument();
    }
  });

  it('falls back to the spaced-out kind for one it has no label for', async () => {
    pushApi({
      'GET /api/v1/me/notification-settings': {
        ...settings,
        kinds: { brand_new_kind: true },
      },
    });
    setupPush();
    renderApp('/profile');
    expect(await screen.findByLabelText('brand new kind')).toBeInTheDocument();
  });

  it('subscribes with the decoded VAPID key and posts the subscription', async () => {
    const api = pushApi();
    const { subscribe } = setupPush();
    renderApp('/profile');

    await userEvent.click(await screen.findByRole('button', { name: 'Enable notifications' }));
    await waitFor(() => expect(api.find('POST', '/api/v1/me/push-subscriptions')).toHaveLength(1));
    expect(subscribe).toHaveBeenCalledWith({
      userVisibleOnly: true,
      applicationServerKey: expect.any(Uint8Array),
    });
    const key = subscribe.mock.calls[0]![0]!.applicationServerKey as unknown as Uint8Array;
    expect([...key]).toEqual([1, 2, 3, 251, 255]);
    expect(api.body('POST', '/api/v1/me/push-subscriptions')).toEqual({
      endpoint: subscriptionForTest.endpoint,
      keys: { p256dh: 'p256dh-key', auth: 'auth-key' },
    });
  });

  it('turns off an existing subscription and deletes it', async () => {
    const api = pushApi();
    const current = {
      ...subscriptionForTest,
      unsubscribe: vi.fn(async () => true),
    } as PushSubscription;
    setupPush({ permission: 'granted', subscription: current });
    renderApp('/profile');

    await userEvent.click(await screen.findByRole('button', { name: 'Turn off' }));
    await waitFor(() => expect(current.unsubscribe).toHaveBeenCalled());
    expect(api.find('DELETE', '/api/v1/me/push-subscriptions')).toHaveLength(1);
  });

  it('shows an error when requesting permission rejects', async () => {
    pushApi();
    setupPush({
      requestPermission: vi.fn(async () => {
        throw new Error('permission request failed');
      }),
    });
    renderApp('/profile');

    await userEvent.click(await screen.findByRole('button', { name: 'Enable notifications' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(
      "We couldn't ask for notification permission. Try again.",
    );
  });

  it('keeps notification settings available when push is unsupported', async () => {
    const api = pushApi();
    vi.stubGlobal('Notification', undefined);
    renderApp('/profile');

    expect(await screen.findByText(/Push notifications aren.t available/)).toBeInTheDocument();
    const start = await screen.findByLabelText('Quiet hours start');
    await userEvent.clear(start);
    await userEvent.type(start, '20:00');
    await userEvent.click(screen.getByRole('button', { name: 'Save settings' }));
    await waitFor(() =>
      expect(api.body('PUT', '/api/v1/me/notification-settings')).toMatchObject({
        quiet_start: '20:00',
      }),
    );
  });

  it('shows the iOS install message and link before standalone mode', async () => {
    pushApi();
    setupPush({ permission: 'granted' });
    Object.defineProperty(navigator, 'userAgent', { configurable: true, value: 'iPhone' });
    renderApp('/profile');

    expect(
      await screen.findByText('Install the app to your Home Screen first.'),
    ).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Learn how to install it' })).toHaveAttribute(
      'href',
      '/install',
    );
  });
});
