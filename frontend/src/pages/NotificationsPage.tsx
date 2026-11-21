import { useMemo } from 'react';
import { useNavigate } from 'react-router';
import { Button } from '../components/Button';
import { Card } from '../components/EmptyState';
import {
  useMarkAllNotificationsRead,
  useMarkNotificationRead,
  useNotifications,
  useUnreadNotificationCount,
} from '../hooks/useNotifications';
import { safeUrl } from '../lib/pushUrl';

function relativeTime(value: string): string {
  const seconds = (Date.now() - new Date(value).getTime()) / 1000;
  const units: [Intl.RelativeTimeFormatUnit, number][] = [
    ['year', 31_536_000],
    ['month', 2_592_000],
    ['week', 604_800],
    ['day', 86_400],
    ['hour', 3_600],
    ['minute', 60],
  ];
  const formatter = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' });
  for (const [unit, size] of units) {
    if (Math.abs(seconds) >= size) return formatter.format(-Math.round(seconds / size), unit);
  }
  return 'Just now';
}

export function NotificationsPage() {
  const navigate = useNavigate();
  const notifications = useNotifications();
  const unreadCount = useUnreadNotificationCount();
  const markRead = useMarkNotificationRead();
  const markAll = useMarkAllNotificationsRead();
  const hasUnread = useMemo(
    () =>
      (unreadCount.data?.count ?? 0) > 0 ||
      (notifications.data?.some((notification) => notification.read_at === null) ?? false),
    [notifications.data, unreadCount.data?.count],
  );

  const openNotification = async (id: string, url: unknown, unread: boolean) => {
    if (unread) await markRead.mutateAsync(id);
    const destination = safeUrl(url);
    // Invalid and external URLs are still marked read, but never take the user out of the app.
    if (destination !== '/' || url === '/') void navigate(destination);
  };

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between gap-4">
        <h1 className="text-2xl font-bold">Notifications</h1>
        {hasUnread ? (
          <Button variant="secondary" onClick={() => markAll.mutate()} disabled={markAll.isPending}>
            Mark all read
          </Button>
        ) : null}
      </div>
      {notifications.isPending ? <p className="text-muted">Loading notifications…</p> : null}
      {notifications.isError ? (
        <p role="alert" className="text-danger">
          We couldn&apos;t load your notifications. Please try again.
        </p>
      ) : null}
      {notifications.isSuccess && notifications.data.length === 0 ? (
        <Card>
          <p className="p-4 text-center text-muted">You&apos;re all caught up.</p>
        </Card>
      ) : null}
      <div className="flex flex-col gap-3" aria-live="polite">
        {notifications.data?.map((notification) => {
          const unread = notification.read_at === null;
          return (
            <button
              key={notification.id}
              type="button"
              onClick={() => void openNotification(notification.id, notification.data.url, unread)}
              className={
                'rounded-3xl border border-line p-4 text-left shadow-sm transition hover:border-accent ' +
                (unread ? 'bg-accent-soft' : 'bg-surface')
              }
            >
              <span className="flex items-start justify-between gap-4">
                <span className={'text-base ' + (unread ? 'font-bold' : 'font-semibold')}>
                  {notification.title}
                </span>
                <time dateTime={notification.created_at} className="shrink-0 text-sm text-muted">
                  {relativeTime(notification.created_at)}
                </time>
              </span>
              <span className="mt-1 block text-muted">{notification.body}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
