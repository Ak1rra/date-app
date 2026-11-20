import { useState } from 'react';
import { Button } from '../../components/Button';
import { Card, EmptyState } from '../../components/EmptyState';
import {
  FormError,
  LoadError,
  PrivateBadge,
  Skeleton,
  TextAreaField,
} from '../../components/forms';
import {
  useCreateNote,
  useDeleteNote,
  useNotes,
  useUpdateNote,
  type NoteKind,
  type NoteOut,
} from '../../hooks/useNotes';
import type { ProfileOut } from '../../hooks/useProfiles';
import { profileName } from './ProfileSection';

function NoteRow({ note }: { note: NoteOut }) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(note.text);
  const update = useUpdateNote();
  const remove = useDeleteNote();
  const error = update.error ?? remove.error;

  return (
    <li className="flex flex-col gap-2 rounded-2xl border border-line p-3">
      {editing ? (
        <form
          className="flex flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (text.trim()) {
              update.mutate(
                { id: note.id, body: { text: text.trim() } },
                { onSuccess: () => setEditing(false) },
              );
            }
          }}
        >
          <TextAreaField label="Edit note" value={text} onChange={(e) => setText(e.target.value)} />
          <div className="flex gap-2">
            <Button type="submit" disabled={update.isPending}>
              Save
            </Button>
            <Button
              variant="secondary"
              onClick={() => {
                setText(note.text);
                setEditing(false);
              }}
            >
              Cancel
            </Button>
          </div>
        </form>
      ) : (
        <>
          <p className="whitespace-pre-wrap">{note.text}</p>
          <div className="flex flex-wrap gap-2">
            <Button
              variant="secondary"
              aria-label="Edit this note"
              onClick={() => setEditing(true)}
            >
              Edit
            </Button>
            <Button
              variant="secondary"
              aria-label="Archive this note"
              disabled={update.isPending}
              onClick={() => update.mutate({ id: note.id, body: { archived: true } })}
            >
              Archive
            </Button>
            <Button
              variant="secondary"
              aria-label="Delete this note"
              disabled={remove.isPending}
              onClick={() => remove.mutate(note.id)}
            >
              Delete
            </Button>
          </div>
        </>
      )}
      <FormError error={error} />
    </li>
  );
}

function NotesBlock({
  kind,
  other,
  title,
  addLabel,
  placeholder,
  emptyTitle,
  emptyText,
}: {
  kind: NoteKind;
  other: ProfileOut;
  title: string;
  addLabel: string;
  placeholder: string;
  emptyTitle: string;
  emptyText: string;
}) {
  const notes = useNotes(kind);
  const create = useCreateNote();
  const [text, setText] = useState('');

  return (
    <Card title={title}>
      <div className="flex flex-col gap-4 pt-2">
        <div className="px-2">
          <PrivateBadge />
        </div>
        <form
          className="flex flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            const trimmed = text.trim();
            if (trimmed) {
              create.mutate(
                { about_profile_id: other.id, kind, text: trimmed },
                { onSuccess: () => setText('') },
              );
            }
          }}
        >
          <TextAreaField
            label={addLabel}
            placeholder={placeholder}
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
          <FormError error={create.error} />
          <div>
            <Button type="submit" disabled={create.isPending || !text.trim()}>
              {create.isPending ? 'Adding…' : 'Add'}
            </Button>
          </div>
        </form>
        {notes.isPending ? <Skeleton label={`Loading ${title}`} /> : null}
        {notes.isError ? (
          <LoadError error={notes.error} onRetry={() => void notes.refetch()} />
        ) : null}
        {notes.data && notes.data.length === 0 ? (
          <EmptyState title={emptyTitle}>{emptyText}</EmptyState>
        ) : null}
        {notes.data && notes.data.length > 0 ? (
          <ul className="flex flex-col gap-3">
            {notes.data.map((n) => (
              <NoteRow key={n.id} note={n} />
            ))}
          </ul>
        ) : null}
      </div>
    </Card>
  );
}

/** Private notes about the other person (FR-PROF-8): only the author ever sees them. */
export function NotesSection({ other }: { other: ProfileOut }) {
  const name = profileName(other);
  return (
    <>
      <NotesBlock
        kind="note"
        other={other}
        title={`My notes about ${name}`}
        addLabel={`Add a note about ${name}`}
        placeholder="e.g. Gets cold easily, so skip the rooftop"
        emptyTitle="No notes yet"
        emptyText={`Jot down anything you notice about ${name}. ${name} never sees it.`}
      />
      <NotesBlock
        kind="mentioned_idea"
        other={other}
        title={`Ideas ${name} mentioned`}
        addLabel={`Add something ${name} mentioned`}
        placeholder="e.g. Wants to try the pottery class"
        emptyTitle="No ideas yet"
        emptyText={`When ${name} drops a hint, save it here so you can surprise them later.`}
      />
    </>
  );
}
