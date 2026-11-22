import { useState } from 'react';
import { Button } from '../../components/Button';
import { FormError, TagInput, TextAreaField, TextField } from '../../components/forms';
import type { IdeaIn, IdeaOut } from '../../hooks/useIdeas';

/** The API accepts at most this many tags on one idea. */
const IDEA_TAG_MAX_COUNT = 10;

interface IdeaFormProps {
  /** Present when editing; absent for a new idea. */
  idea?: IdeaOut;
  /** Prefill for a new idea (ignored when editing). */
  initial?: { title: string; pitch: string };
  submitLabel: string;
  pending: boolean;
  error: unknown;
  onSubmit: (values: IdeaIn) => void;
  onCancel?: () => void;
}

/** Title, description (the pitch), estimated cost in the idea's currency, and tags. */
export function IdeaForm({
  idea,
  initial,
  submitLabel,
  pending,
  error,
  onSubmit,
  onCancel,
}: IdeaFormProps) {
  const [title, setTitle] = useState(idea?.title ?? initial?.title ?? '');
  const [pitch, setPitch] = useState(idea?.pitch ?? initial?.pitch ?? '');
  const [cost, setCost] = useState(idea?.est_cost != null ? String(idea.est_cost) : '');
  const [tags, setTags] = useState<string[]>(idea?.tags ?? []);
  const costNumber = cost.trim() === '' ? null : Number(cost);
  const costInvalid = costNumber !== null && (!Number.isFinite(costNumber) || costNumber < 0);

  return (
    <form
      className="flex flex-col gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        if (!title.trim() || costInvalid) return;
        onSubmit({ title: title.trim(), pitch: pitch.trim(), est_cost: costNumber, tags });
      }}
    >
      <TextField
        label="Title"
        placeholder="e.g. Sunset picnic with too much cheese"
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        maxLength={200}
        required
      />
      <TextAreaField
        label="Description"
        placeholder="What makes it a good date?"
        value={pitch}
        onChange={(e) => setPitch(e.target.value)}
      />
      <TextField
        label={`Estimated cost (${idea?.currency ?? 'USD'})`}
        hint="For the two of you. Leave it blank if you're not sure."
        type="number"
        inputMode="decimal"
        min={0}
        step="any"
        value={cost}
        onChange={(e) => setCost(e.target.value)}
        aria-invalid={costInvalid}
      />
      <TagInput
        label="Tags"
        value={tags}
        onChange={setTags}
        placeholder="e.g. cozy"
        max={IDEA_TAG_MAX_COUNT}
      />
      <FormError error={error} />
      <div className="flex gap-2">
        <Button type="submit" disabled={pending || !title.trim() || costInvalid}>
          {pending ? 'Saving…' : submitLabel}
        </Button>
        {onCancel ? (
          <Button variant="secondary" onClick={onCancel}>
            Cancel
          </Button>
        ) : null}
      </div>
    </form>
  );
}
