import { useState, type FormEvent } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router';
import { Button } from '../components/Button';
import { FormError, LoadError, Skeleton, TextField } from '../components/forms';
import {
  isProviderUnavailable,
  usePatchCouple,
  usePatchMe,
  useSetHomeArea,
} from '../hooks/useCouple';
import { toProfileIn, useProfiles, useSaveProfile } from '../hooks/useProfiles';
import { setSetupSkipped } from '../hooks/setupSkipped';
import { SESSION_KEY, useSession } from '../hooks/useSession';
import { LOOKUP_UNAVAILABLE } from './us/HomeAreaSection';

const STEPS = 3;

function browserTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  } catch {
    return 'UTC';
  }
}

export function SetupPage() {
  const profiles = useProfiles();
  const saveProfile = useSaveProfile();
  const patchMe = usePatchMe();
  const patchCouple = usePatchCouple();
  const setHome = useSetHomeArea();
  const queryClient = useQueryClient();
  const session = useSession();
  const navigate = useNavigate();

  const [step, setStep] = useState(0);
  const [myNameEdit, setMyNameEdit] = useState<string | null>(null);
  const [partnerName, setPartnerName] = useState('');
  const [query, setQuery] = useState('');
  const [timezone, setTimezone] = useState(browserTimezone);
  const [finishError, setFinishError] = useState<unknown>(null);
  const [finishing, setFinishing] = useState(false);

  const me = profiles.data?.find((p) => p.is_me);
  // Until they type, offer the name already on file: the profile's, else the session user's (no-login mode seeds one there).
  const myName = myNameEdit ?? me?.display_name ?? session.data?.user?.display_name ?? '';
  // Every step's success callback advances the wizard, so nothing may be pending when Back is used.
  const busy =
    patchMe.isPending ||
    saveProfile.isPending ||
    patchCouple.isPending ||
    setHome.isPending ||
    finishing;
  const partner = profiles.data?.find((p) => !p.is_me);
  const partnerEditable = partner?.can_edit ?? false;
  const lookupOff = isProviderUnavailable(setHome.error);

  const submitMyName = (e: FormEvent) => {
    e.preventDefault();
    const name = myName.trim();
    if (!name || !me) return;
    patchMe.mutate(
      { display_name: name },
      {
        onSuccess: () =>
          saveProfile.mutate(
            { id: me.id, body: { ...toProfileIn(me), display_name: name } },
            { onSuccess: () => setStep(1) },
          ),
      },
    );
  };

  const submitPartnerName = (e: FormEvent) => {
    e.preventDefault();
    const name = partnerName.trim();
    if (!partner || !partnerEditable) {
      setStep(2);
      return;
    }
    if (!name) return;
    saveProfile.mutate(
      { id: partner.id, body: { ...toProfileIn(partner), display_name: name } },
      { onSuccess: () => setStep(2) },
    );
  };

  /** Saves the time zone, refreshes the session (so the route guard sees the name), then goes home. */
  const finish = async () => {
    setFinishing(true);
    setFinishError(null);
    try {
      await patchCouple.mutateAsync({ timezone });
      await patchMe.mutateAsync({ timezone });
      await queryClient.invalidateQueries({ queryKey: SESSION_KEY });
      void navigate('/', { replace: true });
    } catch (error) {
      setFinishError(error);
      setFinishing(false);
    }
  };

  const submitHome = (e: FormEvent) => {
    e.preventDefault();
    if (!query.trim()) return;
    setHome.mutate(query.trim(), { onSuccess: () => void finish() });
  };

  return (
    <main className="mx-auto flex min-h-dvh max-w-md flex-col justify-center gap-6 bg-canvas p-6 text-ink">
      <header className="flex flex-col gap-1">
        <p className="text-sm font-semibold text-muted">
          Step {step + 1} of {STEPS}
        </p>
        <h1 className="text-3xl font-bold">Let&apos;s get you set up</h1>
      </header>

      {profiles.isPending ? <Skeleton label="Loading your profile" /> : null}
      {profiles.isError ? (
        <LoadError error={profiles.error} onRetry={() => void profiles.refetch()} />
      ) : null}

      {profiles.data && step === 0 ? (
        <form onSubmit={submitMyName}>
          <fieldset disabled={busy} className="flex min-w-0 flex-col gap-4 border-0 p-0">
            <TextField
              label="Your first name"
              hint="This is how we will address you."
              autoComplete="given-name"
              required
              value={myName}
              onChange={(e) => setMyNameEdit(e.target.value)}
            />
            <FormError error={patchMe.error ?? saveProfile.error} />
            <Button type="submit" size="xl" disabled={!myName.trim() || busy}>
              {busy ? 'Saving…' : 'Next'}
            </Button>
          </fieldset>
        </form>
      ) : null}

      {profiles.data && step === 1 ? (
        <form onSubmit={submitPartnerName}>
          <fieldset disabled={busy} className="flex min-w-0 flex-col gap-4 border-0 p-0">
            {partnerEditable ? (
              <TextField
                label="Your partner's first name"
                hint="You can fill in the rest of what you know about them later."
                required
                value={partnerName}
                onChange={(e) => setPartnerName(e.target.value)}
              />
            ) : (
              <p className="text-muted">
                Your partner keeps their own profile, so they will add their name themselves.
              </p>
            )}
            <FormError error={saveProfile.error} />
            <div className="flex flex-col gap-2">
              <Button
                type="submit"
                size="xl"
                disabled={(partnerEditable && !partnerName.trim()) || busy}
              >
                {busy ? 'Saving…' : 'Next'}
              </Button>
              <Button variant="secondary" onClick={() => setStep(0)} disabled={busy}>
                Back
              </Button>
            </div>
          </fieldset>
        </form>
      ) : null}

      {profiles.data && step === 2 ? (
        <form onSubmit={submitHome}>
          <fieldset disabled={busy} className="flex min-w-0 flex-col gap-4 border-0 p-0">
            <TextField
              label="Home area"
              hint="A neighbourhood or town. We use it to find dates nearby."
              placeholder="e.g. Downtown Austin"
              required={!lookupOff}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            <TextField
              label="Time zone"
              hint="Filled in from your device. Change it if it is wrong."
              value={timezone}
              onChange={(e) => setTimezone(e.target.value)}
            />
            {lookupOff ? (
              <p role="alert" className="rounded-xl border border-coral p-3 text-sm">
                {LOOKUP_UNAVAILABLE}
              </p>
            ) : (
              <FormError error={setHome.error} />
            )}
            <FormError error={finishError} />
            <div className="flex flex-col gap-2">
              {lookupOff ? null : (
                <Button type="submit" size="xl" disabled={!query.trim() || busy}>
                  {busy ? 'Saving…' : 'Finish'}
                </Button>
              )}
              <Button
                size={lookupOff ? 'xl' : 'md'}
                variant={lookupOff ? 'primary' : 'secondary'}
                onClick={() => {
                  setSetupSkipped(true);
                  void finish();
                }}
                disabled={busy}
              >
                {finishing ? 'Finishing…' : 'Skip for now'}
              </Button>
              <Button variant="secondary" onClick={() => setStep(1)} disabled={busy}>
                Back
              </Button>
            </div>
          </fieldset>
        </form>
      ) : null}
    </main>
  );
}
