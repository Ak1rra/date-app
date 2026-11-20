import { useState } from 'react';
import { Button } from '../../components/Button';
import { Card } from '../../components/EmptyState';
import {
  ChipGroup,
  FormError,
  LoadError,
  Segmented,
  Skeleton,
  TextAreaField,
  TextField,
} from '../../components/forms';
import {
  useConstraints,
  useSaveConstraints,
  type ConstraintsIn,
  type ConstraintsOut,
} from '../../hooks/useConstraints';
import { ALCOHOL, TRANSPORT_MODES, pretty } from './options';

/** Empty input -> null, otherwise a number. */
const num = (v: string): number | null => (v.trim() === '' ? null : Number(v));
const TRAVEL_MAX = 1440;
const RADIUS_MAX = 1000;

/** The API takes whole numbers from 1 up to a cap; empty (no limit) is always fine. */
const outOfRange = (v: number | null | undefined, max: number) =>
  v !== null && v !== undefined && (!Number.isInteger(v) || v < 1 || v > max);
const show = (v: number | null | undefined) => (v === null || v === undefined ? '' : String(v));

function ConstraintsForm({ initial }: { initial: ConstraintsOut }) {
  const [form, setForm] = useState<ConstraintsIn>({
    alcohol: initial.alcohol as ConstraintsIn['alcohol'],
    budget_min: initial.budget_min,
    budget_max: initial.budget_max,
    currency: initial.currency,
    kids_pets_notes: initial.kids_pets_notes,
    max_radius_km: initial.max_radius_km,
    max_travel_minutes: initial.max_travel_minutes,
    transport_modes: initial.transport_modes as ConstraintsIn['transport_modes'],
  });
  const save = useSaveConstraints();
  const [limitError, setLimitError] = useState<string | null>(null);
  const set = <K extends keyof ConstraintsIn>(key: K, value: ConstraintsIn[K]) =>
    setForm((f) => ({ ...f, [key]: value }));

  return (
    <form
      noValidate
      className="flex flex-col gap-5 pt-2"
      onSubmit={(e) => {
        e.preventDefault();
        if (outOfRange(form.max_travel_minutes, TRAVEL_MAX)) {
          setLimitError(`Longest trip must be a whole number from 1 to ${TRAVEL_MAX} minutes.`);
          return;
        }
        if (outOfRange(form.max_radius_km, RADIUS_MAX)) {
          setLimitError(`Furthest from home must be a whole number from 1 to ${RADIUS_MAX} km.`);
          return;
        }
        setLimitError(null);
        save.mutate(form);
      }}
    >
      <p className="px-2 text-muted">
        These apply to both of you, so every suggestion stays inside them.
      </p>
      <fieldset className="grid grid-cols-3 gap-2">
        <legend className="mb-1 text-sm font-semibold">Budget per date</legend>
        <TextField
          label="Minimum"
          type="number"
          min={0}
          inputMode="decimal"
          value={show(form.budget_min)}
          onChange={(e) => set('budget_min', num(e.target.value))}
        />
        <TextField
          label="Maximum"
          type="number"
          min={0}
          inputMode="decimal"
          value={show(form.budget_max)}
          onChange={(e) => set('budget_max', num(e.target.value))}
        />
        <TextField
          label="Currency"
          maxLength={3}
          value={form.currency}
          onChange={(e) => set('currency', e.target.value.toUpperCase())}
        />
      </fieldset>
      <div className="grid grid-cols-2 gap-2">
        <TextField
          label="Longest trip (minutes)"
          type="number"
          min={1}
          max={TRAVEL_MAX}
          step={1}
          inputMode="numeric"
          value={show(form.max_travel_minutes)}
          onChange={(e) => set('max_travel_minutes', num(e.target.value))}
        />
        <TextField
          label="Furthest from home (km)"
          type="number"
          min={1}
          max={RADIUS_MAX}
          step={1}
          inputMode="numeric"
          value={show(form.max_radius_km)}
          onChange={(e) => set('max_radius_km', num(e.target.value))}
        />
      </div>
      <ChipGroup
        label="How you get around"
        options={TRANSPORT_MODES}
        value={form.transport_modes}
        onChange={(v) => set('transport_modes', v)}
        describe={pretty}
      />
      <Segmented
        label="Alcohol"
        options={ALCOHOL}
        value={form.alcohol}
        onChange={(v) => set('alcohol', v)}
      />
      <TextAreaField
        label="Kids and pets"
        hint="Anything a sitter, a dog or a baby changes."
        value={form.kids_pets_notes ?? ''}
        onChange={(e) => set('kids_pets_notes', e.target.value)}
      />
      {limitError ? (
        <p role="alert" className="rounded-xl border border-coral p-3 text-sm text-ink">
          {limitError}
        </p>
      ) : null}
      <FormError error={save.error} />
      <div className="flex items-center gap-3">
        <Button type="submit" disabled={save.isPending}>
          {save.isPending ? 'Saving…' : 'Save shared limits'}
        </Button>
        {save.isSuccess ? (
          <span role="status" className="text-sm font-semibold">
            ✓ Saved
          </span>
        ) : null}
      </div>
    </form>
  );
}

export function ConstraintsSection() {
  const constraints = useConstraints();
  return (
    <Card title="Shared limits">
      {constraints.isPending ? <Skeleton label="Loading shared limits" /> : null}
      {constraints.isError ? (
        <LoadError error={constraints.error} onRetry={() => void constraints.refetch()} />
      ) : null}
      {constraints.data ? <ConstraintsForm initial={constraints.data} /> : null}
    </Card>
  );
}
