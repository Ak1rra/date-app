import { describe, expect, it } from 'vitest';
import { capMessage, formatReset } from './resetTime';

const time = (d: Date) => new Intl.DateTimeFormat(undefined, { timeStyle: 'short' }).format(d);

describe('capMessage', () => {
  const now = new Date(2026, 10, 22, 15, 0);

  it("says today's with a tomorrow reset for a daily cap", () => {
    const reset = new Date(2026, 10, 23, 0, 0);
    expect(capMessage(reset.toISOString(), 'AI planning', now)).toBe(
      `You've used today's AI planning. It resets tomorrow at ${time(reset)}.`,
    );
  });

  it("says this month's with a date when the reset is more than 36 h away", () => {
    const reset = new Date(2026, 11, 1, 0, 0);
    const text = capMessage(reset.toISOString(), 'AI planning', now);
    expect(text).toMatch(/^You've used this month's AI planning\. It resets /);
    expect(text).toContain(`Dec 1, 2026 at ${time(reset)}`);
  });

  it('names a weekday within the week', () => {
    const reset = new Date(2026, 10, 25, 9, 0);
    expect(capMessage(reset.toISOString(), 'AI planning', now)).toContain('Wednesday at');
  });

  it('uses the noun it is given', () => {
    const reset = new Date(2026, 10, 23, 0, 0);
    expect(capMessage(reset.toISOString(), 'chat messages', now)).toMatch(
      /^You've used today's chat messages\./,
    );
  });

  it('is null without a usable reset time', () => {
    expect(capMessage(null)).toBeNull();
    expect(capMessage('not a date')).toBeNull();
  });
});

describe('formatReset', () => {
  it('formats a medium date with a short time', () => {
    const iso = '2026-11-22T05:00:00+00:00';
    expect(formatReset(iso)).toBe(
      new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(
        new Date(iso),
      ),
    );
    expect(formatReset(null)).toBeNull();
  });
});
