import { useId, useState, type ComponentProps, type ReactNode } from 'react';
import { ApiError } from '../api/http';
import { Button } from './Button';

export const inputClass =
  'min-h-11 w-full rounded-xl border border-line bg-surface px-3 py-2 text-ink placeholder:text-muted disabled:opacity-60';

/** A friendly sentence for any thrown error (never a stack trace or a raw status). */
export function friendlyMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 403) return "You can't change this one.";
    if (error.status >= 500) return "Something went wrong on our side. That wasn't your fault.";
    return error.message;
  }
  return "We couldn't reach the server. Check your connection and try again.";
}

export function FormError({ error }: { error: unknown }) {
  if (!error) return null;
  return (
    <p role="alert" className="rounded-xl border border-coral p-3 text-sm text-ink">
      <strong className="font-semibold">Couldn&apos;t save. </strong>
      {friendlyMessage(error)}
    </p>
  );
}

export function Skeleton({ label = 'Loading…' }: { label?: string }) {
  return (
    <div role="status" aria-label={label} className="flex animate-pulse flex-col gap-3 p-2">
      <div className="h-4 w-1/2 rounded bg-surface-2" />
      <div className="h-4 w-full rounded bg-surface-2" />
      <div className="h-4 w-3/4 rounded bg-surface-2" />
    </div>
  );
}

export function LoadError({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  return (
    <div role="alert" className="flex flex-col items-start gap-3 p-2">
      <p>{friendlyMessage(error)}</p>
      <Button variant="secondary" onClick={onRetry}>
        Try again
      </Button>
    </div>
  );
}

interface FieldProps extends Omit<ComponentProps<'input'>, 'id'> {
  label: string;
  hint?: string;
}

export function TextField({ label, hint, className, ...props }: FieldProps) {
  const id = useId();
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-sm font-semibold">
        {label}
      </label>
      <input
        id={id}
        aria-describedby={hint ? `${id}-hint` : undefined}
        className={`${inputClass} ${className ?? ''}`}
        {...props}
      />
      {hint ? (
        <p id={`${id}-hint`} className="text-sm text-muted">
          {hint}
        </p>
      ) : null}
    </div>
  );
}

export function TextAreaField({
  label,
  hint,
  ...props
}: { label: string; hint?: string } & Omit<ComponentProps<'textarea'>, 'id'>) {
  const id = useId();
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-sm font-semibold">
        {label}
      </label>
      <textarea
        id={id}
        rows={3}
        aria-describedby={hint ? `${id}-hint` : undefined}
        className={inputClass}
        {...props}
      />
      {hint ? (
        <p id={`${id}-hint`} className="text-sm text-muted">
          {hint}
        </p>
      ) : null}
    </div>
  );
}

export function SelectField({
  label,
  children,
  ...props
}: { label: string; children: ReactNode } & Omit<ComponentProps<'select'>, 'id'>) {
  const id = useId();
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-sm font-semibold">
        {label}
      </label>
      <select id={id} className={inputClass} {...props}>
        {children}
      </select>
    </div>
  );
}

const chipBase =
  'inline-flex min-h-11 items-center gap-1 rounded-full border px-4 py-2 text-sm font-semibold motion-safe:transition';

function chipClass(on: boolean) {
  return `${chipBase} ${
    on
      ? 'border-2 border-accent bg-accent-soft text-accent'
      : 'border-line bg-surface text-ink hover:bg-surface-2'
  }`;
}

/** A selected chip also shows a check mark, so state never relies on colour alone. */
function ChipButton({
  on,
  onClick,
  children,
}: {
  on: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button type="button" aria-pressed={on} onClick={onClick} className={chipClass(on)}>
      {on ? <span aria-hidden="true">✓</span> : null}
      {children}
    </button>
  );
}

export function ChipGroup<T extends string>({
  label,
  options,
  value,
  onChange,
  describe,
}: {
  label: string;
  options: readonly T[];
  value: readonly string[];
  onChange: (next: T[]) => void;
  describe?: (option: T) => string;
}) {
  const id = useId();
  return (
    <div role="group" aria-labelledby={id} className="flex flex-col gap-2">
      <span id={id} className="text-sm font-semibold">
        {label}
      </span>
      <div className="flex flex-wrap gap-2">
        {options.map((option) => {
          const on = value.includes(option);
          return (
            <ChipButton
              key={option}
              on={on}
              onClick={() =>
                onChange(options.filter((o) => (o === option ? !on : value.includes(o))))
              }
            >
              {describe ? describe(option) : option}
            </ChipButton>
          );
        })}
      </div>
    </div>
  );
}

/** One of a few exclusive choices (buttons with aria-pressed, like the chips). */
export function Segmented<T extends string>({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: readonly { value: T; label: string }[];
  value: T | null | undefined;
  onChange: (next: T) => void;
}) {
  const id = useId();
  return (
    <div role="group" aria-labelledby={id} className="flex flex-col gap-2">
      <span id={id} className="text-sm font-semibold">
        {label}
      </span>
      <div className="flex flex-wrap gap-2">
        {options.map((o) => (
          <ChipButton key={o.value} on={value === o.value} onClick={() => onChange(o.value)}>
            {o.label}
          </ChipButton>
        ))}
      </div>
    </div>
  );
}

/** The API caps free-text tags at this many characters each, and this many per list. */
export const TAG_MAX_LENGTH = 40;
export const TAG_MAX_COUNT = 30;

/** Free-text tags: Enter or Add commits, each tag has its own remove button. */
export function TagInput({
  label,
  value,
  onChange,
  placeholder,
  max = TAG_MAX_COUNT,
}: {
  label: string;
  value: string[];
  onChange: (next: string[]) => void;
  placeholder?: string;
  /** How many tags the list may hold (the API's cap for this field). */
  max?: number;
}) {
  const id = useId();
  const [draft, setDraft] = useState('');
  const commit = () => {
    const tag = draft.trim();
    if (value.length >= max) return;
    if (tag && !value.some((v) => v.toLowerCase() === tag.toLowerCase())) onChange([...value, tag]);
    setDraft('');
  };
  return (
    <div className="flex flex-col gap-2">
      <label htmlFor={id} className="text-sm font-semibold">
        {label}
      </label>
      <div className="flex gap-2">
        <input
          id={id}
          value={draft}
          placeholder={placeholder}
          maxLength={TAG_MAX_LENGTH}
          disabled={value.length >= max}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault();
              commit();
            }
          }}
          className={inputClass}
        />
        <Button
          variant="secondary"
          onClick={commit}
          disabled={value.length >= max}
          aria-label={`Add to ${label}`}
        >
          Add
        </Button>
      </div>
      {value.length > 0 ? (
        <ul className="flex flex-wrap gap-2">
          {value.map((tag) => (
            <li key={tag}>
              <button
                type="button"
                aria-label={`Remove ${tag} from ${label}`}
                onClick={() => onChange(value.filter((v) => v !== tag))}
                className={chipClass(true)}
              >
                {tag} <span aria-hidden="true">×</span>
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

/** A 1-5 slider with words at both ends; `null` reads "Not set yet" until it is moved. */
export function SliderField({
  label,
  low,
  high,
  value,
  onChange,
}: {
  label: string;
  low: string;
  high: string;
  value: number | null | undefined;
  onChange: (next: number) => void;
}) {
  const id = useId();
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-sm font-semibold">
        {label}
      </label>
      <input
        id={id}
        type="range"
        min={1}
        max={5}
        step={1}
        value={value ?? 3}
        aria-valuetext={value ? `${value} of 5, from ${low} to ${high}` : 'Not set yet'}
        onChange={(e) => onChange(Number(e.target.value))}
        className="min-h-11 w-full accent-[var(--accent)]"
      />
      <div className="flex justify-between text-sm text-muted">
        <span>{low}</span>
        <span>{value ? `${value} of 5` : 'Not set yet'}</span>
        <span>{high}</span>
      </div>
    </div>
  );
}

/** The "only you can see this" marker (UX-7): words plus a lock, never colour alone. */
export function PrivateBadge() {
  return (
    <span className="inline-flex items-center gap-1 rounded-full border-2 border-accent bg-accent-soft px-3 py-1 text-sm font-semibold text-accent">
      <span aria-hidden="true">🔒</span>
      Only you can see this
    </span>
  );
}
