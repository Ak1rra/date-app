import type { ReactNode } from 'react';
import { Navigate, useLocation } from 'react-router';
import { useSession } from '../hooks/useSession';
import { ApiError } from '../api/http';
import { Button } from './Button';

function Centered({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-dvh flex-col items-center justify-center gap-4 bg-canvas p-6 text-center text-ink">
      {children}
    </div>
  );
}

interface RequireAuthProps {
  children: ReactNode;
  /** The setup page itself must be reachable before a display name exists. */
  allowIncompleteProfile?: boolean;
}

/** Signed-out -> /signin; signed in without a display name -> /setup. */
export function RequireAuth({ children, allowIncompleteProfile = false }: RequireAuthProps) {
  const session = useSession();
  const location = useLocation();

  if (session.isPending) {
    return (
      <Centered>
        <p role="status" className="text-muted">
          Loading…
        </p>
      </Centered>
    );
  }

  if (session.isError) {
    // A 401 from the API is an answer, not an outage.
    if (session.error instanceof ApiError && session.error.status === 401) {
      return <Navigate to="/signin" replace state={{ from: location.pathname }} />;
    }
    // Any other API answer means we reached the server; only a network failure is "can't reach".
    const serverFault = session.error instanceof ApiError;
    return (
      <Centered>
        <h1 className="text-xl font-semibold">
          {serverFault ? 'Something went wrong on our side' : "Can't reach the server"}
        </h1>
        <p className="max-w-prose text-muted">
          {serverFault
            ? "That wasn't your fault. Give it a moment and try again."
            : "We couldn't connect just now. Check your connection and try again."}
        </p>
        <Button onClick={() => void session.refetch()} disabled={session.isFetching}>
          {session.isFetching ? 'Trying…' : 'Try again'}
        </Button>
      </Centered>
    );
  }

  const { authenticated, user } = session.data;
  if (!authenticated || !user) {
    return <Navigate to="/signin" replace state={{ from: location.pathname }} />;
  }
  if (!allowIncompleteProfile && !user.display_name) {
    return <Navigate to="/setup" replace />;
  }
  return <>{children}</>;
}
