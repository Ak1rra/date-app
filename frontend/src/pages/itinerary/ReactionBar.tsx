import { useState } from 'react';
import { Button } from '../../components/Button';
import { FormError, TextAreaField, TextField } from '../../components/forms';
import { useReactToItinerary } from '../../hooks/useItineraries';

export function ReactionBar({ itineraryId }: { itineraryId: string }) {
  const react = useReactToItinerary(itineraryId);
  const [moving, setMoving] = useState(false);
  const [suggestedStart, setSuggestedStart] = useState('');
  const [note, setNote] = useState('');

  return (
    <section
      className="rounded-3xl border-2 border-accent bg-accent-soft p-4"
      aria-label="Your response"
    >
      <h2 className="text-lg font-semibold">How does this sound, darling?</h2>
      <div className="mt-3 flex flex-wrap gap-2">
        <Button disabled={react.isPending} onClick={() => react.mutate({ kind: 'heart' })}>
          ❤️ I&apos;m in
        </Button>
        <Button variant="secondary" onClick={() => setMoving((value) => !value)}>
          Can we move it?
        </Button>
      </div>
      {moving ? (
        <form
          className="mt-4 flex flex-col gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            react.mutate(
              {
                kind: 'move',
                suggested_start: suggestedStart ? new Date(suggestedStart).toISOString() : null,
                note: note.trim() || null,
              },
              { onSuccess: () => setMoving(false) },
            );
          }}
        >
          <TextField
            label="Suggested start (optional)"
            type="datetime-local"
            value={suggestedStart}
            onChange={(event) => setSuggestedStart(event.target.value)}
          />
          <TextAreaField
            label="A note (optional)"
            value={note}
            onChange={(event) => setNote(event.target.value)}
            placeholder="Could we make it a little later?"
          />
          <FormError error={react.error} />
          <Button type="submit" disabled={react.isPending}>
            Send suggestion
          </Button>
        </form>
      ) : null}
      {!moving ? <FormError error={react.error} /> : null}
    </section>
  );
}
