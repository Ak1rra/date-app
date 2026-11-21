import { useQueries } from '@tanstack/react-query';
import { ButtonLink } from '../../components/Button';
import { Card, EmptyState } from '../../components/EmptyState';
import { LoadError, Skeleton } from '../../components/forms';
import { itineraryQueryOptions, useItineraries } from '../../hooks/useItineraries';
import { useViewerId } from '../../hooks/useSession';

const dueDayFormatter = new Intl.DateTimeFormat(undefined, {
  weekday: 'short',
  month: 'short',
  day: 'numeric',
});

export function FinishingTouchesCard() {
  const itineraries = useItineraries();
  const userId = useViewerId();
  const now = new Date();
  const end = now.getTime() + 30 * 86_400_000;
  const candidates = (Array.isArray(itineraries.data) ? itineraries.data : []).filter((item) => {
    const startsAt = item.starts_at ? Date.parse(item.starts_at) : NaN;
    return (
      item.status === 'scheduled' && item.is_planner && startsAt >= now.getTime() && startsAt <= end
    );
  });
  const details = useQueries({
    queries: candidates.map((item) => itineraryQueryOptions(userId, item.id)),
  });
  const dueItems = details.flatMap((query, index) => {
    const itinerary = query.data;
    if (!itinerary) return [];
    return itinerary.checklist
      .filter((item) => {
        const dueAt = item.due_at ? Date.parse(item.due_at) : NaN;
        return !item.done && dueAt >= now.getTime() && dueAt <= now.getTime() + 7 * 86_400_000;
      })
      .map((item) => ({ item, itinerary: candidates[index]! }));
  });
  dueItems.sort((left, right) => Date.parse(left.item.due_at!) - Date.parse(right.item.due_at!));
  const detailsPending = details.some((query) => query.isPending);
  const detailsError = details.find((query) => query.isError);

  return (
    <Card title="Finishing touches due">
      {itineraries.isPending || detailsPending ? (
        <Skeleton label="Loading finishing touches" />
      ) : null}
      {itineraries.isError ? (
        <LoadError error={itineraries.error} onRetry={() => void itineraries.refetch()} />
      ) : null}
      {!itineraries.isPending && !itineraries.isError && detailsError ? (
        <LoadError
          error={detailsError.error}
          onRetry={() => {
            void Promise.all(
              details.filter((query) => query.isError).map((query) => query.refetch()),
            );
          }}
        />
      ) : null}
      {!itineraries.isPending &&
      !itineraries.isError &&
      !detailsPending &&
      !detailsError &&
      dueItems.length === 0 ? (
        <EmptyState title="All caught up">
          When a planned date needs a reservation, a gift or a booking, the reminders land here.
        </EmptyState>
      ) : null}
      {!itineraries.isPending &&
      !itineraries.isError &&
      !detailsPending &&
      !detailsError &&
      dueItems.length > 0 ? (
        <ul className="flex flex-col gap-3 p-2">
          {dueItems.map(({ item, itinerary }) => (
            <li
              key={`${itinerary.id}-${item.id}`}
              className="flex flex-col gap-1 rounded-2xl bg-surface-2 p-3"
            >
              <span className="font-semibold">{item.text}</span>
              <span className="text-sm text-muted">
                Due {dueDayFormatter.format(new Date(item.due_at!))}
              </span>
              <ButtonLink
                to={`/itineraries/${itinerary.id}`}
                variant="secondary"
                className="self-start text-sm"
              >
                {itinerary.title}
              </ButtonLink>
            </li>
          ))}
        </ul>
      ) : null}
    </Card>
  );
}
