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
