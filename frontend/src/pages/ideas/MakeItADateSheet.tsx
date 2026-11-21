import { useState } from 'react';
import { useNavigate } from 'react-router';
import { ApiError } from '../../api/http';
import { Button, ButtonLink } from '../../components/Button';
import { FormError, SelectField, TextField } from '../../components/forms';
import { useMakeItADate, type IdeaItineraryIn, type IdeaOut } from '../../hooks/useIdeas';
import { localToIso } from '../itinerary/dateWindow';

/** FormError shows a generic line for a 503; the planner's own message ("resting") reads better. */
export function plannerError(error: unknown) {
  return error instanceof ApiError && error.status === 503
    ? new ApiError(422, error.code, error.message)
    : error;
}

/** "Make it a date" for an active idea: opens the sheet below. */
export function MakeItADateButton({ idea }: { idea: IdeaOut }) {
  const [open, setOpen] = useState(false);
  if (idea.status !== 'active') return null;
  return (
    <>
      <Button
        variant="secondary"
        aria-label={`Make ${idea.title} a date`}
        onClick={() => setOpen(true)}
      >
        Make it a date
      </Button>
      {open ? (
        <MakeItADateSheet ideaId={idea.id} ideaTitle={idea.title} onClose={() => setOpen(false)} />
      ) : null}
    </>
  );
}

export function MakeItADateSheet({
  ideaId,
  ideaTitle,
  onClose,
}: {
  ideaId: string;
  ideaTitle: string;
  onClose: () => void;
}) {
  const navigate = useNavigate();
  const make = useMakeItADate(ideaId);
  const [mode, setMode] = useState<'free' | 'pick'>('free');
  const [starts, setStarts] = useState('');
  const [ends, setEnds] = useState('');
  const [stops, setStops] = useState('');
  const [budget, setBudget] = useState('');
  const endsTooEarly = mode === 'pick' && !!starts && !!ends && new Date(ends) <= new Date(starts);
  const ready = mode === 'free' || (!!starts && !endsTooEarly);

  return (
    <div
      className="fixed inset-0 z-10 flex items-end bg-ink/40 p-3 sm:items-center sm:justify-center"
      role="presentation"
    >
      <section
        className="max-h-full w-full max-w-lg overflow-y-auto rounded-3xl bg-surface p-5 shadow-xl"
        role="dialog"
        aria-modal="true"
        aria-labelledby="make-date-title"
      >
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 id="make-date-title" className="text-xl font-semibold">
              Make it a date
            </h2>
            <p className="mt-1 text-sm text-muted">
              We&apos;ll turn &ldquo;{ideaTitle}&rdquo; into a plan you can tweak.
            </p>
          </div>
          <Button variant="secondary" onClick={onClose} aria-label="Close make it a date">
            Close
          </Button>
        </div>
        <form
          className="mt-5 flex flex-col gap-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (!ready) return;
            const body: IdeaItineraryIn = {};
            if (mode === 'pick') {
              body.starts_at = localToIso(starts);
              if (ends) body.ends_at = localToIso(ends);
            }
            if (stops) body.stop_count = Number(stops);
            if (budget.trim() !== '' && Number(budget) >= 0) body.budget_max = Number(budget);
            make.mutate(body, {
              onSuccess: (itinerary) => navigate(`/itineraries/${itinerary.id}`),
            });
          }}
        >
          <div className="flex flex-wrap gap-2" role="group" aria-label="When">
            <Button
              type="button"
              variant={mode === 'free' ? 'primary' : 'secondary'}
              aria-pressed={mode === 'free'}
              onClick={() => setMode('free')}
            >
              First time we&apos;re both free
            </Button>
            <Button
              type="button"
              variant={mode === 'pick' ? 'primary' : 'secondary'}
              aria-pressed={mode === 'pick'}
              onClick={() => setMode('pick')}
            >
              Pick a time
            </Button>
          </div>
          {mode === 'pick' ? (
            <div className="grid gap-3 sm:grid-cols-2">
              <TextField
                label="Starts"
                type="datetime-local"
                required
                value={starts}
                onChange={(event) => setStarts(event.target.value)}
              />
              <TextField
                label="Ends (optional)"
                type="datetime-local"
                value={ends}
                onChange={(event) => setEnds(event.target.value)}
                hint={endsTooEarly ? 'The end needs to be after the start.' : undefined}
                aria-invalid={endsTooEarly || undefined}
              />
            </div>
          ) : null}
          <ButtonLink
            to={`/calendar?idea=${encodeURIComponent(ideaId)}`}
            variant="secondary"
            onClick={onClose}
          >
            Find a time
          </ButtonLink>
          <div className="grid gap-3 sm:grid-cols-2">
            <SelectField label="Stops" value={stops} onChange={(e) => setStops(e.target.value)}>
              <option value="">Any</option>
              {[1, 2, 3, 4, 5].map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </SelectField>
            <TextField
              label="Budget (optional)"
              type="number"
              min={0}
              step="any"
              inputMode="decimal"
              value={budget}
              onChange={(event) => setBudget(event.target.value)}
            />
          </div>
          <FormError error={plannerError(make.error)} />
          <Button type="submit" disabled={!ready || make.isPending}>
            {make.isPending ? 'Planning…' : 'Make it a date'}
          </Button>
        </form>
      </section>
    </div>
  );
}
