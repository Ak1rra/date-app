import { useState } from 'react';
import { Button, ButtonLink } from '../../components/Button';
import { describeApplyError, useApplyAction, type ChatActionOut } from '../../hooks/useChat';

const slotFormatter = new Intl.DateTimeFormat(undefined, {
  weekday: 'short',
  month: 'short',
  day: 'numeric',
  hour: 'numeric',
  minute: 'numeric',
});

const TYPE_LABEL: Record<ChatActionOut['type'], string> = {
  idea: 'New idea',
  itinerary_change: 'Plan change',
  calendar_slot: 'Time on the calendar',
};

function text(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value : null;
}

/** The type-specific line under the summary (never throws on an unexpected payload). */
function detail(action: ChatActionOut): string | null {
  const { payload } = action;
  if (action.type === 'idea') return text(payload.title);
  if (action.type === 'itinerary_change') return text(payload.description);
  const startsAt = text(payload.starts_at);
  if (!startsAt || Number.isNaN(Date.parse(startsAt))) return null;
  return slotFormatter.format(new Date(startsAt));
}

/** Where the applied suggestion ended up, from the apply response or the stored result. */
function linkFor(
  action: ChatActionOut,
  applied: { idea: { id: string } | null; itinerary: { id: string } | null } | undefined,
): { to: string; label: string } | null {
  const itineraryId = applied?.itinerary?.id ?? text(action.result?.itinerary_id);
  if (itineraryId) return { to: `/itineraries/${itineraryId}`, label: 'View the plan' };
  const ideaId = applied?.idea?.id ?? text(action.result?.idea_id);
  if (ideaId) return { to: '/ideas', label: 'See it in Ideas' };
  return null;
}

/** An assistant suggestion: nothing changes until Apply; Dismiss only hides the card here. */
export function ActionCard({ action }: { action: ChatActionOut }) {
  const apply = useApplyAction();
  const [dismissed, setDismissed] = useState(false);
  if (dismissed) return null;

  const appliedNow = apply.isSuccess ? apply.data : undefined;
  const applied = !!action.applied_at || apply.isSuccess;
  const line = detail(action);
  const link = applied ? linkFor(action, appliedNow) : null;

  return (
    <div className="flex flex-col gap-2 rounded-2xl border border-line bg-surface p-3 text-sm">
      <p className="text-xs font-semibold tracking-wide text-muted uppercase">
        {TYPE_LABEL[action.type]}
      </p>
      <p className="font-semibold">{action.summary}</p>
      {line ? <p className="text-muted">{line}</p> : null}
      {apply.isError ? (
        <p role="alert" className="text-coral">
          {describeApplyError(apply.error)}
        </p>
      ) : null}
      <div className="flex flex-wrap items-center gap-2">
        {applied ? (
          <>
            <span className="font-semibold text-accent">Added ✓</span>
            {link ? (
              <ButtonLink to={link.to} variant="secondary">
                {link.label}
              </ButtonLink>
            ) : null}
          </>
        ) : (
          <>
            <Button onClick={() => apply.mutate(action.id)} disabled={apply.isPending}>
              {apply.isPending ? 'Applying…' : 'Apply'}
            </Button>
            <Button variant="secondary" onClick={() => setDismissed(true)}>
              Dismiss
            </Button>
          </>
        )}
      </div>
    </div>
  );
}
