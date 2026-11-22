import { ApiError } from './http';

/**
 * FormError shows a generic line for a 5xx; the planner's own message ("resting", "couldn't put
 * that together") reads better. Covers 503 (planner unavailable) and 502 `ai_failed`.
 */
export function plannerError(error: unknown) {
  const isPlannerFailure =
    error instanceof ApiError &&
    (error.status === 503 || (error.status === 502 && error.code === 'ai_failed'));
  return isPlannerFailure ? new ApiError(422, error.code, error.message) : error;
}

const RATE_LIMITED = "You've hit the AI limit for now — try again later.";

/**
 * plannerError plus the AI-run extras: a 429 (daily/monthly AI cap) gets its own sentence instead
 * of FormError's generic one.
 */
export function aiError(error: unknown) {
  if (error instanceof ApiError) {
    if (error.status === 429) return new ApiError(422, error.code, RATE_LIMITED);
  }
  return plannerError(error);
}
