import { useState } from 'react';
import { plannerError } from '../../api/plannerError';
import { Button } from '../../components/Button';
import { FormError, TextField } from '../../components/forms';
import { Pill } from '../../components/Pill';
import {
  useIsRegenerating,
  useRegenerateStop,
  type ItineraryOut,
} from '../../hooks/useItineraries';
import { Money } from './Money';

type Stop = ItineraryOut['stops'][number];

const modeLabels: Record<NonNullable<Stop['travel_to_next']>['mode'], string> = {
  drive: 'Drive',
  transit: 'Transit',
  walk: 'Walk',
  bicycle: 'Bike',
  rideshare: 'Rideshare',
};

function localTime(value: string | null) {
  if (!value) return null;
  return new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit' }).format(
    new Date(value),
  );
}

function warningText(value: Record<string, unknown>) {
  if (typeof value.message === 'string') return value.message;
  if (typeof value.text === 'string') return value.text;
  if (typeof value.code === 'string') return value.code;
  return (
    Object.values(value)
      .filter((part) => typeof part === 'string')
      .join(' · ') || 'A little note for this stop.'
  );
}

function miles(meters: number | null) {
  return meters === null
    ? null
    : `${(meters / 1609.344).toFixed(meters / 1609.344 < 10 ? 1 : 0)} miles`;
}

/** Planner-only: asks the planner for a replacement for one stop, with an optional steer. */
function TryAnother({ itineraryId, stop }: { itineraryId: string; stop: Stop }) {
  const regenerate = useRegenerateStop(itineraryId);
  // One regeneration at a time per itinerary: each response replaces the whole cached itinerary.
  const busy = useIsRegenerating(itineraryId);
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState('');

  if (!open) {
    return (
      <div>
        <Button
          variant="secondary"
          aria-label={`Try another for ${stop.title}`}
          disabled={busy}
          onClick={() => setOpen(true)}
        >
          Try another
        </Button>
      </div>
    );
  }
  return (
    <form
      className="flex flex-col gap-3 rounded-2xl bg-surface-2 p-3"
      aria-label={`Try another for ${stop.title}`}
      onSubmit={(event) => {
        event.preventDefault();
        if (busy) return;
        regenerate.mutate(
          { stopId: stop.id, note: note.trim() || undefined },
          {
            onSuccess: () => {
              setOpen(false);
              setNote('');
            },
          },
        );
      }}
    >
      <TextField
        label="What should change? (optional)"
        maxLength={300}
        disabled={busy}
        value={note}
        onChange={(event) => setNote(event.target.value)}
        placeholder="Something quieter, closer to home…"
      />
      <FormError error={plannerError(regenerate.error)} />
      <div className="flex flex-wrap gap-2">
        <Button type="submit" disabled={busy}>
          {regenerate.isPending ? 'Finding something new…' : 'Find something new'}
        </Button>
        <Button
          variant="secondary"
          disabled={regenerate.isPending}
          onClick={() => {
            regenerate.reset();
            setOpen(false);
          }}
        >
          Never mind
        </Button>
      </div>
    </form>
  );
}

export function StopCard({
  stop,
  currency,
  itineraryId,
}: {
  stop: Stop;
  currency?: string;
  /** Set only for the planner of a draft or scheduled date; shows "Try another". */
  itineraryId?: string;
}) {
  const venue = stop.venue;
  const links = [
    stop.maps.google ? { href: stop.maps.google, label: 'Google Maps' } : null,
    stop.maps.apple ? { href: stop.maps.apple, label: 'Apple Maps' } : null,
    stop.maps.directions_from_previous
      ? { href: stop.maps.directions_from_previous, label: 'Directions' }
      : null,
    stop.booking_url ? { href: stop.booking_url, label: 'Booking' } : null,
  ].filter((link): link is { href: string; label: string } => !!link);

  return (
    <article className="flex flex-col gap-3 rounded-3xl border border-line bg-surface p-4 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-sm font-semibold text-muted">Stop {stop.position + 1}</p>
          <h2 className="text-xl font-semibold">{stop.title}</h2>
          {stop.evergreen ? (
            <p className="mt-1">
              <Pill>Evergreen</Pill>
            </p>
          ) : null}
          {stop.description ? <p className="mt-1 text-muted">{stop.description}</p> : null}
        </div>
        {stop.est_cost !== null ? (
          <p className="font-semibold">
            <Money value={stop.est_cost} currency={currency} />
          </p>
        ) : null}
      </div>

      {stop.starts_at || stop.ends_at ? (
        <p className="text-sm font-semibold">
          {localTime(stop.starts_at) ?? 'Any time'}
          {stop.ends_at ? ` – ${localTime(stop.ends_at)}` : ''}
        </p>
      ) : null}

      {venue ? (
        <div className="flex flex-col gap-1 text-sm">
          {venue.name ? <p className="font-semibold">{venue.name}</p> : null}
          {venue.address ? <p className="text-muted">{venue.address}</p> : null}
          <p className="text-muted">
            {venue.rating !== null && venue.rating !== undefined
              ? `★ ${venue.rating.toFixed(1)}`
              : null}
            {venue.price_level !== null && venue.price_level !== undefined
              ? `${venue.rating !== null && venue.rating !== undefined ? ' · ' : ''}${'$'.repeat(Math.max(1, venue.price_level))}`
              : null}
          </p>
        </div>
      ) : null}

      {stop.notes ? <p className="rounded-xl bg-surface-2 p-3 text-sm">{stop.notes}</p> : null}

      {links.length > 0 ? (
        <div className="flex flex-wrap gap-2">
          {links.map((link) => (
            <a
              key={link.label}
              href={link.href}
              target="_blank"
              rel="noreferrer"
              className="rounded-full border border-line px-3 py-2 text-sm font-semibold text-accent underline"
            >
              {link.label}
            </a>
          ))}
        </div>
      ) : null}

      {itineraryId ? <TryAnother itineraryId={itineraryId} stop={stop} /> : null}

      {stop.warnings.length > 0 ? (
        <ul
          className="flex flex-col gap-1 rounded-xl border border-coral p-3 text-sm"
          aria-label="Stop warnings"
        >
          {stop.warnings.map((warning, index) => (
            <li key={index}>⚠ {warningText(warning)}</li>
          ))}
        </ul>
      ) : null}

      {stop.travel_to_next ? (
        <div className="border-t border-line pt-3 text-sm text-muted">
          <span className="font-semibold text-ink">{modeLabels[stop.travel_to_next.mode]}</span> ·{' '}
          {stop.travel_to_next.duration_minutes} min
          {miles(stop.travel_to_next.distance_m)
            ? ` · ${miles(stop.travel_to_next.distance_m)}`
            : ''}
          {stop.travel_to_next.source === 'estimate' ? (
            <span className="ml-2 rounded-full bg-surface-2 px-2 py-1 text-xs">approx.</span>
          ) : null}
        </div>
      ) : null}
    </article>
  );
}
