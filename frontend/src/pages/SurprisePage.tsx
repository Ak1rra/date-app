import { useEffect, useRef, useState } from 'react';
import { Navigate, useParams } from 'react-router';
import { ApiError } from '../api/http';
import { Card, EmptyState } from '../components/EmptyState';
import { LoadError, Skeleton } from '../components/forms';
import { usePersonTheme } from '../hooks/usePersonTheme';
import { useItinerary } from '../hooks/useItineraries';
import { useSession } from '../hooks/useSession';
import { useSurpriseView, type ReleasedHintOut } from '../hooks/useSurpriseView';

const HINT_LABELS: Record<string, string> = {
  dress_code: 'What to wear',
  bring: 'Bring along',
  ready_by: 'Be ready by',
  eat: 'Food',
  energy: 'Energy',
  indoor_outdoor: 'Indoors or out',
  weather: 'Weather',
  travel: 'Getting there',
  custom: 'A little hint',
};

const dateTime = new Intl.DateTimeFormat(undefined, { dateStyle: 'full', timeStyle: 'short' });
const timeOnly = new Intl.DateTimeFormat(undefined, { timeStyle: 'short' });

const hintLabel = (kind: string) => HINT_LABELS[kind] ?? 'A little hint';

/** Days, hours and minutes until the start, or "It's time!" once it has begun. */
function countdownText(target: Date, now: Date): string {
  const remaining = target.getTime() - now.getTime();
  if (remaining <= 0) return "It's time!";
  const minutes = Math.ceil(remaining / 60_000);
  const days = Math.floor(minutes / 1440);
  const hours = Math.floor((minutes % 1440) / 60);
  const mins = minutes % 60;
  const plural = (n: number, unit: string) => `${n} ${unit}${n === 1 ? '' : 's'}`;
  return [plural(days, 'day'), plural(hours, 'hour'), plural(mins, 'minute')].join(' · ');
}

function useNow(intervalMs: number) {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = setInterval(() => setNow(new Date()), intervalMs);
    return () => clearInterval(timer);
  }, [intervalMs]);
  return now;
}

function newestFirst(hints: ReleasedHintOut[]) {
  return [...hints].sort((a, b) => Date.parse(b.released_at) - Date.parse(a.released_at));
}

export function SurprisePage() {
  const { id } = useParams<{ id: string }>();
  const session = useSession();
  usePersonTheme(session.data?.user?.role);
  const surprise = useSurpriseView(id);
  const notFound = surprise.error instanceof ApiError && surprise.error.status === 404;
  // Once the surprise is revealed the itinerary becomes visible to the surprisee.
  const itinerary = useItinerary(notFound ? id : undefined);
  const now = useNow(60_000);

  // Refetch once the local clock passes the reveal, so a page left open gets redirected.
  const revealAtRaw = surprise.data?.reveal_at;
  const refetchedFor = useRef<string | null>(null);
  const { refetch } = surprise;
  useEffect(() => {
    if (!revealAtRaw || refetchedFor.current === revealAtRaw) return;
    if (now.getTime() >= Date.parse(revealAtRaw)) {
      refetchedFor.current = revealAtRaw;
      void refetch();
    }
  }, [now, revealAtRaw, refetch]);

  if (surprise.isPending) return <Skeleton label="Loading your surprise" />;
  if (surprise.isError) {
    if (notFound) {
      if (itinerary.isPending) return <Skeleton label="Loading your surprise" />;
      if (itinerary.data) return <Navigate to={`/itineraries/${id}`} replace />;
      const hidden =
        itinerary.error instanceof ApiError &&
        (itinerary.error.status === 404 || itinerary.error.status === 403);
      if (!hidden) {
        return <LoadError error={itinerary.error} onRetry={() => void itinerary.refetch()} />;
      }
      return (
        <Card>
          <EmptyState title="This surprise isn't here">
            It may have been called off, or it was never meant for you.
          </EmptyState>
        </Card>
      );
    }
    return <LoadError error={surprise.error} onRetry={() => void surprise.refetch()} />;
  }
  if (!surprise.data) return null;
  const data = surprise.data;
  const startsAt = new Date(data.starts_at);
  const revealAt = data.reveal_at ? new Date(data.reveal_at) : null;
  const showReveal = revealAt !== null && revealAt.getTime() !== startsAt.getTime();
  const hints = newestFirst(data.hints);

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-col gap-2">
        <h1 className="text-3xl font-bold">A surprise is coming ✨</h1>
        <p className="text-muted">
          <time dateTime={data.starts_at}>{dateTime.format(startsAt)}</time>
          {data.ends_at ? ` · until about ${timeOnly.format(new Date(data.ends_at))}` : null}
        </p>
        <p className="text-xl font-semibold text-accent" aria-live="polite">
          {countdownText(startsAt, now)}
        </p>
        {showReveal ? (
          <p className="text-muted">All will be revealed {dateTime.format(revealAt)}</p>
        ) : null}
      </header>

      <Card title="Hints">
        {hints.length === 0 ? (
          <EmptyState title="No hints yet">
            No hints yet — your partner is keeping you guessing.
          </EmptyState>
        ) : (
          <ul className="flex flex-col gap-3 p-2">
            {hints.map((hint) => (
              <li key={hint.id} className="flex flex-col gap-1 rounded-2xl bg-surface-2 p-3">
                <span className="text-xs font-semibold tracking-wide text-accent uppercase">
                  {hintLabel(hint.kind)}
                </span>
                <span>{hint.text}</span>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
