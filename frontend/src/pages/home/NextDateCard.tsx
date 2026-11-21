import { ButtonLink } from '../../components/Button';
import { Card, EmptyState } from '../../components/EmptyState';
import { LoadError, Skeleton } from '../../components/forms';
import { countdownLabel, useNextDate } from '../../hooks/useHome';

const dateTimeFormatter = new Intl.DateTimeFormat(undefined, {
  dateStyle: 'full',
  timeStyle: 'short',
});

export function NextDateCard() {
  const itineraries = useNextDate();

  return (
    <Card title="Next date" className="md:col-span-2">
      {itineraries.isPending ? <Skeleton label="Loading your next date" /> : null}
      {itineraries.isError ? (
        <LoadError error={itineraries.error} onRetry={() => void itineraries.refetch()} />
      ) : null}
      {!itineraries.isPending && !itineraries.isError && !itineraries.data ? (
        <EmptyState title="Nothing planned yet">
          Your next date will show up here, with the countdown and everything you still need to do.
          Tap &ldquo;Plan a date&rdquo; to start one.
        </EmptyState>
      ) : null}
      {itineraries.data?.starts_at ? (
        <div className="flex flex-col gap-4 p-2">
          <ButtonLink
            to={`/itineraries/${itineraries.data.id}`}
            variant="secondary"
            className="flex-col items-start rounded-2xl p-4 text-left"
          >
            <span className="flex w-full items-start justify-between gap-3">
              <span className="text-lg font-semibold">{itineraries.data.title}</span>
              <span className="shrink-0 text-xs font-semibold uppercase tracking-wide text-accent">
                {countdownLabel(new Date(itineraries.data.starts_at))}
              </span>
            </span>
            <time className="text-sm font-normal text-muted" dateTime={itineraries.data.starts_at}>
              {dateTimeFormatter.format(new Date(itineraries.data.starts_at))}
            </time>
            <span className="text-sm font-normal text-muted">
              {itineraries.data.stop_count} {itineraries.data.stop_count === 1 ? 'stop' : 'stops'}
            </span>
            {itineraries.data.is_planner && itineraries.data.is_surprise ? (
              <span className="rounded-full bg-accent-soft px-3 py-1 text-xs font-semibold text-accent">
                Only you can see this
              </span>
            ) : null}
          </ButtonLink>
        </div>
      ) : null}
    </Card>
  );
}
