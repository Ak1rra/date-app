import { Outlet } from 'react-router';
import { useProfileSwitchSync } from '../hooks/profileSwitch';
import { usePersonTheme } from '../hooks/usePersonTheme';
import { useSession } from '../hooks/useSession';

/**
 * Runs GET /auth/session once at the root, above public and protected routes alike, so the
 * csrf_token cookie exists before the first sign-in or verify POST. RequireAuth shares the
 * same query key and reuses the cached result. Also applies the per-person palette app-wide.
 */
export function SessionBootstrap() {
  const user = useSession().data?.user;
  usePersonTheme(user?.role);
  useProfileSwitchSync(user?.id);
  return <Outlet />;
}
