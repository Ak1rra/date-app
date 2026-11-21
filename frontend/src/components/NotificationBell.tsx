import { Link } from 'react-router';
import { useUnreadNotificationCount } from '../hooks/useNotifications';

function BellIcon() {
  return (
    <svg
      aria-hidden="true"
      focusable="false"
      width="22"
      height="22"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M18 9a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9" />
      <path d="M10 21h4" />
    </svg>
  );
}

export function NotificationBell() {
  const unread = useUnreadNotificationCount();
  const count = unread.data?.count ?? 0;
  const label = `Notifications, ${count} unread`;

  return (
    <Link
      to="/notifications"
      aria-label={label}
      className="relative flex size-11 items-center justify-center rounded-full text-muted hover:bg-surface-2"
    >
      <BellIcon />
      {count > 0 ? (
        <span className="absolute top-0 right-0 min-w-5 rounded-full bg-accent px-1 text-center text-xs leading-5 font-bold text-on-accent">
          {count > 9 ? '9+' : count}
        </span>
      ) : null}
    </Link>
  );
}
