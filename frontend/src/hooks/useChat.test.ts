import { describe, expect, it } from 'vitest';
import { ApiError } from '../api/http';
import { describeApplyError, describeSendError } from './useChat';

describe('describeSendError', () => {
  it('explains the daily cap for a 429', () => {
    expect(describeSendError(new ApiError(429, 'rate_limited', 'x'))).toBe(
      "You've used today's chat messages. Try again tomorrow.",
    );
  });

  it('passes the planner message through for a 503', () => {
    expect(describeSendError(new ApiError(503, 'ai_unavailable', 'The planner is resting.'))).toBe(
      'The planner is resting.',
    );
  });

  it('falls back to a connection hint for a network failure', () => {
    expect(describeSendError(new TypeError('Failed to fetch'))).toMatch(/connection/i);
  });
});

describe('describeApplyError', () => {
  it('names the planner rule for a 403', () => {
    expect(describeApplyError(new ApiError(403, 'forbidden', 'x'))).toBe(
      'Only the planner can change this date.',
    );
  });

  it('keeps the server message for a conflict', () => {
    expect(describeApplyError(new ApiError(409, 'nothing_to_apply', 'Nothing to apply.'))).toBe(
      'Nothing to apply.',
    );
  });
});
