import type { ReactNode } from 'react';
import type { IdeaOut } from '../../hooks/useIdeas';
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

/** Title, pitch, estimated cost and tags: what every idea shows wherever it appears. */
export function IdeaDetails({
  idea,
  titleLevel = 3,
  children,
}: {
  idea: IdeaOut;
  titleLevel?: 3 | 4;
  children?: ReactNode;
}) {
  const Title = `h${titleLevel}` as const;
  const cost = formatCost(idea.est_cost, idea.currency);
  return (
    <div className="flex flex-col gap-2">
      <Title className="text-lg font-semibold">{idea.title}</Title>
      {idea.pitch ? <p className="whitespace-pre-wrap">{idea.pitch}</p> : null}
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
      {children}
    </div>
  );
}
