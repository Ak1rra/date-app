import { useId, useState } from 'react';
import { Button } from '../../components/Button';
import { Card, EmptyState } from '../../components/EmptyState';
import { FormError, LoadError, SelectField, Skeleton, TextField } from '../../components/forms';
import {
  useDeleteImportantDate,
  useImportantDates,
  useSaveImportantDate,
  type ImportantDateIn,
  type ImportantDateOut,
} from '../../hooks/useImportantDates';
import { useProfiles } from '../../hooks/useProfiles';
import { profileName } from './ProfileSection';
import { pretty } from './options';

const KINDS: ImportantDateIn['kind'][] = ['anniversary', 'birthday', 'custom'];

const blank: ImportantDateIn = {
  name: '',
  date: '',
  kind: 'anniversary',
  recurring: true,
  profile_id: null,
};

function DateForm({
  initial,
  id,
  onDone,
}: {
  initial: ImportantDateIn;
  id?: string;
  onDone: () => void;
}) {
  const [form, setForm] = useState(initial);
  const save = useSaveImportantDate();
  const profiles = useProfiles();
  const checkboxId = useId();
  const set = <K extends keyof ImportantDateIn>(key: K, value: ImportantDateIn[K]) =>
    setForm((f) => ({ ...f, [key]: value }));

  return (
    <form
      aria-label={id ? 'Edit important date' : 'Add an important date'}
      className="flex flex-col gap-4 rounded-2xl border border-line p-3"
      onSubmit={(e) => {
        e.preventDefault();
        const body = { ...form, profile_id: form.kind === 'birthday' ? form.profile_id : null };
        save.mutate({ id, body }, { onSuccess: onDone });
      }}
    >
      <TextField
        label="What is it?"
        placeholder="e.g. Our anniversary"
        required
        value={form.name}
        onChange={(e) => set('name', e.target.value)}
      />
      <TextField
        label="Date"
        type="date"
        required
        value={form.date}
        onChange={(e) => set('date', e.target.value)}
      />
      <SelectField
        label="Kind"
        value={form.kind}
        onChange={(e) => set('kind', e.target.value as ImportantDateIn['kind'])}
      >
        {KINDS.map((k) => (
          <option key={k} value={k}>
            {pretty(k)}
          </option>
        ))}
      </SelectField>
      {form.kind === 'birthday' ? (
        <SelectField
          label="Whose birthday?"
          value={form.profile_id ?? ''}
          onChange={(e) => set('profile_id', e.target.value || null)}
        >
          <option value="">Someone else</option>
          {profiles.data?.map((p) => (
            <option key={p.id} value={p.id}>
              {profileName(p)}
            </option>
          ))}
        </SelectField>
      ) : null}
      <div className="flex min-h-11 items-center gap-2">
        <input
          id={checkboxId}
          type="checkbox"
          className="size-5 accent-[var(--accent)]"
          checked={form.recurring}
          onChange={(e) => set('recurring', e.target.checked)}
        />
        <label htmlFor={checkboxId}>Every year</label>
      </div>
      <FormError error={save.error} />
      <div className="flex flex-wrap gap-2">
        <Button type="submit" disabled={save.isPending}>
          {save.isPending ? 'Saving…' : id ? 'Save changes' : 'Add date'}
        </Button>
        <Button variant="secondary" onClick={onDone} disabled={save.isPending}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

function DateRow({ item }: { item: ImportantDateOut }) {
  const [editing, setEditing] = useState(false);
  const remove = useDeleteImportantDate();
  if (editing) {
    return (
      <li>
        <DateForm
          id={item.id}
          initial={{
            name: item.name,
            date: item.date,
            kind: item.kind,
            recurring: item.recurring,
            profile_id: item.profile_id,
          }}
          onDone={() => setEditing(false)}
        />
      </li>
    );
  }
  return (
    <li className="flex flex-col gap-2 rounded-2xl border border-line p-3">
      <div>
        <p className="font-semibold">{item.name}</p>
        <p className="text-muted">
          {item.date} · {pretty(item.kind)}
          {item.recurring ? ' · every year' : ''}
        </p>
      </div>
      <FormError error={remove.error} />
      <div className="flex gap-2">
        <Button
          variant="secondary"
          aria-label={`Edit ${item.name}`}
          onClick={() => setEditing(true)}
        >
          Edit
        </Button>
        <Button
          variant="secondary"
          aria-label={`Delete ${item.name}`}
          disabled={remove.isPending}
          onClick={() => remove.mutate(item.id)}
        >
          Delete
        </Button>
      </div>
    </li>
  );
}

export function ImportantDatesSection() {
  const dates = useImportantDates();
  const [adding, setAdding] = useState(false);
  const sorted = dates.data ? [...dates.data].sort((a, b) => a.date.localeCompare(b.date)) : [];

  return (
    <Card title="Important dates">
      <div className="flex flex-col gap-4 pt-2">
        {dates.isPending ? <Skeleton label="Loading important dates" /> : null}
        {dates.isError ? (
          <LoadError error={dates.error} onRetry={() => void dates.refetch()} />
        ) : null}
        {dates.data && sorted.length === 0 && !adding ? (
          <EmptyState title="No dates yet">
            Add birthdays and your anniversary. We will remind you early enough to plan something.
          </EmptyState>
        ) : null}
        {sorted.length > 0 ? (
          <ul className="flex flex-col gap-3">
            {sorted.map((item) => (
              <DateRow key={item.id} item={item} />
            ))}
          </ul>
        ) : null}
        {adding ? (
          <DateForm initial={blank} onDone={() => setAdding(false)} />
        ) : dates.data ? (
          <div>
            <Button variant="secondary" onClick={() => setAdding(true)}>
              Add an important date
            </Button>
          </div>
        ) : null}
      </div>
    </Card>
  );
}
