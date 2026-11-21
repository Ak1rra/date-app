import { useMemo, useState } from 'react';
import { useSearchParams } from 'react-router';
import { Button } from '../components/Button';
import { Card } from '../components/EmptyState';
import { LoadError } from '../components/forms';
import { useCalendar, usePrefetchUpcoming } from '../hooks/useCalendar';
import { useViewerId } from '../hooks/useSession';
import { AgendaList } from './calendar/AgendaList';
import { MonthGrid } from './calendar/MonthGrid';
import { SuggestTimes } from './calendar/SuggestTimes';
import {
  addMonths,
  dayKey,
  gridDays,
  gridRange,
  groupByDay,
  startOfMonth,
} from './calendar/helpers';

const monthName = new Intl.DateTimeFormat(undefined, { month: 'long', year: 'numeric' });
const dayName = new Intl.DateTimeFormat(undefined, { dateStyle: 'full' });

/** Keyed by the viewer, so a profile switch resets the month and the selected day. */
export function CalendarPage() {
  const viewerId = useViewerId();
  return <CalendarBody key={viewerId ?? 'none'} />;
}

function CalendarBody() {
  const [params] = useSearchParams();
  const [now] = useState(() => Date.now());
  const [month, setMonth] = useState(() => startOfMonth(new Date(now)));
  const [selected, setSelected] = useState<string | null>(null);
  usePrefetchUpcoming();

  const days = useMemo(() => gridDays(month), [month]);
  const { from, to } = useMemo(() => gridRange(days), [days]);
  const calendar = useCalendar(from, to);

  const entriesByDay = useMemo(() => groupByDay(calendar.data ?? []), [calendar.data]);
  const agendaEntries = useMemo(() => {
    if (selected) return entriesByDay.get(selected) ?? [];
    const thisMonth = dayKey(month).slice(0, 7);
    return (calendar.data ?? []).filter(
      (entry) =>
        dayKey(new Date(entry.starts_at)).startsWith(thisMonth) &&
        new Date(entry.ends_at ?? entry.starts_at).getTime() >= now,
    );
  }, [selected, entriesByDay, calendar.data, month, now]);

  function go(delta: number) {
    setMonth((m) => addMonths(m, delta));
    setSelected(null);
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-end justify-between gap-3">
        <div>
          <p className="text-sm font-semibold text-muted">Planned dates and occasions</p>
          <h1 className="text-2xl font-bold">Calendar</h1>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="secondary" aria-label="Previous month" onClick={() => go(-1)}>
            ‹
          </Button>
          <h2 aria-live="polite" className="min-w-36 text-center text-lg font-semibold">
            {monthName.format(month)}
          </h2>
          <Button variant="secondary" aria-label="Next month" onClick={() => go(1)}>
            ›
          </Button>
        </div>
      </div>

      {calendar.isError ? (
        <LoadError error={calendar.error} onRetry={() => void calendar.refetch()} />
      ) : null}

      <div className="grid gap-6 md:grid-cols-[minmax(0,2fr)_minmax(0,1fr)] md:items-start">
        <Card>
          <MonthGrid
            month={month}
            days={days}
            entriesByDay={entriesByDay}
            selected={selected}
            busy={calendar.isPending}
            onSelect={(key) => setSelected((current) => (current === key ? null : key))}
          />
        </Card>
        <div className="flex flex-col gap-6">
          <Card title="Agenda">
            <h3 className="px-2 pt-2 font-semibold">
              {selected
                ? dayName.format(new Date(`${selected}T00:00`))
                : `Coming up in ${monthName.format(month)}`}
            </h3>
            <AgendaList entries={agendaEntries} />
          </Card>
          <SuggestTimes ideaId={params.get('idea')} />
        </div>
      </div>
    </div>
  );
}
