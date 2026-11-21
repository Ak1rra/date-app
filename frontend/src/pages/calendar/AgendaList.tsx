import { Link } from 'react-router';
import { PrivateBadge } from '../../components/forms';
import type { CalendarEntry } from '../../hooks/useCalendar';
import { entryTone, formatTime, isCancelled, occasionIcon } from './helpers';

const dayLabel = new Intl.DateTimeFormat(undefined, {
  weekday: 'short',
  month: 'short',
  day: 'numeric',
});

function when(entry: CalendarEntry) {
  if (entry.all_day) return 'All day';
  const start = formatTime(entry.starts_at);
  return entry.ends_at ? `${start} – ${formatTime(entry.ends_at)}` : start;
}

/** The entries for the chosen day, or what is coming up in the visible month. */
export function AgendaList({ entries }: { entries: CalendarEntry[] }) {
  if (entries.length === 0) return <p className="p-2 text-muted">Nothing planned here.</p>;
  return (
    <ul className="flex flex-col gap-3 pt-2">
      {entries.map((entry) => (
        <li
          key={entry.id + entry.starts_at}
          className={`flex flex-col gap-1 rounded-2xl p-3 ${entryTone(entry)}`}
        >
          <span className="text-xs font-semibold tracking-wide uppercase">
            {dayLabel.format(new Date(entry.starts_at))} · {when(entry)}
          </span>
          <span className="font-semibold">
            {entry.kind === 'occasion' ? (
              <span aria-hidden="true">{occasionIcon(entry.title)} </span>
            ) : null}
            {entry.kind === 'date' && entry.itinerary_id ? (
              <Link to={`/itineraries/${entry.itinerary_id}`} className="underline">
                {entry.title}
              </Link>
            ) : (
              entry.title
            )}
          </span>
          {isCancelled(entry) ? <span className="text-sm">Cancelled</span> : null}
          {entry.is_surprise ? (
            <span>
              <PrivateBadge />
            </span>
          ) : null}
        </li>
      ))}
    </ul>
  );
}
