import { useEffect, useState } from 'react';
import { Button } from '../../components/Button';
import { FormError, Segmented, SelectField, TextField } from '../../components/forms';
import type { HintIn } from '../../hooks/useSurprise';
import { localToIso } from './dateWindow';

export type HintKind = HintIn['kind'];

export const HINT_MAX = 20;
export const HINT_TEXT_MAX = 300;
export const PAST_TIME_MESSAGE = 'That time has already passed — pick another.';

export const hintKinds: readonly { value: HintKind; label: string }[] = [
  { value: 'dress_code', label: 'Dress code' },
  { value: 'bring', label: 'What to bring' },
  { value: 'ready_by', label: 'Be ready by' },
  { value: 'eat', label: 'Eating' },
  { value: 'energy', label: 'Energy level' },
  { value: 'indoor_outdoor', label: 'Indoors or out' },
  { value: 'weather', label: 'Weather gear' },
  { value: 'travel', label: 'Travel' },
  { value: 'custom', label: 'Something else' },
];

export const kindLabel = (kind: HintKind) =>
  hintKinds.find((option) => option.value === kind)?.label ?? kind;

/** An ISO time as a <input type="datetime-local"> value (browser-local wall time). */
export function isoToLocal(value: string | null | undefined) {
  if (!value) return '';
  const date = new Date(value);
  const offset = date.getTimezoneOffset() * 60000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}

const HOUR = 3_600_000;
const relativeChoices = [
  { value: 'h2', label: '2 hours before', hours: 2 },
  { value: 'h6', label: '6 hours before', hours: 6 },
  { value: 'h12', label: '12 hours before', hours: 12 },
  { value: 'd1', label: '1 day before', days: 1 },
  { value: 'd2', label: '2 days before', days: 2 },
  { value: 'd3', label: '3 days before', days: 3 },
  { value: 'd7', label: '7 days before', days: 7 },
] as const;

type RelativeChoice = (typeof relativeChoices)[number];
type ReleaseChoice = 'now' | 'pick' | RelativeChoice['value'];

/** Hours are exact elapsed time; days keep the local wall-clock time across a DST change. */
function relativeTime(option: RelativeChoice, startsAt: string) {
  const date = new Date(startsAt);
  if ('days' in option) {
    date.setDate(date.getDate() - option.days);
    return date.getTime();
  }
  return date.getTime() - option.hours * HOUR;
}

function releaseAt(choice: ReleaseChoice, pick: string, startsAt: string | null) {
  if (choice === 'now') return null;
  if (choice === 'pick') return localToIso(pick);
  const found = relativeChoices.find((option) => option.value === choice);
  if (!found || !startsAt) return null;
  return new Date(relativeTime(found, startsAt)).toISOString();
}

/** The current time, refreshed every `ms` so time-sensitive options stay honest. */
function useNow(ms: number) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(timer);
  }, [ms]);
  return now;
}

export function HintForm({
  initial,
  prefill,
  startsAt,
  submitLabel,
  pending,
  error,
  onSubmit,
  onCancel,
}: {
  initial?: { kind: HintKind; text: string; release_at: string };
  /** Seeds the kind and text only (a suggestion); the release choice stays "Now". */
  prefill?: { kind: HintKind; text: string };
  startsAt: string | null;
  submitLabel: string;
  pending: boolean;
  error: unknown;
  onSubmit: (body: HintIn) => void;
  onCancel?: () => void;
}) {
  const [kind, setKind] = useState<HintKind>(initial?.kind ?? prefill?.kind ?? 'dress_code');
  const [text, setText] = useState(initial?.text ?? prefill?.text ?? '');
  const [choice, setChoice] = useState<ReleaseChoice>(initial ? 'pick' : 'now');
  const [pick, setPick] = useState(isoToLocal(initial?.release_at));
  const [pastError, setPastError] = useState(false);
  const now = useNow(60_000);
  const trimmed = text.trim();
  const valid =
    trimmed.length >= 1 && trimmed.length <= HINT_TEXT_MAX && (choice !== 'pick' || !!pick);

  return (
    <form
      className="flex flex-col gap-3"
      // `min` is only a picker hint; the past-time check below shows our own message.
      noValidate
      aria-label={initial ? 'Edit hint' : 'Add a hint'}
      onSubmit={(event) => {
        event.preventDefault();
        if (!valid) return;
        const releaseTime = releaseAt(choice, pick, startsAt);
        // Recompute at submit time: the form may have sat open past the release time.
        if (releaseTime && Date.parse(releaseTime) <= Date.now()) {
          setPastError(true);
          return;
        }
        setPastError(false);
        onSubmit({ kind, text: trimmed, release_at: releaseTime });
      }}
    >
      <fieldset disabled={pending} className="m-0 flex min-w-0 flex-col gap-3 border-0 p-0">
        <Segmented label="What kind of hint?" options={hintKinds} value={kind} onChange={setKind} />
        <TextField
          label="Hint"
          hint="A little clue, nothing that gives the venue away."
          value={text}
          maxLength={HINT_TEXT_MAX}
          onChange={(event) => setText(event.target.value)}
          placeholder="Wear something you can dance in"
        />
        <SelectField
          label="When to release"
          value={choice}
          onChange={(event) => {
            setChoice(event.target.value as ReleaseChoice);
            setPastError(false);
          }}
        >
          <option value="now">Now</option>
          {relativeChoices.map((option) => (
            <option
              key={option.value}
              value={option.value}
              disabled={!startsAt || relativeTime(option, startsAt) <= now}
            >
              {option.label}
            </option>
          ))}
          <option value="pick">Pick a time</option>
        </SelectField>
        {choice === 'pick' ? (
          <TextField
            label="Release time"
            type="datetime-local"
            value={pick}
            min={isoToLocal(new Date(now).toISOString())}
            onChange={(event) => {
              setPick(event.target.value);
              setPastError(false);
            }}
          />
        ) : null}
        {pastError ? (
          <p role="alert" className="rounded-xl border border-coral p-3 text-sm text-ink">
            {PAST_TIME_MESSAGE}
          </p>
        ) : null}
        <FormError error={error} />
        <div className="flex flex-wrap gap-2">
          <Button type="submit" disabled={!valid || pending}>
            {submitLabel}
          </Button>
          {onCancel ? (
            <Button type="button" variant="secondary" disabled={pending} onClick={onCancel}>
              Cancel
            </Button>
          ) : null}
        </div>
      </fieldset>
    </form>
  );
}
