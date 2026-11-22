import { useState } from 'react';
import { aiError } from '../../api/plannerError';
import { Button } from '../../components/Button';
import { FormError, Segmented, SelectField, TextField } from '../../components/forms';
import { useGenerateIdeas, type GenerateIdeasIn } from '../../hooks/useIdeas';

export type GenerateMode = GenerateIdeasIn['mode'];

const MODES: readonly { value: GenerateMode; label: string }[] = [
  { value: 'match', label: 'For us to swipe' },
  { value: 'plan', label: 'Just for me' },
];

type Place = NonNullable<GenerateIdeasIn['indoor_outdoor']>;
type Category = NonNullable<GenerateIdeasIn['category']>;
const PLACES: readonly { value: Place; label: string }[] = [
  { value: 'either', label: 'Either' },
  { value: 'indoor', label: 'Indoor' },
  { value: 'outdoor', label: 'Outdoor' },
];

const CATEGORIES: readonly { value: Category; label: string }[] = [
  { value: 'food', label: 'Food' },
  { value: 'drink', label: 'Drink' },
  { value: 'activity', label: 'Activity' },
  { value: 'event', label: 'Event' },
  { value: 'outdoors', label: 'Outdoors' },
  { value: 'other', label: 'Other' },
];

const COUNTS = [5, 6, 8, 10] as const;
const VIBE_MAX = 200;
const BUDGET_MAX = 10_000;

/** Ask the AI for a batch of ideas. The result lands in the deck (match) or My ideas (plan). */
export function GenerateIdeasSheet({
  defaultMode,
  onClose,
}: {
  defaultMode: GenerateMode;
  onClose: () => void;
}) {
  const generate = useGenerateIdeas();
  const [mode, setMode] = useState<GenerateMode>(defaultMode);
  const [vibe, setVibe] = useState('');
  const [category, setCategory] = useState('');
  const [place, setPlace] = useState<Place>('either');
  const [budget, setBudget] = useState('');
  const [count, setCount] = useState('8');
  const [surprise, setSurprise] = useState(false);
  const [degraded, setDegraded] = useState(false);
  const budgetInvalid =
    budget.trim() !== '' && !(Number(budget) > 0 && Number(budget) <= BUDGET_MAX);

  return (
    <div
      className="fixed inset-0 z-10 flex items-end bg-ink/40 p-3 sm:items-center sm:justify-center"
      role="presentation"
    >
      <section
        className="max-h-full w-full max-w-lg overflow-y-auto rounded-3xl bg-surface p-5 shadow-xl"
        role="dialog"
        aria-modal="true"
        aria-labelledby="generate-ideas-title"
      >
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 id="generate-ideas-title" className="text-xl font-semibold">
              Generate ideas
            </h2>
            <p className="mt-1 text-sm text-muted">
              Tell us the mood and we&apos;ll dream up a few date ideas.
            </p>
          </div>
          <Button variant="secondary" onClick={onClose} aria-label="Close generate ideas">
            Close
          </Button>
        </div>
        {degraded ? (
          <div className="mt-5 flex flex-col items-start gap-4">
            <p role="status" className="rounded-xl border border-line p-3 text-sm">
              Some live details (venues, weather or events) couldn&apos;t be checked, so a few ideas
              may be rough.
            </p>
            <Button onClick={onClose}>Done</Button>
          </div>
        ) : (
          <form
            className="mt-5 flex flex-col gap-4"
            onSubmit={(event) => {
              event.preventDefault();
              if (budgetInvalid || generate.isPending) return;
              const body: GenerateIdeasIn = {
                mode,
                vibe: vibe.trim() || null,
                category: CATEGORIES.find((c) => c.value === category)?.value ?? null,
                indoor_outdoor: place === 'either' ? null : place,
                budget_max: budget.trim() === '' ? null : Number(budget),
                count: Number(count),
                surprise_me: surprise,
              };
              generate.mutate(body, {
                onSuccess: (result) => {
                  if (result.degraded.length > 0) setDegraded(true);
                  else onClose();
                },
              });
            }}
          >
            <fieldset
              disabled={generate.isPending}
              className="m-0 flex min-w-0 flex-col gap-4 border-0 p-0"
            >
              <Segmented label="Who is it for?" options={MODES} value={mode} onChange={setMode} />
              <TextField
                label="Vibe (optional)"
                maxLength={VIBE_MAX}
                value={vibe}
                onChange={(event) => setVibe(event.target.value)}
                placeholder="Cozy, a little adventurous"
              />
              <div className="grid gap-3 sm:grid-cols-2">
                <SelectField
                  label="Category"
                  value={category}
                  onChange={(event) => setCategory(event.target.value)}
                >
                  <option value="">Any</option>
                  {CATEGORIES.map((c) => (
                    <option key={c.value} value={c.value}>
                      {c.label}
                    </option>
                  ))}
                </SelectField>
                <SelectField
                  label="How many ideas"
                  value={count}
                  onChange={(event) => setCount(event.target.value)}
                >
                  {COUNTS.map((n) => (
                    <option key={n} value={n}>
                      {n}
                    </option>
                  ))}
                </SelectField>
              </div>
              <Segmented label="Where" options={PLACES} value={place} onChange={setPlace} />
              <TextField
                label="Budget max, USD (optional)"
                type="number"
                min={0.01}
                max={BUDGET_MAX}
                step="any"
                inputMode="decimal"
                value={budget}
                onChange={(event) => setBudget(event.target.value)}
                hint={
                  budgetInvalid
                    ? 'Enter more than 0 and up to 10,000, or leave it blank.'
                    : undefined
                }
                aria-invalid={budgetInvalid || undefined}
              />
              <label className="flex min-h-11 items-center gap-3 text-sm font-semibold">
                <input
                  type="checkbox"
                  className="size-5"
                  checked={surprise}
                  onChange={(event) => setSurprise(event.target.checked)}
                />
                Surprise me
              </label>
              <FormError error={aiError(generate.error)} />
              <Button type="submit" disabled={budgetInvalid || generate.isPending}>
                {generate.isPending ? 'Dreaming up ideas…' : 'Generate ideas'}
              </Button>
            </fieldset>
          </form>
        )}
      </section>
    </div>
  );
}
