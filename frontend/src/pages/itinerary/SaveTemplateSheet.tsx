import { useState } from 'react';
import { Button, ButtonLink } from '../../components/Button';
import { FormError, TextField } from '../../components/forms';
import { useSaveTemplate } from '../../hooks/useTemplates';
import { Sheet } from '../../components/Sheet';

const NAME_MAX = 80;

export function SaveTemplateSheet({
  itineraryId,
  title,
  onClose,
}: {
  itineraryId: string;
  title: string;
  onClose: () => void;
}) {
  const save = useSaveTemplate(itineraryId);
  const [name, setName] = useState(title.slice(0, NAME_MAX));
  const trimmed = name.trim();

  return (
    <Sheet
      labelledBy="save-template-title"
      className="w-full max-w-lg rounded-3xl bg-surface p-5 shadow-xl"
      onClose={onClose}
    >
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 id="save-template-title" className="text-xl font-semibold">
            Save as a template
          </h2>
          <p className="mt-1 text-sm text-muted">
            Love this one? Keep its shape and plan it again whenever you like.
          </p>
        </div>
        <Button variant="secondary" onClick={onClose} aria-label="Close save template">
          Close
        </Button>
      </div>
      {save.isSuccess ? (
        <div className="mt-5 flex flex-col gap-3">
          <p role="status" className="rounded-xl border-2 border-accent bg-accent-soft p-3">
            Saved! It&apos;s waiting in your templates.
          </p>
          <div className="flex flex-wrap gap-2">
            <ButtonLink to="/templates" variant="secondary">
              See templates
            </ButtonLink>
            <Button onClick={onClose}>Done</Button>
          </div>
        </div>
      ) : (
        <form
          className="mt-5 flex flex-col gap-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (!trimmed) return;
            save.mutate(trimmed);
          }}
        >
          <TextField
            label="Template name"
            required
            maxLength={NAME_MAX}
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
          <FormError error={save.error} />
          <Button type="submit" disabled={!trimmed || save.isPending}>
            {save.isPending ? 'Saving…' : 'Save template'}
          </Button>
        </form>
      )}
    </Sheet>
  );
}
