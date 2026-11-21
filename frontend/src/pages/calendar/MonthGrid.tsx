import type { CalendarEntry } from '../../hooks/useCalendar';
import { dayKey, entryTone, occasionIcon } from './helpers';

const MAX_CHIPS = 2;

/** A Monday for labelling the weekday header; the date itself is arbitrary. */
const weekdayLabels = Array.from({ length: 7 }, (_, i) =>
  new Intl.DateTimeFormat(undefined, { weekday: 'short' }).format(new Date(2024, 0, 1 + i)),
);

const fullDate = new Intl.DateTimeFormat(undefined, { dateStyle: 'full' });

interface MonthGridProps {
  month: Date;
  days: Date[];
  entriesByDay: Map<string, CalendarEntry[]>;
  selected: string | null;
  onSelect: (key: string) => void;
  busy?: boolean;
}

/** Monday-first month grid: each day is a button showing up to two entry chips, then "+N". */
export function MonthGrid({ month, days, entriesByDay, selected, onSelect, busy }: MonthGridProps) {
  const today = dayKey(new Date());
  return (
    <div aria-busy={busy} className="flex flex-col gap-1">
      <div className="grid grid-cols-7 gap-1 text-center text-xs font-semibold text-muted">
        {weekdayLabels.map((label) => (
          <span key={label}>{label}</span>
        ))}
      </div>
      <div className="grid grid-cols-7 gap-1">
        {days.map((day) => {
          const key = dayKey(day);
          const entries = entriesByDay.get(key) ?? [];
          const outside = day.getMonth() !== month.getMonth();
          const isToday = key === today;
          const isSelected = key === selected;
          return (
            <button
              key={key}
              type="button"
              aria-label={fullDate.format(day)}
              aria-pressed={isSelected}
              aria-current={isToday ? 'date' : undefined}
              onClick={() => onSelect(key)}
              className={`flex min-h-16 min-w-0 flex-col items-stretch gap-1 rounded-xl p-1 text-left md:min-h-24 ${
                outside ? 'bg-surface-2/50 text-muted' : 'bg-surface text-ink'
              } ${isToday ? 'outline-2 outline-accent' : 'border border-line'} ${
                isSelected ? 'ring-2 ring-accent' : ''
              }`}
            >
              <span className="text-xs font-semibold">{day.getDate()}</span>
              {entries.slice(0, MAX_CHIPS).map((entry) => (
                <span
                  key={entry.id + entry.starts_at}
                  className={`block truncate rounded-md px-1 text-xs ${entryTone(entry)}`}
                >
                  {entry.kind === 'occasion' ? (
                    <span aria-hidden="true">{occasionIcon(entry.title)} </span>
                  ) : null}
                  {entry.title}
                </span>
              ))}
              {entries.length > MAX_CHIPS ? (
                <span className="text-xs font-semibold text-muted">
                  +{entries.length - MAX_CHIPS}
                </span>
              ) : null}
            </button>
          );
        })}
      </div>
    </div>
  );
}
