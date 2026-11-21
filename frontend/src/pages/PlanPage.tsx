import { useState } from 'react';
import { useNavigate } from 'react-router';
import { Button } from '../components/Button';
import { Card } from '../components/EmptyState';
import {
  useBuildPlan,
  useMoreOptions,
  usePlanAll,
  usePlanSessions,
  useShowOptions,
  type PlanOptionOut,
  type PlanSessionIn,
} from '../hooks/usePlan';
import type { ItineraryOut } from '../hooks/useItineraries';
import { useViewerId } from '../hooks/useSession';
import { BuildProgress } from './plan/BuildProgress';
import { OptionsStep } from './plan/OptionsStep';
import { PlanForm, type PlanAction } from './plan/PlanForm';
import { PlanError, WINDOW_ERROR_TEXT, isWindowError } from './plan/PlanError';

interface ActiveSession {
  id: string;
  options: PlanOptionOut[];
  degraded: string[];
}

/**
 * Keyed by the viewer, so a profile switch remounts the body: options (surprise plans included),
 * the kept session and the form all reset, and a request still in flight for the previous profile
 * can no longer call back into this page.
 */
export function PlanPage() {
  const viewerId = useViewerId();
  return <PlanBody key={viewerId ?? 'none'} />;
}

function PlanBody() {
  const navigate = useNavigate();
  const sessions = usePlanSessions();
  const [active, setActive] = useState<ActiveSession | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [progress, setProgress] = useState('');
  // A session that was created but whose options or plan-all step failed, with the inputs it was
  // made for: retrying with the same inputs reuses it, any edit makes the next submit create anew.
  const [kept, setKept] = useState<{ id: string; inputs: string } | null>(null);

  const showOptions = useShowOptions();
  const planAll = usePlanAll();
  const moreOptions = useMoreOptions(active?.id);
  const build = useBuildPlan(active?.id);
  const busy =
    showOptions.isPending || planAll.isPending || moreOptions.isPending || build.isPending;

  const resumable = sessions.data?.find((s) => s.status === 'open' && s.options.length > 0);

  const goToItinerary = (itinerary: ItineraryOut) => void navigate(`/itineraries/${itinerary.id}`);

  function start(action: PlanAction, body: PlanSessionIn) {
    setError(null);
    const inputs = JSON.stringify(body);
    const vars = {
      body,
      sessionId: kept?.inputs === inputs ? kept.id : undefined,
      onSession: (id: string) => setKept({ id, inputs }),
    };
    if (action === 'options') {
      setProgress('Dreaming up three dates for you…');
      showOptions.mutate(vars, {
        onSuccess: (session) => {
          setKept(null);
          setActive({ id: session.id, options: session.options, degraded: session.degraded });
        },
        onError: setError,
      });
    } else {
      setProgress('Planning the whole thing…');
      planAll.mutate(vars, { onSuccess: goToItinerary, onError: setError });
    }
  }

  function more() {
    setError(null);
    setProgress('Finding three more ideas…');
    moreOptions.mutate(undefined, {
      onSuccess: (session) =>
        setActive({ id: session.id, options: session.options, degraded: session.degraded }),
      onError: setError,
    });
  }

  const windowError = !active && isWindowError(error) ? WINDOW_ERROR_TEXT : null;

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-bold">Plan a date</h1>
      {busy ? <BuildProgress label={progress} /> : null}
      {windowError ? null : <PlanError error={error} />}
      {active ? (
        <Card title="Your options">
          <div className="pt-3">
            <OptionsStep
              options={active.options}
              degraded={active.degraded}
              disabled={busy}
              onMore={more}
              onStartOver={() => {
                setActive(null);
                setError(null);
              }}
              onBuild={(body) => {
                setError(null);
                setProgress('Building your date…');
                build.mutate(body, { onSuccess: goToItinerary, onError: setError });
              }}
            />
          </div>
        </Card>
      ) : (
        <>
          {resumable ? (
            <Card>
              <div className="flex flex-col items-start gap-2 p-2">
                <p className="font-semibold">You have options waiting.</p>
                <Button
                  variant="secondary"
                  disabled={busy}
                  onClick={() =>
                    setActive({ id: resumable.id, options: resumable.options, degraded: [] })
                  }
                >
                  Pick up where you left off
                </Button>
              </div>
            </Card>
          ) : null}
          {sessions.isError ? (
            <div
              role="status"
              className="flex flex-wrap items-center gap-3 rounded-xl border border-line bg-surface-2 p-3 text-sm"
            >
              <span>Couldn&apos;t check for a plan in progress.</span>
              <Button variant="secondary" onClick={() => void sessions.refetch()}>
                Retry
              </Button>
            </div>
          ) : null}
          <Card title="Tell us about the night">
            <div className="pt-3">
              <PlanForm disabled={busy} onSubmit={start} windowError={windowError} />
            </div>
          </Card>
        </>
      )}
    </div>
  );
}
