import { Outlet } from 'react-router';
import { useSession } from '../hooks/useSession';

/**
 * Runs GET /auth/session once at the root, above public and protected routes alike, so the
 * csrf_token cookie exists before the first sign-in or verify POST. RequireAuth shares the
 * same query key and reuses the cached result.
 */
export function SessionBootstrap() {
  useSession();
  return <Outlet />;
}
