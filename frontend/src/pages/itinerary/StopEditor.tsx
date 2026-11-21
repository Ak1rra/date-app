import { useState } from 'react';
import { Button } from '../../components/Button';
import { FormError, TextField } from '../../components/forms';
import { useReplaceStops, type ItineraryOut, type StopIn } from '../../hooks/useItineraries';
import { AddStopSheet, type NewStop } from './AddStopSheet';

type Stop = ItineraryOut['stops'][number];

interface DraftStop {
  key: number;
  id: string | null;
  title: string;
  place_id: string | null;
  starts: string;
  ends: string;
  startsIso: string | null;
  endsIso: string | null;
  description: string | null;
  est_cost: number | null;
  notes: string | null;
  reveal_policy: StopIn['reveal_policy'];
}

let draftKey = 0;
const HOUR_MS = 60 * 60 * 1000;

/** A datetime-local value in the browser's timezone. */
function localInput(value: string | Date | null) {
  if (!value) return '';
  const date = new Date(value);
  const offset = date.getTimezoneOffset() * 60000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}

/** Keeps the server's exact instant when the input was left as it was (the input drops seconds). */
function toIso(local: string, original: string | null) {
  if (original && localInput(original) === local) return original;
  return new Date(local).toISOString();
}

function fromStop(stop: Stop, key: number): DraftStop {
  return {
    key,
    id: stop.id,
    title: stop.title,
    place_id: stop.place_id,
    starts: localInput(stop.starts_at),
    ends: localInput(stop.ends_at),
    startsIso: stop.starts_at,
    endsIso: stop.ends_at,
    description: stop.description,
    est_cost: stop.est_cost,
    notes: stop.notes,
    reveal_policy: stop.reveal_policy,
  };
}

export function StopEditor({
  itinerary,
  onClose,
}: {
  itinerary: ItineraryOut;
  onClose: () => void;
}) {
  const replace = useReplaceStops(itinerary.id);
  const [stops, setStops] = useState<DraftStop[]>(() =>
    [...itinerary.stops]
      .sort((a, b) => a.position - b.position)
      .map((stop) => fromStop(stop, draftKey++)),
  );
  const [adding, setAdding] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  function update(key: number, patch: Partial<DraftStop>) {
    setProblem(null);
    setStops((current) => current.map((stop) => (stop.key === key ? { ...stop, ...patch } : stop)));
  }

  function move(index: number, delta: -1 | 1) {
    setStops((current) => {
      const next = [...current];
      const moved = next[index];
      const other = next[index + delta];
      if (!moved || !other) return current;
      next[index] = other;
      next[index + delta] = moved;
      return next;
    });
  }

  function remove(stop: DraftStop) {
    if (!window.confirm(`Remove ${stop.title} from this date?`)) return;
    setStops((current) => current.filter((item) => item.key !== stop.key));
  }

  function add(stop: NewStop) {
    setProblem(null);
    const last = stops[stops.length - 1];
    const fallback = localInput(itinerary.starts_at ?? new Date());
    const start = last?.ends || fallback;
    const end = localInput(new Date(new Date(start).getTime() + HOUR_MS));
    setStops((current) => [
      ...current,
      {
        key: draftKey++,
        id: null,
        title: stop.title,
        place_id: stop.place_id,
        starts: start,
        ends: end,
        startsIso: null,
        endsIso: null,
        description: null,
        est_cost: null,
        notes: null,
        reveal_policy: 'with_itinerary',
      },
    ]);
    setAdding(false);
  }

  function save() {
    for (const stop of stops) {
      if (!stop.title.trim()) return setProblem('Every stop needs a name.');
      if (!stop.starts || !stop.ends)
        return setProblem(`Pick a start and end time for ${stop.title}.`);
      if (new Date(stop.ends) <= new Date(stop.starts))
        return setProblem(`${stop.title} needs to end after it starts.`);
    }
    setProblem(null);
    replace.mutate(
      stops.map((stop) => ({
        ...(stop.id ? { id: stop.id } : {}),
        title: stop.title.trim(),
        place_id: stop.place_id,
        starts_at: toIso(stop.starts, stop.startsIso),
        ends_at: toIso(stop.ends, stop.endsIso),
        description: stop.description,
        est_cost: stop.est_cost,
        notes: stop.notes,
        reveal_policy: stop.reveal_policy,
      })),
      { onSuccess: onClose },
    );
  }

  return (
    <section className="flex flex-col gap-4" aria-label="Edit stops">
      <h2 className="text-xl font-semibold">Edit the plan</h2>
      {stops.length === 0 ? <p className="text-muted">No stops yet. Add the first one.</p> : null}
      <ol className="flex flex-col gap-4">
        {stops.map((stop, index) => (
          <li
            key={stop.key}
            className="flex flex-col gap-3 rounded-3xl border border-line bg-surface p-4 shadow-sm"
          >
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="text-sm font-semibold text-muted">Stop {index + 1}</p>
              <div className="flex flex-wrap gap-2">
                <Button
                  variant="secondary"
                  disabled={index === 0}
                  aria-label={`Move ${stop.title} up`}
                  onClick={() => move(index, -1)}
                >
                  ↑
                </Button>
                <Button
                  variant="secondary"
                  disabled={index === stops.length - 1}
                  aria-label={`Move ${stop.title} down`}
                  onClick={() => move(index, 1)}
                >
                  ↓
                </Button>
                <Button
                  variant="secondary"
                  disabled={stops.length <= 1}
                  aria-label={`Remove ${stop.title}`}
                  onClick={() => remove(stop)}
                >
                  Remove
                </Button>
              </div>
            </div>
            <TextField
              label={`Name of stop ${index + 1}`}
              value={stop.title}
              onChange={(event) => update(stop.key, { title: event.target.value })}
            />
            <div className="grid gap-3 sm:grid-cols-2">
              <TextField
                label={`Start time for ${stop.title}`}
                type="datetime-local"
                value={stop.starts}
                onChange={(event) => update(stop.key, { starts: event.target.value })}
              />
              <TextField
                label={`End time for ${stop.title}`}
                type="datetime-local"
                value={stop.ends}
                onChange={(event) => update(stop.key, { ends: event.target.value })}
              />
            </div>
          </li>
        ))}
      </ol>
      {stops.length === 1 ? (
        <p className="text-sm text-muted">A date needs at least one stop.</p>
      ) : null}
      {problem ? (
        <p role="alert" className="rounded-xl border border-coral p-3 text-sm text-ink">
          {problem}
        </p>
      ) : null}
      <FormError error={replace.error} />
      <div className="flex flex-wrap gap-2">
        <Button variant="secondary" disabled={stops.length >= 8} onClick={() => setAdding(true)}>
          {stops.length >= 8 ? 'Up to 8 stops' : 'Add a stop'}
        </Button>
        <Button disabled={replace.isPending || stops.length === 0} onClick={save}>
          {replace.isPending ? 'Saving…' : 'Save stops'}
        </Button>
        <Button variant="secondary" disabled={replace.isPending} onClick={onClose}>
          Discard changes
        </Button>
      </div>
      {adding ? <AddStopSheet onAdd={add} onClose={() => setAdding(false)} /> : null}
    </section>
  );
}
