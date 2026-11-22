import { describe, expect, it } from 'vitest';
import { ApiError } from './http';
import { aiError } from './plannerError';

describe('aiError', () => {
  it('turns a daily cap into a friendly message with the reset time', () => {
    const reset = new Date(Date.now() + 8 * 3_600_000).toISOString();
    const error = aiError(new ApiError(429, 'ai_cap_reached', 'raw', reset)) as ApiError;
    expect(error.message).toMatch(/^You've used today's AI planning\. It resets .+\.$/);
  });

  it('turns a monthly cap into a "this month" message', () => {
    const reset = new Date(Date.now() + 10 * 24 * 3_600_000).toISOString();
    const error = aiError(new ApiError(429, 'ai_cap_reached', 'raw', reset)) as ApiError;
    expect(error.message).toMatch(/^You've used this month's AI planning\. It resets /);
  });

  it('falls back to the generic limit line without a reset time', () => {
    const error = aiError(new ApiError(429, 'ai_cap_reached', 'raw')) as ApiError;
    expect(error.message).toBe("You've hit the AI limit for now — try again later.");
  });
});
