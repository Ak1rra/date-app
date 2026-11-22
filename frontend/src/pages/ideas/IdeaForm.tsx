import { useState } from 'react';
import { Button } from '../../components/Button';
import { FormError, TagInput, TextAreaField, TextField } from '../../components/forms';
import type { IdeaIn, IdeaOut } from '../../hooks/useIdeas';
import { safeHttpUrl } from '../../lib/url';

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
  const initialVenueName = idea?.venue?.name ?? '';
  const initialLink = idea?.venue?.url ?? '';
  const [venueName, setVenueName] = useState(initialVenueName);
  const [link, setLink] = useState(initialLink);
  const venueNameChanged = venueName.trim() !== initialVenueName.trim();
  const linkChanged = link.trim() !== initialLink.trim();
  // A blank link is fine; an untouched stored one is never re-checked or re-sent.
  const linkInvalid = linkChanged && link.trim() !== '' && safeHttpUrl(link) === null;
  const costNumber = cost.trim() === '' ? null : Number(cost);
  const costInvalid = costNumber !== null && (!Number.isFinite(costNumber) || costNumber < 0);

  return (
    <form
      className="flex flex-col gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        if (!title.trim() || costInvalid || linkInvalid) return;
        // Editing sends only the fields that were changed, null when emptied so they clear;
        // a new idea omits empty ones.
        const optional = (value: string, changed: boolean) =>
          idea && !changed ? undefined : value.trim() || (idea ? null : undefined);
        onSubmit({
          title: title.trim(),
          pitch: pitch.trim(),
          est_cost: costNumber,
          tags,
          venue_name: optional(venueName, venueNameChanged),
          link: optional(link, linkChanged),
        });
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
        label="Place (optional)"
        placeholder="e.g. Riverside Park"
        value={venueName}
        onChange={(e) => setVenueName(e.target.value)}
        maxLength={120}
      />
      <TextField
        label="Link (optional)"
        type="url"
        inputMode="url"
        value={link}
        onChange={(e) => setLink(e.target.value)}
        maxLength={500}
        aria-invalid={linkInvalid}
        error={linkInvalid ? 'Use a full web address starting with http:// or https://' : undefined}
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
        <Button type="submit" disabled={pending || !title.trim() || costInvalid || linkInvalid}>
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
