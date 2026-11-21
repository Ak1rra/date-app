import { useState } from 'react';
import { Button } from '../../components/Button';
import { FormError, TextAreaField, TextField } from '../../components/forms';
import { useScheduleItinerary, type ScheduleIn } from '../../hooks/useItineraries';

function toIso(value: string) {
  return new Date(value).toISOString();
}

function localInput(value: string | null) {
  if (!value) return '';
  const date = new Date(value);
  const offset = date.getTimezoneOffset() * 60000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}

export function ScheduleSheet({
  itineraryId,
  startsAt,
  endsAt,
  initialMode,
  onClose,
}: {
  itineraryId: string;
  startsAt: string | null;
  endsAt: string | null;
  initialMode: ScheduleIn['mode'];
  onClose: () => void;
}) {
  const schedule = useScheduleItinerary(itineraryId);
  const [starts, setStarts] = useState(localInput(startsAt));
  const [ends, setEnds] = useState(localInput(endsAt));
  const [mode, setMode] = useState<ScheduleIn['mode']>(initialMode);
  const [message, setMessage] = useState('');

  return (
    <div
      className="fixed inset-0 z-10 flex items-end bg-ink/40 p-3 sm:items-center sm:justify-center"
      role="presentation"
    >
      <section
        className="w-full max-w-lg rounded-3xl bg-surface p-5 shadow-xl"
        role="dialog"
        aria-modal="true"
        aria-labelledby="schedule-title"
      >
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 id="schedule-title" className="text-xl font-semibold">
              Schedule your date
            </h2>
            <p className="mt-1 text-sm text-muted">
              Pick the moment. We&apos;ll handle the butterflies.
            </p>
          </div>
          <Button variant="secondary" onClick={onClose} aria-label="Close schedule">
            Close
          </Button>
        </div>
        <form
          className="mt-5 flex flex-col gap-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (!starts) return;
            schedule.mutate(
              {
                starts_at: toIso(starts),
                ends_at: ends ? toIso(ends) : null,
                mode,
                invitation_message: message.trim() || null,
              },
              { onSuccess: onClose },
            );
          }}
        >
          <div className="grid gap-3 sm:grid-cols-2">
            <TextField
              label="Starts"
              type="datetime-local"
              required
              value={starts}
              onChange={(event) => setStarts(event.target.value)}
            />
            <TextField
              label="Ends (optional)"
              type="datetime-local"
              value={ends}
              onChange={(event) => setEnds(event.target.value)}
            />
          </div>
          <div className="flex flex-wrap gap-2" role="group" aria-label="How to share">
            {(['announce', 'surprise'] as const).map((option) => (
              <Button
                key={option}
                type="button"
                variant={mode === option ? 'primary' : 'secondary'}
                aria-pressed={mode === option}
                onClick={() => setMode(option)}
              >
                {option === 'announce' ? 'Announce' : 'Surprise'}
              </Button>
            ))}
          </div>
          {mode === 'surprise' ? (
            <p className="rounded-xl border-2 border-accent bg-accent-soft p-3 text-sm font-semibold">
              Surprise — they won&apos;t see it until you reveal it
            </p>
          ) : null}
          <TextAreaField
            label="Invitation message (optional)"
            value={message}
            onChange={(event) => setMessage(event.target.value)}
            placeholder="I have a little something planned for us…"
          />
          <FormError error={schedule.error} />
          <Button type="submit" disabled={!starts || schedule.isPending}>
            {schedule.isPending ? 'Scheduling…' : 'Schedule date'}
          </Button>
        </form>
      </section>
    </div>
  );
}
