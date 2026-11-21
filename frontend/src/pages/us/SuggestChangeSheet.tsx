import { useEffect, useRef, useState } from 'react';
import { ApiError } from '../../api/http';
import { Button } from '../../components/Button';
import { FormError, TextAreaField } from '../../components/forms';
import {
  toProfileIn,
  useCreateSuggestion,
  useSuggestions,
  type ProfileChanges,
  type ProfileIn,
  type ProfileOut,
} from '../../hooks/useProfiles';
import { ProfileFields } from './ProfileFields';
import { describeChanges } from './SuggestionsReview';

const NOTE_MAX = 500;

/** Empty text and null mean the same thing ("not set"), so clearing an unset field is no change. */
const normalise = (value: unknown) => (value === '' ? null : value);

/** Only the fields the user actually changed from the profile as it is now. */
export function changedFields(before: ProfileIn, after: ProfileIn): ProfileChanges {
  const changes: Record<string, unknown> = {};
  for (const key of Object.keys(before) as (keyof ProfileIn)[]) {
    if (JSON.stringify(normalise(before[key])) !== JSON.stringify(normalise(after[key]))) {
      changes[key] = normalise(after[key]);
    }
  }
  return changes as ProfileChanges;
}

export function SuggestChangeSheet({
  profile,
  name,
  onClose,
  onEditDirectly,
}: {
  profile: ProfileOut;
  name: string;
  onClose: () => void;
  onEditDirectly: () => void;
}) {
  const [before] = useState<ProfileIn>(() => toProfileIn(profile));
  const [form, setForm] = useState<ProfileIn>(before);
  const [note, setNote] = useState('');
  const create = useCreateSuggestion(profile.id);
  const dialog = useRef<HTMLElement>(null);
  const set = <K extends keyof ProfileIn>(key: K, value: ProfileIn[K]) =>
    setForm((f) => ({ ...f, [key]: value }));
  const changes = changedFields(before, form);
  const nothingChanged = Object.keys(changes).length === 0;

  useEffect(() => {
    dialog.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-10 flex items-end overflow-y-auto bg-ink/40 p-3 sm:items-center sm:justify-center"
      role="presentation"
    >
      <section
        ref={dialog}
        tabIndex={-1}
        className="max-h-full w-full max-w-lg overflow-y-auto rounded-3xl bg-surface p-5 shadow-xl"
        role="dialog"
        aria-modal="true"
        aria-labelledby="suggest-title"
      >
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 id="suggest-title" className="text-xl font-semibold">
              Suggest a change for {name}
            </h2>
            <p className="mt-1 text-sm text-muted">
              Change what you think is off. {name} decides whether to accept it.
            </p>
          </div>
          <Button variant="secondary" onClick={onClose} aria-label="Close suggestion">
            Close
          </Button>
        </div>
        <form
          className="mt-5 flex flex-col gap-5"
          onSubmit={(e) => {
            e.preventDefault();
            if (nothingChanged) return;
            create.mutate(
              { changes, note: note.trim() || null },
              {
                onSuccess: onClose,
                onError: (error) => {
                  if (error instanceof ApiError && error.code === 'edit_directly') onEditDirectly();
                },
              },
            );
          }}
        >
          <ProfileFields form={form} set={set} />
          <TextAreaField
            label="Note (optional)"
            hint={`${note.length} of ${NOTE_MAX}`}
            maxLength={NOTE_MAX}
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
          <FormError error={create.error} />
          <Button type="submit" disabled={nothingChanged || create.isPending}>
            {create.isPending ? 'Sending…' : 'Send suggestion'}
          </Button>
        </form>
      </section>
    </div>
  );
}

/** The other member's profile: a "Suggest a change" button, plus my suggestions still waiting. */
export function SuggestChange({ profile, name }: { profile: ProfileOut; name: string }) {
  const [open, setOpen] = useState(false);
  const [direct, setDirect] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const eligible = !profile.is_me && profile.subject_joined;
  const suggestions = useSuggestions(profile.id, eligible && !profile.can_edit);
  if (!eligible) return null;
  if (profile.can_edit) {
    return direct ? (
      <p role="status" className="rounded-xl border border-line bg-surface p-3 text-sm">
        You can edit this directly now.
      </p>
    ) : null;
  }
  const waiting = suggestions.data?.filter((s) => s.mine && s.status === 'pending') ?? [];
  const close = () => {
    setOpen(false);
    trigger.current?.focus();
  };
  return (
    <div className="flex flex-col gap-3">
      <div>
        <Button ref={trigger} onClick={() => setOpen(true)}>
          Suggest a change
        </Button>
      </div>
      {waiting.length ? (
        <ul aria-label="Your suggestions" className="flex flex-col gap-2 text-sm">
          {waiting.map((s) => (
            <li key={s.id} className="rounded-xl border border-line bg-surface p-3">
              <p className="font-semibold">Waiting on {name}</p>
              {describeChanges(profile, s).map((c) => (
                <p key={c.key}>{c.text}</p>
              ))}
            </li>
          ))}
        </ul>
      ) : null}
      {open ? (
        <SuggestChangeSheet
          profile={profile}
          name={name}
          onClose={close}
          onEditDirectly={() => {
            setOpen(false);
            setDirect(true);
          }}
        />
      ) : null}
    </div>
  );
}
