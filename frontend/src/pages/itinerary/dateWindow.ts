export type DateWindowStyle = 'full' | 'medium';

export function formatDateWindow(
  startsAt: string | null,
  endsAt: string | null,
  style: DateWindowStyle,
) {
  if (!startsAt) return null;

  const startDate = new Date(startsAt);
  const endDate = endsAt ? new Date(endsAt) : null;
  const sameDay =
    !endDate ||
    (startDate.getFullYear() === endDate.getFullYear() &&
      startDate.getMonth() === endDate.getMonth() &&
      startDate.getDate() === endDate.getDate());

  if (style === 'full') {
    const date = new Intl.DateTimeFormat(undefined, { dateStyle: 'full' }).format(startDate);
    const time = new Intl.DateTimeFormat(undefined, { timeStyle: 'short' });
    const end = endDate
      ? ` – ${sameDay ? '' : `${new Intl.DateTimeFormat(undefined, { dateStyle: 'full' }).format(endDate)} · `}${time.format(endDate)}`
      : '';
    return `${date} · ${time.format(startDate)}${end}`;
  }

  const formatter = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' });
  const start = formatter.format(startDate);
  if (!endDate) return start;
  const end = sameDay
    ? new Intl.DateTimeFormat(undefined, { timeStyle: 'short' }).format(endDate)
    : formatter.format(endDate);
  return `${start} – ${end}`;
}

/** A <input type="datetime-local"> value (browser-local wall time) as an aware ISO string. */
export function localToIso(value: string) {
  return new Date(value).toISOString();
}
