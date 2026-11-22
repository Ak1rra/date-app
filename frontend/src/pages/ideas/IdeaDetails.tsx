import type { ReactNode } from 'react';
import { Pill } from '../../components/Pill';
import type { IdeaOut } from '../../hooks/useIdeas';
import { safeHttpUrl } from '../../lib/url';
import type { People, Person } from './usePeople';

/** An estimated cost in the idea's own currency (an unknown code falls back to USD). */
export function formatCost(cost: number | null, currency: string | null = 'USD'): string | null {
  if (cost === null) return null;
  const options = {
    style: 'currency',
    maximumFractionDigits: 2,
    minimumFractionDigits: 0,
  } as const;
  try {
    return new Intl.NumberFormat(undefined, { ...options, currency: currency ?? 'USD' }).format(
      cost,
    );
  } catch {
    return new Intl.NumberFormat(undefined, { ...options, currency: 'USD' }).format(cost);
  }
}

const MILES_PER_KM = 0.621371;

/** "About 45 min", "About 2 h" or "About 1 h 30 min". */
function formatDuration(minutes: number): string {
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  if (hours === 0) return `About ${rest} min`;
  return rest === 0 ? `About ${hours} h` : `About ${hours} h ${rest} min`;
}

const BEST_TIME_WORDS = {
  day: 'Best in the daytime',
  evening: 'Best in the evening',
  weekend: 'Best at the weekend',
  any: 'Any time works',
} as const;

const INDOOR_OUTDOOR_WORDS = {
  indoor: 'Indoors',
  outdoor: 'Outdoors',
  either: 'In or out',
} as const;

/** The event's local date and time, in the viewer's locale; null when the API gave neither. */
function formatEventWhen(event: NonNullable<IdeaOut['event']>): string | null {
  const parts: string[] = [];
  if (event.local_date) {
    const [y, m, d] = event.local_date.split('-').map(Number);
    if (y && m && d) {
      parts.push(
        new Intl.DateTimeFormat(undefined, {
          weekday: 'short',
          month: 'short',
          day: 'numeric',
        }).format(new Date(y, m - 1, d)),
      );
    }
  }
  if (event.local_time) {
    const [hh, mm] = event.local_time.split(':').map(Number);
    if (hh !== undefined && !Number.isNaN(hh)) {
      parts.push(
        new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit' }).format(
          new Date(2000, 0, 1, hh, mm || 0),
        ),
      );
    }
  }
  return parts.length > 0 ? parts.join(' at ') : null;
}

/** AI ideas say whether they came from live data; manual and mentioned ones carry no badge. */
function groundingLabel(idea: IdeaOut): string | null {
  if (idea.source !== 'ai' && idea.source !== 'chat' && idea.source !== 'plan') return null;
  return idea.grounded ? 'Live pick' : 'Evergreen idea';
}

// Full class names so Tailwind can see them.
const CHIP: Record<Person | 'none', string> = {
  husband: 'border-husband bg-husband-soft text-husband',
  wife: 'border-wife bg-wife-soft text-wife',
  none: 'border-line bg-surface-2 text-ink',
};

const VOTE_WORDS = { yes: 'Yes', no: 'No', maybe: 'Maybe' } as const;

/** Votes per FR-VOTE-2: my vote when I have one, the partner's only once the API reveals it. */
export function VoteChips({ idea, people }: { idea: IdeaOut; people: People }) {
  const { mine, partner } = idea.votes;
  if (!mine && !partner) return null;
  return (
    <ul className="flex flex-wrap gap-2" aria-label="Votes">
      {mine ? (
        <li
          className={`rounded-full border-2 px-3 py-1 text-sm font-semibold ${CHIP[people.mePerson ?? 'none']}`}
        >
          You: {VOTE_WORDS[mine]}
        </li>
      ) : null}
      {partner ? (
        <li
          className={`rounded-full border-2 px-3 py-1 text-sm font-semibold ${CHIP[people.partnerPerson ?? 'none']}`}
        >
          {people.partnerName}: {VOTE_WORDS[partner]}
        </li>
      ) : null}
    </ul>
  );
}

/** Title, pitch, timing, venue, cost, tags and attribution: what every idea shows wherever it appears. */
export function IdeaDetails({
  idea,
  titleLevel = 2,
  children,
}: {
  idea: IdeaOut;
  titleLevel?: 2 | 3 | 4;
  children?: ReactNode;
}) {
  const Title = `h${titleLevel}` as const;
  const cost = formatCost(idea.est_cost, idea.currency);
  const when = idea.event ? formatEventWhen(idea.event) : null;
  const { venue } = idea;
  const priceLevel =
    venue?.price_level != null
      ? venue.price_level === 0
        ? 'Free'
        : '$'.repeat(Math.min(venue.price_level, 4))
      : null;
  const details = [
    idea.duration_minutes ? formatDuration(idea.duration_minutes) : null,
    idea.best_time ? BEST_TIME_WORDS[idea.best_time] : null,
    idea.indoor_outdoor ? INDOOR_OUTDOOR_WORDS[idea.indoor_outdoor] : null,
    when,
  ].filter((item): item is string => item !== null);
  const venueLink = safeHttpUrl(venue?.url);
  const venueDetails = venue
    ? [
        venueLink ? null : venue.name,
        venue.rating != null ? `${venue.rating.toFixed(1)}★` : null,
        priceLevel,
        venue.distance_km != null
          ? `${(venue.distance_km * MILES_PER_KM).toFixed(1)} mi away`
          : null,
      ].filter((item): item is string => item !== null)
    : [];
  const grounding = groundingLabel(idea);
  return (
    <div className="flex flex-col gap-2">
      <Title className="text-lg font-semibold">{idea.title}</Title>
      {idea.pitch ? <p className="whitespace-pre-wrap">{idea.pitch}</p> : null}
      {grounding ? (
        <p>
          <Pill>{grounding}</Pill>
        </p>
      ) : null}
      {details.length > 0 ? (
        <ul className="flex flex-wrap gap-x-3 gap-y-1 text-sm text-muted" aria-label="Details">
          {details.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      ) : null}
      {venueDetails.length > 0 || venueLink ? (
        <ul className="flex flex-wrap gap-x-3 gap-y-1 text-sm text-muted" aria-label="Venue">
          {venueLink ? (
            <li>
              <a
                href={venueLink}
                target="_blank"
                rel="noopener noreferrer"
                className="font-semibold text-accent underline"
              >
                {venue?.name ?? 'Link'}
                <span className="sr-only"> (opens in a new tab)</span>
              </a>
            </li>
          ) : null}
          {venueDetails.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      ) : null}
      {cost ? <p className="text-sm text-muted">About {cost}</p> : null}
      {idea.tags.length > 0 ? (
        <ul className="flex flex-wrap gap-2" aria-label="Tags">
          {idea.tags.map((tag) => (
            <li
              key={tag}
              className="rounded-full border border-line bg-surface-2 px-3 py-1 text-sm text-ink"
            >
              {tag}
            </li>
          ))}
        </ul>
      ) : null}
      {idea.attributions.length > 0 ? (
        <p className="text-xs text-muted">Data: {idea.attributions.join(', ')}</p>
      ) : null}
      {children}
    </div>
  );
}
