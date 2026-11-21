import { useItineraries, type ItinerarySummary } from './useItineraries';
import type { ImportantDateOut } from './useImportantDates';

type DateLike = Pick<ImportantDateOut, 'date' | 'recurring'>;

function localDate(year: number, month: number, day: number) {
  return new Date(year, month - 1, day);
}

function parseDateOnly(value: string) {
  const [year, month, day] = value.split('-').map(Number);
  if (!year || !month || !day) return null;
  const parsed = localDate(year, month, day);
  return parsed.getFullYear() === year &&
    parsed.getMonth() === month - 1 &&
    parsed.getDate() === day
    ? parsed
    : null;
}

function calendarDay(value: Date) {
  return Date.UTC(value.getFullYear(), value.getMonth(), value.getDate());
}

function occurrenceForYear(month: number, day: number, year: number) {
  const adjustedDay =
    month === 2 && day === 29 ? Math.min(day, new Date(year, 2, 0).getDate()) : day;
  return localDate(year, month, adjustedDay);
}

/** Returns the next local-calendar occurrence, or null for a past one-off date. */
export function nextOccurrence(date: DateLike, now: Date = new Date()): Date | null {
  const original = parseDateOnly(date.date);
  if (!original) return null;

  if (!date.recurring) {
    return calendarDay(original) >= calendarDay(now) ? original : null;
  }

  const thisYear = occurrenceForYear(
    original.getMonth() + 1,
    original.getDate(),
    now.getFullYear(),
  );
  return calendarDay(thisYear) >= calendarDay(now)
    ? thisYear
    : occurrenceForYear(original.getMonth() + 1, original.getDate(), now.getFullYear() + 1);
}

/** Difference in local calendar days, intentionally ignoring the time of day. */
export function daysUntil(target: Date, now: Date = new Date()): number {
  return Math.round((calendarDay(target) - calendarDay(now)) / 86_400_000);
}

export function countdownLabel(target: Date, now: Date = new Date()): string {
  const days = daysUntil(target, now);
  if (days <= 0) return 'Tonight';
  if (days === 1) return 'Tomorrow';
  return `In ${days} days`;
}

export function selectNextDate(
  itineraries: ItinerarySummary[] | undefined,
  now: Date = new Date(),
): ItinerarySummary | undefined {
  return (Array.isArray(itineraries) ? itineraries : [])
    .filter(
      (item): item is ItinerarySummary =>
        item.status === 'scheduled' && !!item.starts_at && new Date(item.starts_at) >= now,
    )
    .sort((left, right) => Date.parse(left.starts_at!) - Date.parse(right.starts_at!))[0];
}

export function useNextDate(now: Date = new Date()) {
  const itineraries = useItineraries();
  const data = selectNextDate(itineraries.data, now);
  return { ...itineraries, data };
}
