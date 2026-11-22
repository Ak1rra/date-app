import { Link } from 'react-router';
import { ApiError } from '../../api/http';
import { friendlyMessage } from '../../components/forms';
import { formatReset } from '../../lib/resetTime';

/** True when the error is the API rejecting the date window (shown next to the time fields). */
export function isWindowError(error: unknown): boolean {
  return error instanceof ApiError && error.status === 422 && error.code === 'invalid_window';
}

export const WINDOW_ERROR_TEXT =
  "That time window doesn't work. Pick a date and times at most 14 days long.";

/** A friendly, in-voice message for each way planning can fail. */
export function PlanError({ error }: { error: unknown }) {
  if (!error) return null;
  let content = <>{friendlyMessage(error)}</>;
  if (error instanceof ApiError) {
    if (error.status === 429) {
      const reset = formatReset(error.resetAt);
      content = (
        <>
          You two have used up all the planning magic for now.{' '}
          {reset ? `It comes back ${reset}.` : 'Try again a little later.'}
        </>
      );
    } else if (error.status === 503) {
      content = <>The planner is resting right now. Give it a little while and try again.</>;
    } else if (error.status === 502) {
      content = <>That one didn&apos;t come together — try again.</>;
    } else if (error.status === 409 && error.code === 'home_area_required') {
      content = (
        <>
          We need to know where you&apos;re based before we can find places.{' '}
          <Link to="/profile" className="font-semibold underline">
            Set your home area
          </Link>
          , then come back.
        </>
      );
    }
  }
  return (
    <p role="alert" className="rounded-xl border border-coral p-3 text-sm text-ink">
      {content}
    </p>
  );
}
