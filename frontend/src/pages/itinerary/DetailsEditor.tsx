import { useState } from 'react';
import { Button } from '../../components/Button';
import { FormError, TextAreaField, TextField } from '../../components/forms';
import {
  useUpdateItinerary,
  type ItineraryOut,
  type ItineraryPatch,
} from '../../hooks/useItineraries';

const orNull = (value: string) => value.trim() || null;

export function DetailsEditor({
  itinerary,
  onClose,
}: {
  itinerary: ItineraryOut;
  onClose: () => void;
}) {
  const update = useUpdateItinerary(itinerary.id);
  const [title, setTitle] = useState(itinerary.title);
  const [budget, setBudget] = useState(itinerary.budget === null ? '' : String(itinerary.budget));
  const [backup, setBackup] = useState(itinerary.backup_plan ?? '');
  const [bringWear, setBringWear] = useState(itinerary.bring_wear ?? '');
  const [message, setMessage] = useState(itinerary.invitation_message ?? '');

  function submit() {
    const patch: ItineraryPatch = {};
    if (title.trim() !== itinerary.title) patch.title = title.trim();
    const nextBudget = budget.trim() === '' ? null : Number(budget);
    if (nextBudget !== itinerary.budget) patch.budget = nextBudget;
    if (orNull(backup) !== itinerary.backup_plan) patch.backup_plan = orNull(backup);
    if (orNull(bringWear) !== itinerary.bring_wear) patch.bring_wear = orNull(bringWear);
    if (orNull(message) !== itinerary.invitation_message)
      patch.invitation_message = orNull(message);
    if (Object.keys(patch).length === 0) return onClose();
    update.mutate(patch, { onSuccess: onClose });
  }

  const budgetInvalid = budget.trim() !== '' && !(Number(budget) >= 0);

  return (
    <div
      className="fixed inset-0 z-10 flex items-end bg-ink/40 p-3 sm:items-center sm:justify-center"
      role="presentation"
    >
      <section
        className="max-h-full w-full max-w-lg overflow-y-auto rounded-3xl bg-surface p-5 shadow-xl"
        role="dialog"
        aria-modal="true"
        aria-labelledby="details-title"
      >
        <div className="flex items-start justify-between gap-4">
          <h2 id="details-title" className="text-xl font-semibold">
            Edit details
          </h2>
          <Button variant="secondary" onClick={onClose} aria-label="Close edit details">
            Close
          </Button>
        </div>
        <form
          className="mt-5 flex flex-col gap-4"
          onSubmit={(event) => {
            event.preventDefault();
            submit();
          }}
        >
          <TextField
            label="Title"
            required
            value={title}
            onChange={(event) => setTitle(event.target.value)}
          />
          <TextField
            label={`Budget (${itinerary.currency})`}
            type="number"
            inputMode="decimal"
            min={0}
            step="any"
            value={budget}
            onChange={(event) => setBudget(event.target.value)}
          />
          <TextAreaField
            label="Backup plan"
            value={backup}
            onChange={(event) => setBackup(event.target.value)}
          />
          <TextAreaField
            label="Bring / wear"
            value={bringWear}
            onChange={(event) => setBringWear(event.target.value)}
          />
          <TextAreaField
            label="Invitation message"
            value={message}
            onChange={(event) => setMessage(event.target.value)}
          />
          <FormError error={update.error} />
          <Button type="submit" disabled={!title.trim() || budgetInvalid || update.isPending}>
            {update.isPending ? 'Saving…' : 'Save details'}
          </Button>
        </form>
      </section>
    </div>
  );
}
