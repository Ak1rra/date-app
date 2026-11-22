import { useEffect, useState, type ReactNode } from 'react';
import { useLocation, useNavigate } from 'react-router';
import { ApiError } from '../api/http';
import { Button, ButtonLink } from '../components/Button';
import { ProfileError } from '../components/ProfileChoice';
import { LoadError, TextField } from '../components/forms';
import { useMeta } from '../hooks/useMeta';
import {
  clearPendingInvite,
  getPendingInvite,
  setPendingInvite,
  useJoin,
  type PendingInvite,
} from '../hooks/usePairing';
import { useSession } from '../hooks/useSession';

const CODE_LENGTH = 8;

/** Words for the join errors people can fix; anything else shows the server's own message. */
const JOIN_MESSAGES: Record<string, string> = {
  invite_invalid: 'This invite has expired or was already used. Ask your partner for a new one.',
  couple_full: 'That couple already has two people.',
  already_paired: "You're already paired with someone. Leave that space from your Profile first.",
  already_member: "You're already in this couple.",
};

function friendlyJoinError(error: ApiError): ApiError {
  const message = JOIN_MESSAGES[error.code];
  return message ? new ApiError(error.status, error.code, message) : error;
}

function Frame({ children }: { children: ReactNode }) {
  return (
    <main className="mx-auto flex min-h-dvh max-w-md flex-col justify-center gap-6 bg-canvas p-6 text-ink">
      {children}
    </main>
  );
}

/** Where an invite link lands: /join#token=... (the token stays out of the query and the server logs). */
export function JoinPage() {
  const meta = useMeta();
  const session = useSession();
  const join = useJoin();
  const navigate = useNavigate();
  const { hash } = useLocation();
  const [stored] = useState(getPendingInvite);
  const [used, setUsed] = useState(false);
  const [code, setCode] = useState(stored?.short_code ?? '');
  const [error, setError] = useState<unknown>(null);

  // A link's token wins over an older stored invite. `used` drops it once the server refuses it.
  const hashToken = new URLSearchParams(hash.replace(/^#/, '')).get('token');
  const pending: PendingInvite | null = used ? null : hashToken ? { token: hashToken } : stored;

  useEffect(() => {
    if (!hashToken) return;
    setPendingInvite({ token: hashToken });
    // The token must not linger in the address bar, history entry or a copied URL.
    window.history.replaceState(
      window.history.state,
      '',
      window.location.pathname + window.location.search,
    );
  }, [hashToken]);

  if (meta.isPending || session.isPending) {
    return (
      <Frame>
        <p role="status" className="text-center text-muted">
          Loading…
        </p>
      </Frame>
    );
  }

  if (meta.isError) {
    return (
      <Frame>
        <LoadError error={meta.error} onRetry={() => void meta.refetch()} />
      </Frame>
    );
  }

  if (meta.data.no_login) {
    return (
      <Frame>
        <h1 className="text-3xl font-bold">Join your partner</h1>
        <p className="text-muted">
          Joining isn&apos;t needed here. Everyone in the household already shares this space.
        </p>
        <div>
          <ButtonLink to="/">Go home</ButtonLink>
        </div>
      </Frame>
    );
  }

  const signedOut =
    session.isError && session.error instanceof ApiError && session.error.status === 401;
  if (session.isError && !signedOut) {
    return (
      <Frame>
        <LoadError error={session.error} onRetry={() => void session.refetch()} />
      </Frame>
    );
  }
  const authenticated = !signedOut && session.data?.authenticated && !!session.data.user;

  const codeReady = code.length === CODE_LENGTH;
  const body = codeReady ? { short_code: code } : pending?.token ? { token: pending.token } : null;

  const codeField = (
    <TextField
      label="Have a code?"
      hint="The 8 letters and numbers your partner sees."
      value={code}
      maxLength={CODE_LENGTH + 4}
      autoCapitalize="characters"
      autoComplete="off"
      spellCheck={false}
      onChange={(e) => {
        join.reset();
        setError(null);
        setCode(
          e.target.value
            .toUpperCase()
            .replace(/[^A-Z0-9]/g, '')
            .slice(0, CODE_LENGTH),
        );
      }}
    />
  );

  const submit = async () => {
    if (!body) return;
    setError(null);
    try {
      await join.mutateAsync(body);
    } catch (caught) {
      // These two mean the invite can't be used again, so don't keep nagging about it.
      if (
        caught instanceof ApiError &&
        (caught.code === 'invite_invalid' || caught.code === 'already_member')
      ) {
        clearPendingInvite();
        setUsed(true);
      }
      setError(caught instanceof ApiError ? friendlyJoinError(caught) : caught);
      return;
    }
    await navigate('/', { replace: true });
  };

  if (!authenticated) {
    return (
      <Frame>
        <h1 className="text-3xl font-bold">Join your partner</h1>
        <p className="text-muted">
          {pending?.token
            ? 'Your invite is ready. Sign in or create your account to use it.'
            : 'Sign in, then use your partner’s link or code to share one space.'}
        </p>
        {codeField}
        <div>
          <ButtonLink
            to="/signin"
            state={{ from: '/join' }}
            size="xl"
            onClick={() => {
              // Keep a typed code across the sign-in round trip (the link's token is saved already).
              if (codeReady) setPendingInvite({ ...pending, short_code: code });
            }}
          >
            Sign in to join your partner
          </ButtonLink>
        </div>
      </Frame>
    );
  }

  return (
    <Frame>
      <h1 className="text-3xl font-bold">Join your partner</h1>
      <p className="text-muted">
        {pending?.token && !codeReady
          ? 'Your invite link is ready. Join to share ideas, dates and chats.'
          : 'Enter the code your partner shared, or open their invite link.'}
      </p>
      {codeField}
      <Button size="xl" disabled={!body || join.isPending} onClick={() => void submit()}>
        {join.isPending ? 'Joining…' : 'Join'}
      </Button>
      <ProfileError error={error} />
      <div>
        <ButtonLink to="/" variant="secondary">
          Back home
        </ButtonLink>
      </div>
    </Frame>
  );
}
