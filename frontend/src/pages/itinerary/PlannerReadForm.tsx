import { useState } from 'react';
import { Button } from '../../components/Button';
import { FormError, TextAreaField } from '../../components/forms';
import { usePutPlannerRead, type PlannerReadOut } from '../../hooks/useFeedback';

const TEXT_LIMIT = 1000;

export function PlannerReadForm({
  itineraryId,
  saved,
}: {
  itineraryId: string;
  saved: PlannerReadOut | null;
}) {
  const put = usePutPlannerRead(itineraryId);
  const [editing, setEditing] = useState(saved === null);
  const [enjoyment, setEnjoyment] = useState(saved?.enjoyment ?? 0);
  const [highlights, setHighlights] = useState(saved?.highlights ?? '');
  const [misses, setMisses] = useState(saved?.misses ?? '');

  return (
    <section
      className="rounded-3xl border-2 border-line bg-surface-2 p-4"
      aria-label="How did they like it?"
    >
      <h3 className="text-lg font-semibold">How did they like it?</h3>
      <p className="mt-1 text-sm text-muted">Private to you. Your partner never sees this.</p>
      {saved && !editing ? (
        <div className="mt-3 flex flex-col gap-2">
          <p>
            <span className="font-semibold">Enjoyment:</span> {saved.enjoyment}/5
          </p>
          {saved.highlights ? (
            <p>
              <span className="font-semibold">Highlights:</span> {saved.highlights}
            </p>
          ) : null}
          {saved.misses ? (
            <p>
              <span className="font-semibold">Misses:</span> {saved.misses}
            </p>
          ) : null}
          <div>
            <Button variant="secondary" onClick={() => setEditing(true)}>
              Edit my read
            </Button>
          </div>
        </div>
      ) : (
        <form
          className="mt-3 flex flex-col gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            if (enjoyment < 1 || put.isPending) return;
            put.mutate(
              {
                enjoyment,
                highlights: highlights.trim().slice(0, TEXT_LIMIT) || null,
                misses: misses.trim().slice(0, TEXT_LIMIT) || null,
              },
              { onSuccess: () => setEditing(false) },
            );
          }}
        >
          <fieldset className="flex flex-col gap-2">
            <legend className="text-sm font-semibold">How much did they enjoy it?</legend>
            <div className="flex flex-wrap gap-2">
              {[1, 2, 3, 4, 5].map((n) => (
                <label
                  key={n}
                  className={`inline-flex min-h-11 min-w-11 cursor-pointer items-center justify-center rounded-full border px-3 font-semibold has-[:focus-visible]:ring-2 ${
                    n === enjoyment
                      ? 'border-2 border-accent bg-accent-soft text-accent'
                      : 'border-line bg-surface text-ink'
                  }`}
                >
                  <input
                    type="radio"
                    name="enjoyment"
                    className="sr-only"
                    aria-label={`Enjoyment ${n} of 5`}
                    checked={enjoyment === n}
                    onChange={() => setEnjoyment(n)}
                  />
                  <span aria-hidden="true">{n}</span>
                </label>
              ))}
            </div>
          </fieldset>
          <TextAreaField
            label="What they loved"
            value={highlights}
            maxLength={TEXT_LIMIT}
            onChange={(event) => setHighlights(event.target.value)}
          />
          <TextAreaField
            label="What missed the mark"
            value={misses}
            maxLength={TEXT_LIMIT}
            onChange={(event) => setMisses(event.target.value)}
          />
          <FormError error={put.error} />
          <div className="flex gap-2">
            <Button type="submit" disabled={enjoyment < 1 || put.isPending}>
              {put.isPending ? 'Saving…' : 'Save my read'}
            </Button>
            {saved ? (
              <Button variant="secondary" onClick={() => setEditing(false)}>
                Cancel
              </Button>
            ) : null}
          </div>
        </form>
      )}
    </section>
  );
}
