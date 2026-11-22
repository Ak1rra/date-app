const HOUR_MS = 3_600_000;
/** A daily cap resets within 24 h and a monthly one is days away, so 36 h splits them. */
const MONTHLY_AFTER_MS = 36 * HOUR_MS;

function parse(resetAt: string | null): Date | null {
  if (!resetAt) return null;
  const date = new Date(resetAt);
  return Number.isNaN(date.getTime()) ? null : date;
}

/** An absolute local date and time, e.g. "Nov 23, 2026, 12:00 AM". */
export function formatReset(resetAt: string | null): string | null {
  const date = parse(resetAt);
  if (!date) return null;
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(
    date,
  );
}

const startOfDay = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();

/** A weekday and time: "tomorrow at 12:00 AM", "Friday at 9:00 AM", or a date when further out. */
function whenText(date: Date, now: Date): string {
  const time = new Intl.DateTimeFormat(undefined, { timeStyle: 'short' }).format(date);
  const days = Math.round((startOfDay(date) - startOfDay(now)) / (24 * HOUR_MS));
  if (days >= 0 && days <= 1) {
    const day = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' }).format(days, 'day');
    return `${day} at ${time}`;
  }
  if (days > 1 && days < 7) {
    return `${new Intl.DateTimeFormat(undefined, { weekday: 'long' }).format(date)} at ${time}`;
  }
  return `${new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' }).format(date)} at ${time}`;
}

/**
 * "You've used today's AI planning. It resets tomorrow at 12:00 AM." — "this month's" when the
 * reset is more than 36 h away. Null when the server sent no usable `reset_at`.
 */
export function capMessage(
  resetAt: string | null,
  noun = 'AI planning',
  now: Date = new Date(),
): string | null {
  const date = parse(resetAt);
  if (!date) return null;
  const period = date.getTime() - now.getTime() > MONTHLY_AFTER_MS ? "this month's" : "today's";
  return `You've used ${period} ${noun}. It resets ${whenText(date, now)}.`;
}
