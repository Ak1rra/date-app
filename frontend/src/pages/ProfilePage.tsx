import { Link } from 'react-router';
import { ButtonLink } from '../components/Button';
import { AccountData } from '../components/AccountData';
import { Card } from '../components/EmptyState';
import { LeaveCouple } from '../components/LeaveCouple';
import { PushSettings } from '../components/PushSettings';
import { PROFILES, ProfileError } from '../components/ProfileChoice';
import { useChooseProfile } from '../hooks/useChooseProfile';
import { useMeta } from '../hooks/useMeta';
import { useSession, useViewerId } from '../hooks/useSession';

export function ProfilePage() {
  const session = useSession();
  const viewerId = useViewerId();
  const meta = useMeta();
  const choose = useChooseProfile();
  const user = session.data?.user;
  const current = user?.role === 'owner' ? 'husband' : user?.role === 'partner' ? 'wife' : null;
  const currentLabel = PROFILES.find((p) => p.value === current)?.label;

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-bold">Settings</h1>
      <Card>
        <div className="flex flex-col gap-4 p-2">
          <p className="text-lg">
            You&apos;re using Date Planner as{' '}
            <strong className="font-semibold">{currentLabel ?? 'a guest'}</strong>
          </p>
          {user?.display_name ? <p className="text-muted">{user.display_name}</p> : null}
          {meta.data?.no_login ? (
            <>
              <div role="group" aria-label="Switch profile" className="grid grid-cols-2 gap-2">
                {PROFILES.map(({ value, label, tone }) => {
                  const selected = value === current;
                  return (
                    <button
                      key={value}
                      type="button"
                      aria-pressed={selected}
                      disabled={choose.isPending}
                      onClick={() => {
                        if (!selected) choose.mutate(value);
                      }}
                      className={`min-h-11 rounded-full px-5 py-2 font-semibold motion-safe:transition disabled:opacity-60 ${tone} ${
                        selected ? 'border-4' : 'border hover:brightness-95'
                      }`}
                    >
                      {choose.isPending && choose.variables === value ? 'Opening…' : label}
                    </button>
                  );
                })}
              </div>
              <ProfileError error={choose.error} />
              <p className="text-sm text-muted">Anyone using this app can switch profiles.</p>
            </>
          ) : null}
        </div>
      </Card>
      <Card title="Home Screen">
        <div className="flex flex-col gap-3 p-2">
          <p className="text-muted">
            Keep us one tap away, and get nudges when a date needs you. 😉
          </p>
          <div>
            <ButtonLink to="/install" variant="secondary">
              Install &amp; trust guide
            </ButtonLink>
          </div>
        </div>
      </Card>
      <PushSettings key={viewerId ?? 'none'} />
      <LeaveCouple key={`leave-${viewerId ?? 'none'}`} />
      <AccountData key={`account-${viewerId ?? 'none'}`} />
      <p className="text-sm text-muted">
        <Link to="/privacy" className="underline">
          Privacy
        </Link>
      </p>
    </div>
  );
}
