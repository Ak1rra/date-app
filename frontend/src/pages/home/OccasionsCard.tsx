import { Card, EmptyState } from '../../components/EmptyState';
import { CalendarIcon } from '../../components/Icons';
import { LoadError, Skeleton } from '../../components/forms';
import { nextOccurrence, daysUntil } from '../../hooks/useHome';
import { useImportantDates } from '../../hooks/useImportantDates';

const dateFormatter = new Intl.DateTimeFormat(undefined, { month: 'long', day: 'numeric' });

const kindLabels = {
  anniversary: 'Anniversary',
  birthday: 'Birthday',
  custom: 'Special day',
} as const;

export function OccasionsCard() {
  const dates = useImportantDates();
  const now = new Date();
  const occasions = (Array.isArray(dates.data) ? dates.data : [])
    .flatMap((date) => {
      const occurrence = nextOccurrence(date, now);
      if (!occurrence) return [];
      const days = daysUntil(occurrence, now);
      return days >= 0 && days <= 60 ? [{ date, occurrence, days }] : [];
    })
    .sort((left, right) => left.occurrence.getTime() - right.occurrence.getTime())
    .slice(0, 5);

  return (
    <Card title="Upcoming occasions">
      {dates.isPending ? <Skeleton label="Loading upcoming occasions" /> : null}
      {dates.isError ? (
        <LoadError error={dates.error} onRetry={() => void dates.refetch()} />
      ) : null}
      {!dates.isPending && !dates.isError && occasions.length === 0 ? (
        <EmptyState title="No occasions yet" icon={<CalendarIcon />}>
          Add birthdays and anniversaries in Us and we&apos;ll help you plan ahead.
        </EmptyState>
      ) : null}
      {!dates.isPending && !dates.isError && occasions.length > 0 ? (
        <ul className="flex flex-col gap-3 p-2">
          {occasions.map(({ date, occurrence, days }) => (
            <li
              key={date.id}
              className="flex items-center justify-between gap-3 rounded-2xl bg-surface-2 p-3"
            >
              <span className="flex min-w-0 flex-col gap-1">
                <span className="truncate font-semibold">{date.name}</span>
                <span className="text-sm text-muted">{kindLabels[date.kind]}</span>
              </span>
              <span className="shrink-0 text-right text-sm">
                <time
                  dateTime={`${occurrence.getFullYear()}-${String(occurrence.getMonth() + 1).padStart(2, '0')}-${String(occurrence.getDate()).padStart(2, '0')}`}
                >
                  {dateFormatter.format(occurrence)}
                </time>
                <span className="block font-semibold text-accent">
                  {days === 0 ? 'today' : `in ${days} days`}
                </span>
              </span>
            </li>
          ))}
        </ul>
      ) : null}
    </Card>
  );
}
