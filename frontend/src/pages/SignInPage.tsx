import { useQueryClient } from '@tanstack/react-query';
import { useLocation, useNavigate } from 'react-router';
import { ApiError } from '../api/http';
import { Button } from '../components/Button';
import { PROFILES, ProfileError } from '../components/ProfileChoice';
import { useChooseProfile, type Profile } from '../hooks/useChooseProfile';
import { isSetupSkipped } from '../hooks/setupSkipped';
import { loadCouple } from '../hooks/useCouple';
import { useMeta } from '../hooks/useMeta';
import { PlaceholderPage } from './PlaceholderPage';

function SignInPlaceholder() {
  return (
    <PlaceholderPage title="Sign in">
      Sign-in with an emailed magic link is coming soon.
    </PlaceholderPage>
  );
}

export function SignInPage() {
  const meta = useMeta();
  const choose = useChooseProfile();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const location = useLocation();

  if (meta.isPending) {
    return (
      <main className="mx-auto flex min-h-dvh max-w-md flex-col items-center justify-center bg-canvas p-6 text-center text-ink">
        <p role="status" className="text-muted">
          Loading…
        </p>
      </main>
    );
  }

  if (meta.isError) {
    // An ApiError means the server answered; anything else is a network failure.
    const serverFault = meta.error instanceof ApiError;
    return (
      <main className="mx-auto flex min-h-dvh max-w-md flex-col items-center justify-center gap-4 bg-canvas p-6 text-center text-ink">
        <h1 className="text-xl font-semibold">Can&apos;t open Date Planner right now</h1>
        <p role="alert" className="max-w-prose text-muted">
          {serverFault
            ? 'Something went wrong on our side.'
            : "We couldn't reach the server. Check your connection."}
        </p>
        <Button onClick={() => void meta.refetch()} disabled={meta.isFetching}>
          {meta.isFetching ? 'Trying…' : 'Try again'}
        </Button>
      </main>
    );
  }

  if (!meta.data.no_login) return <SignInPlaceholder />;

  const from = (location.state as { from?: unknown } | null)?.from;
  const pick = (profile: Profile) =>
    choose.mutate(profile, {
      onSuccess: async (session) => {
        const destination = typeof from === 'string' ? from : '/';
        // A household with no home area hasn't been set up; the planner can't work without one.
        const userId = session.user?.id;
        const needsSetup = userId
          ? await loadCouple(queryClient, userId).then(
              (couple) => !couple.home_area && !isSetupSkipped(),
              () => false, // can't tell, so behave as before and let the app load
            )
          : false;
        void navigate(needsSetup ? '/setup' : destination, { replace: true });
      },
    });

  return (
    <main className="mx-auto flex min-h-dvh max-w-md flex-col justify-center gap-6 bg-canvas p-6 text-ink">
      <h1 className="text-3xl font-bold">Who&apos;s using Date Planner?</h1>
      <div className="flex flex-col gap-4">
        {PROFILES.map(({ value, label, variant }) => (
          <Button
            key={value}
            variant={variant}
            size="xl"
            disabled={choose.isPending}
            onClick={() => pick(value)}
          >
            {choose.isPending && choose.variables === value ? 'Opening…' : label}
          </Button>
        ))}
      </div>
      <ProfileError error={choose.error} />
    </main>
  );
}
