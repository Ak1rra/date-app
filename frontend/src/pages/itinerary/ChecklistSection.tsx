import { useState } from 'react';
import { Button } from '../../components/Button';
import { Card } from '../../components/EmptyState';
import { FormError, SelectField, TextField } from '../../components/forms';
import {
  useAddChecklistItem,
  useDeleteChecklistItem,
  useUpdateChecklistItem,
  type ItineraryOut,
} from '../../hooks/useItineraries';

const kinds = ['reserve', 'buy', 'bring', 'arrange', 'wear', 'other'] as const;

function dueDate(value: string | null) {
  if (!value) return '';
  return new Date(value).toLocaleDateString();
}

export function ChecklistSection({
  itinerary,
  canEdit,
}: {
  itinerary: ItineraryOut;
  canEdit: boolean;
}) {
  const add = useAddChecklistItem(itinerary.id);
  const update = useUpdateChecklistItem(itinerary.id);
  const remove = useDeleteChecklistItem(itinerary.id);
  const [text, setText] = useState('');
  const [kind, setKind] = useState<(typeof kinds)[number]>('other');
  const [due, setDue] = useState('');

  return (
    <Card title="Finishing touches">
      <div className="flex flex-col gap-4 pt-3">
        {itinerary.checklist.length > 0 ? (
          <ul className="flex flex-col gap-2">
            {itinerary.checklist.map((item) => (
              <li
                key={item.id}
                className="flex items-center gap-3 rounded-2xl border border-line p-3"
              >
                <input
                  type="checkbox"
                  checked={item.done}
                  disabled={!canEdit || update.isPending}
                  aria-label={`Mark ${item.text} done`}
                  onChange={(event) =>
                    update.mutate({ itemId: item.id, body: { done: event.target.checked } })
                  }
                  className="size-5 accent-[var(--accent)]"
                />
                <div className="min-w-0 flex-1">
                  <p className={item.done ? 'text-muted line-through' : 'font-semibold'}>
                    {item.text}
                  </p>
                  <p className="text-sm text-muted">
                    {item.kind}
                    {item.due_at ? ` · due ${dueDate(item.due_at)}` : ''}
                  </p>
                </div>
                {canEdit ? (
                  <Button
                    variant="secondary"
                    aria-label={`Delete ${item.text}`}
                    disabled={remove.isPending}
                    onClick={() => remove.mutate(item.id)}
                  >
                    Delete
                  </Button>
                ) : null}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-muted">A few tiny details make the big date feel effortless.</p>
        )}

        {canEdit ? (
          <form
            className="flex flex-col gap-3 border-t border-line pt-4"
            onSubmit={(event) => {
              event.preventDefault();
              const trimmed = text.trim();
              if (!trimmed) return;
              add.mutate(
                {
                  text: trimmed,
                  kind,
                  due_at: due ? new Date(`${due}T12:00:00`).toISOString() : null,
                },
                {
                  onSuccess: () => {
                    setText('');
                    setDue('');
                    setKind('other');
                  },
                },
              );
            }}
          >
            <TextField
              label="Add a finishing touch"
              value={text}
              onChange={(event) => setText(event.target.value)}
              placeholder="Reserve the table"
            />
            <div className="grid gap-3 sm:grid-cols-2">
              <SelectField
                label="Kind"
                value={kind}
                onChange={(event) => setKind(event.target.value as (typeof kinds)[number])}
              >
                {kinds.map((option) => (
                  <option key={option} value={option}>
                    {option}
                  </option>
                ))}
              </SelectField>
              <TextField
                label="Due date (optional)"
                type="date"
                value={due}
                onChange={(event) => setDue(event.target.value)}
              />
            </div>
            <FormError error={add.error ?? update.error ?? remove.error} />
            <div>
              <Button type="submit" disabled={!text.trim() || add.isPending}>
                Add item
              </Button>
            </div>
          </form>
        ) : null}
      </div>
    </Card>
  );
}
