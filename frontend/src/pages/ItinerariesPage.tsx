import { ButtonLink } from '../components/Button';
import { Card, EmptyState } from '../components/EmptyState';
import { LoadError, Skeleton } from '../components/forms';
import { useItineraries } from '../hooks/useItineraries';
import { formatDateWindow } from './itinerary/dateWindow';

export function ItinerariesPage() {
  const itineraries = useItineraries();

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-end justify-between gap-3">
        <div>
          <p className="text-sm font-semibold text-muted">Your little adventures</p>
          <h1 className="text-2xl font-bold">Itineraries</h1>
        </div>
        <div className="flex flex-wrap justify-end gap-2">
          <ButtonLink to="/templates" variant="secondary">
            Templates
          </ButtonLink>
          <ButtonLink to="/plan" variant="secondary">
            Plan a date
          </ButtonLink>
        </div>
      </div>

      <Card>
        {itineraries.isPending ? <Skeleton label="Loading itineraries" /> : null}
        {itineraries.isError ? (
          <LoadError error={itineraries.error} onRetry={() => void itineraries.refetch()} />
        ) : null}
        {itineraries.data?.length === 0 ? (
          <EmptyState
            title="No dates planned yet"
            action={<ButtonLink to="/plan">Plan our first date</ButtonLink>}
          >
            Pick a vibe and we&apos;ll turn it into a date worth getting dressed up for.
          </EmptyState>
        ) : null}
        {itineraries.data && itineraries.data.length > 0 ? (
          <ul className="flex flex-col gap-3">
            {itineraries.data.map((item) => (
              <li key={item.id}>
                <ButtonLink
                  to={`/itineraries/${item.id}`}
                  variant="secondary"
                  className="w-full justify-between rounded-2xl text-left"
                >
                  <span className="flex min-w-0 flex-col items-start gap-1">
                    <span className="truncate font-semibold">{item.title}</span>
                    <span className="text-sm font-normal text-muted">
                      {formatDateWindow(item.starts_at, item.ends_at, 'medium') ??
                        'Date to be chosen'}{' '}
                      · {item.stop_count} {item.stop_count === 1 ? 'stop' : 'stops'}
                    </span>
                  </span>
                  <span className="shrink-0 text-xs font-semibold uppercase tracking-wide">
                    {item.status}
                  </span>
                </ButtonLink>
              </li>
            ))}
          </ul>
        ) : null}
      </Card>
    </div>
  );
}
