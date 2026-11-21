import { useState } from 'react';
import { Button } from '../../components/Button';
import { TextAreaField } from '../../components/forms';
import type { PlanBuildIn, PlanOptionOut } from '../../hooks/usePlan';
import { OptionCard } from './OptionCard';

/** The API caps the note about how to combine options at this many characters. */
const COMBINE_NOTE_MAX_LENGTH = 300;
const MAX_PICKS = 3;

interface OptionsStepProps {
  options: PlanOptionOut[];
  /** Notice shown when some data sources were down (INT-3); it never blocks anything. */
  degraded: string[];
  disabled: boolean;
  onBuild: (body: PlanBuildIn) => void;
  onMore: () => void;
  onStartOver: () => void;
}

/** Pick one option, or up to three with a note on how to combine them, then build the date. */
export function OptionsStep({
  options,
  degraded,
  disabled,
  onBuild,
  onMore,
  onStartOver,
}: OptionsStepProps) {
  const [picked, setPicked] = useState<string[]>([]);
  const [note, setNote] = useState('');

  const toggle = (id: string) =>
    setPicked((current) =>
      current.includes(id)
        ? current.filter((p) => p !== id)
        : current.length < MAX_PICKS
          ? [...current, id]
          : current,
    );

  return (
    <div className="flex flex-col gap-4">
      {degraded.length > 0 ? (
        <p role="status" className="rounded-xl border border-line bg-surface-2 p-3 text-sm">
          Some sources were unavailable, so options may be thinner.
        </p>
      ) : null}
      <p className="text-muted">
        Pick the one that makes you smile, or up to three to blend into a single date.
      </p>
      <ul aria-label="Date options" className="flex flex-col gap-3">
        {options.map((option) => (
          <OptionCard
            key={option.id}
            option={option}
            selected={picked.includes(option.id)}
            locked={picked.length >= MAX_PICKS}
            onToggle={() => toggle(option.id)}
          />
        ))}
      </ul>
      {picked.length > 1 ? (
        <TextAreaField
          label="How should we combine them? (optional)"
          placeholder="Dinner from the first, the walk from the second…"
          maxLength={COMBINE_NOTE_MAX_LENGTH}
          value={note}
          onChange={(e) => setNote(e.target.value)}
        />
      ) : null}
      <div className="flex flex-col gap-3 md:flex-row">
        <Button
          size="xl"
          disabled={disabled || picked.length === 0}
          onClick={() =>
            onBuild({
              option_ids: picked,
              combine_note: picked.length > 1 && note.trim() ? note.trim() : null,
            })
          }
        >
          Build this date
        </Button>
        <Button variant="secondary" size="xl" disabled={disabled} onClick={onMore}>
          More options
        </Button>
        <Button variant="secondary" disabled={disabled} onClick={onStartOver}>
          Start over
        </Button>
      </div>
    </div>
  );
}
