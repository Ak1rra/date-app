export interface WindowPreset {
  id: string;
  label: string;
  start: string;
  end: string;
}

export const DEFAULT_PRESET: WindowPreset = {
  id: 'evening',
  label: 'Evening',
  start: '18:00',
  end: '23:00',
};

export const WINDOW_PRESETS: readonly WindowPreset[] = [
  DEFAULT_PRESET,
  { id: 'afternoon', label: 'Afternoon', start: '13:00', end: '18:00' },
  { id: 'all-day', label: 'All day', start: '10:00', end: '22:00' },
];

/** Today as YYYY-MM-DD in the browser's timezone (what an <input type="date"> wants). */
export function todayLocal(now = new Date()): string {
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

/**
 * The window as aware ISO strings: the date and times are read in the browser's timezone, so
 * "18:00" means 6pm where she is. Null when a field is empty or the end is not after the start.
 */
export function buildWindow(
  date: string,
  start: string,
  end: string,
): { window_start: string; window_end: string } | null {
  if (!date || !start || !end) return null;
  const from = new Date(`${date}T${start}`);
  const to = new Date(`${date}T${end}`);
  if (Number.isNaN(from.getTime()) || Number.isNaN(to.getTime()) || to <= from) return null;
  return { window_start: from.toISOString(), window_end: to.toISOString() };
}
