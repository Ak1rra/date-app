import type { CalendarEntry } from '../../hooks/useCalendar';
import { todayLocal } from '../plan/window';

/** YYYY-MM-DD in the browser's timezone. */
export const dayKey = (d: Date) => todayLocal(d);

export const startOfMonth = (d: Date) => new Date(d.getFullYear(), d.getMonth(), 1);

export const addMonths = (d: Date, n: number) => new Date(d.getFullYear(), d.getMonth() + n, 1);

/** The days a month grid shows: whole Monday-first weeks (4-6 of them, so at most 42 days). */
export function gridDays(month: Date): Date[] {
  const year = month.getFullYear();
  const m = month.getMonth();
  const offset = (new Date(year, m, 1).getDay() + 6) % 7;
  const inMonth = new Date(year, m + 1, 0).getDate();
  const count = Math.ceil((offset + inMonth) / 7) * 7;
  return Array.from({ length: count }, (_, i) => new Date(year, m, 1 - offset + i));
}

/** The query range covering every visible day, as ISO datetimes. */
export function gridRange(days: Date[]): { from: string; to: string } {
  const first = days[0]!;
  const last = days[days.length - 1]!;
  return {
    from: first.toISOString(),
    // The API's `to` is exclusive, so end at the next local midnight.
    to: new Date(last.getFullYear(), last.getMonth(), last.getDate() + 1).toISOString(),
  };
}

/** Entries bucketed by the local day they start on (the server sorts them by start). */
export function groupByDay(entries: CalendarEntry[]): Map<string, CalendarEntry[]> {
  const byDay = new Map<string, CalendarEntry[]>();
  for (const entry of entries) {
    const key = dayKey(new Date(entry.starts_at));
    byDay.set(key, [...(byDay.get(key) ?? []), entry]);
  }
  return byDay;
}

export const formatTime = (iso: string) =>
  new Intl.DateTimeFormat(undefined, { timeStyle: 'short' }).format(new Date(iso));

/** HH:MM in the browser's timezone (what an <input type="time"> wants). */
export function timeValue(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export const isCancelled = (entry: CalendarEntry) => entry.status === 'cancelled';

export function occasionIcon(title: string): string {
  if (/birthday/i.test(title)) return '🎂';
  if (/anniversary/i.test(title)) return '💍';
  return '🎉';
}

/** Colour coding: occasions muted, announced dates accent, your own surprise a distinct border. */
export function entryTone(entry: CalendarEntry): string {
  const base = isCancelled(entry) ? 'line-through ' : '';
  if (entry.kind === 'occasion') return `${base}bg-surface-2 text-ink`;
  if (entry.is_surprise) return `${base}border-2 border-dashed border-accent bg-surface text-ink`;
  if (entry.announced) return `${base}bg-accent-soft text-accent`;
  return `${base}border border-line bg-surface text-ink`;
}
