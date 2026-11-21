import { useState } from 'react';
import { Button } from '../../components/Button';
import { Segmented, TextAreaField, TextField } from '../../components/forms';
import type { PlanSessionIn } from '../../hooks/usePlan';
import { DEFAULT_PRESET, WINDOW_PRESETS, buildWindow, todayLocal } from './window';

/** Mirrors the API's limits on these free-text fields. */
const STEER_MAX_LENGTH = 500;
const MUST_INCLUDE_MAX_LENGTH = 200;

export type PlanAction = 'options' | 'plan-all';

interface PlanFormProps {
  /** Disables the fields and both buttons while a request is running. */
  disabled: boolean;
  onSubmit: (action: PlanAction, body: PlanSessionIn) => void;
  /** A server-side window problem (422), shown next to the time fields. */
  windowError?: string | null;
}

/** When, how much, and what kind of night; then either three options or the whole plan. */
export function PlanForm({ disabled, onSubmit, windowError }: PlanFormProps) {
  const [date, setDate] = useState(() => todayLocal());
  const [start, setStart] = useState(DEFAULT_PRESET.start);
  const [end, setEnd] = useState(DEFAULT_PRESET.end);
  const [budget, setBudget] = useState('');
  const [steer, setSteer] = useState('');
  const [mustInclude, setMustInclude] = useState('');
  const [surprise, setSurprise] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);

  const budgetNumber = budget.trim() === '' ? null : Number(budget);
  const budgetInvalid =
    budgetNumber !== null && (!Number.isFinite(budgetNumber) || budgetNumber < 0);
  const preset = WINDOW_PRESETS.find((p) => p.start === start && p.end === end);
  const shownWindowError = localError ?? windowError ?? null;

  function submit(action: PlanAction) {
    const window = buildWindow(date, start, end);
    if (!window) {
      setLocalError('Pick a date, and an end time that is after the start time.');
      return;
    }
    if (budgetInvalid) return;
    setLocalError(null);
    onSubmit(action, {
      ...window,
      budget_max: budgetNumber,
      steer: steer.trim() || null,
      must_include: mustInclude.trim() || null,
      surprise,
    });
  }

  return (
    <form
      aria-label="Plan a date"
      className="flex flex-col gap-4"
      onSubmit={(e) => {
        e.preventDefault();
        submit('options');
      }}
    >
      <fieldset disabled={disabled} className="flex min-w-0 flex-col gap-4">
        <TextField
          label="Date"
          type="date"
          value={date}
          min={todayLocal()}
          onChange={(e) => setDate(e.target.value)}
          required
        />
        <Segmented
          label="When"
          options={WINDOW_PRESETS.map((p) => ({ value: p.id, label: p.label }))}
          value={preset?.id}
          onChange={(id) => {
            const chosen = WINDOW_PRESETS.find((p) => p.id === id);
            if (!chosen) return;
            setStart(chosen.start);
            setEnd(chosen.end);
            setLocalError(null);
          }}
        />
        <div className="grid grid-cols-2 gap-3">
          <TextField
            label="Start time"
            type="time"
            value={start}
            onChange={(e) => setStart(e.target.value)}
            required
          />
          <TextField
            label="End time"
            type="time"
            value={end}
            onChange={(e) => setEnd(e.target.value)}
            required
          />
        </div>
        {shownWindowError ? (
          <p role="alert" className="rounded-xl border border-coral p-3 text-sm text-ink">
            {shownWindowError}
          </p>
        ) : null}
        <TextField
          label="Budget (USD)"
          hint="For the two of you. Leave it blank if there's no limit."
          type="number"
          inputMode="decimal"
          min={0}
          step="any"
          value={budget}
          onChange={(e) => setBudget(e.target.value)}
          aria-invalid={budgetInvalid}
        />
        <TextAreaField
          label="What are you in the mood for?"
          placeholder="Cozy, a little flirty, nothing too loud…"
          maxLength={STEER_MAX_LENGTH}
          value={steer}
          onChange={(e) => setSteer(e.target.value)}
        />
        <TextField
          label="Must include"
          placeholder="e.g. dessert somewhere with a view"
          maxLength={MUST_INCLUDE_MAX_LENGTH}
          value={mustInclude}
          onChange={(e) => setMustInclude(e.target.value)}
        />
        <div className="flex flex-col gap-2">
          <Button
            variant={surprise ? 'primary' : 'secondary'}
            aria-pressed={surprise}
            onClick={() => setSurprise((on) => !on)}
          >
            Make it a surprise
          </Button>
          {surprise ? (
            <p className="inline-flex items-center gap-2 rounded-xl border-2 border-accent bg-accent-soft p-3 text-sm font-semibold">
              <span aria-hidden="true">🔒</span>
              Surprise — only you can see this
            </p>
          ) : null}
        </div>
      </fieldset>
      <div className="flex flex-col gap-3 md:flex-row">
        <Button type="submit" size="xl" disabled={disabled}>
          Show me 3 options
        </Button>
        <Button
          variant="secondary"
          size="xl"
          disabled={disabled}
          onClick={() => submit('plan-all')}
        >
          Plan the whole thing
        </Button>
      </div>
    </form>
  );
}
