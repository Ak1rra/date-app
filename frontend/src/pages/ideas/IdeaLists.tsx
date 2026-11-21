import { useState } from 'react';
import { ApiError } from '../../api/http';
import { Button } from '../../components/Button';
import { EmptyState } from '../../components/EmptyState';
import { FormError, LoadError, Skeleton } from '../../components/forms';
import { HomeIcon, IdeasIcon, SparkleIcon } from '../../components/Icons';
import {
  isMatch,
  useIdeaLists,
  useUpdateIdea,
  useVote,
  type IdeaListsOut,
  type IdeaOut,
  type VoteValue,
} from '../../hooks/useIdeas';
import { IdeaDetails, VoteChips } from './IdeaDetails';
import { IdeaForm } from './IdeaForm';
import type { People } from './usePeople';

export type ListName = keyof IdeaListsOut;

const VOTE_BUTTONS: { value: VoteValue; label: string }[] = [
  { value: 'yes', label: 'Yes please' },
  { value: 'maybe', label: 'Maybe' },
  { value: 'no', label: 'Nope' },
];

function IdeaRow({
  idea,
  people,
  onMatch,
}: {
  idea: IdeaOut;
  people: People;
  onMatch: (idea: IdeaOut) => void;
}) {
  const [editing, setEditing] = useState(false);
  const update = useUpdateIdea();
  const vote = useVote();

  if (editing) {
    return (
      <li className="rounded-2xl border border-line p-3">
        <IdeaForm
          idea={idea}
          submitLabel="Save changes"
          pending={update.isPending}
          error={update.error}
          onSubmit={(body) =>
            update.mutate({ id: idea.id, body }, { onSuccess: () => setEditing(false) })
          }
          onCancel={() => {
            update.reset();
            setEditing(false);
          }}
        />
      </li>
    );
  }

  return (
    <li className="flex flex-col gap-3 rounded-2xl border border-line p-3">
      <IdeaDetails idea={idea} titleLevel={4}>
        <VoteChips idea={idea} people={people} />
      </IdeaDetails>
      <div className="flex flex-wrap gap-2">
        <Button
          variant="secondary"
          aria-label={`Edit ${idea.title}`}
          onClick={() => setEditing(true)}
        >
          Edit
        </Button>
        {idea.status !== 'active' ? (
          <span className="self-center rounded-full border border-line bg-surface-2 px-3 py-1 text-sm text-muted">
            Expired
          </span>
        ) : (
          VOTE_BUTTONS.filter((b) => b.value !== idea.votes.mine).map((b) => (
            <Button
              key={b.value}
              variant="secondary"
              disabled={vote.isPending}
              aria-label={`${b.label} for ${idea.title}`}
              onClick={() =>
                vote.mutate(
                  { id: idea.id, value: b.value },
                  { onSuccess: (updated) => isMatch(updated) && onMatch(updated) },
                )
              }
            >
              {b.label}
            </Button>
          ))
        )}
      </div>
      {vote.error instanceof ApiError && vote.error.status === 409 ? (
        <p role="status" className="rounded-xl border border-line p-3 text-sm">
          This one&apos;s already on the calendar — votes are locked.
        </p>
      ) : (
        <FormError error={vote.error} />
      )}
    </li>
  );
}

function IdeaRows({
  ideas,
  people,
  onMatch,
}: {
  ideas: IdeaOut[];
  people: People;
  onMatch: (idea: IdeaOut) => void;
}) {
  return (
    <ul className="flex flex-col gap-3">
      {ideas.map((idea) => (
        <IdeaRow key={idea.id} idea={idea} people={people} onMatch={onMatch} />
      ))}
    </ul>
  );
}

/** One of the four vote lists (FR-VOTE-4): matches, maybes, waiting on my partner, passed. */
export function IdeaLists({
  which,
  people,
  onMatch,
}: {
  which: ListName;
  people: People;
  onMatch: (idea: IdeaOut) => void;
}) {
  const lists = useIdeaLists();
  if (lists.isPending) return <Skeleton label="Loading ideas" />;
  if (lists.isError) return <LoadError error={lists.error} onRetry={() => void lists.refetch()} />;

  const ideas = lists.data[which];
  const name = people.partnerName;

  if (which === 'matches') {
    return ideas.length === 0 ? (
      <EmptyState icon={<SparkleIcon />} title="No matches yet">
        Keep swiping. When you both say yes, it lands here.
      </EmptyState>
    ) : (
      <div className="pt-2">
        <IdeaRows ideas={ideas} people={people} onMatch={onMatch} />
      </div>
    );
  }

  if (which === 'maybes') {
    return ideas.length === 0 ? (
      <EmptyState icon={<IdeasIcon />} title="No maybes">
        Not sure about one? Tap Maybe and it waits here for a second look.
      </EmptyState>
    ) : (
      <div className="pt-2">
        <IdeaRows ideas={ideas} people={people} onMatch={onMatch} />
      </div>
    );
  }

  if (which === 'pending_partner') {
    return ideas.length === 0 ? (
      <EmptyState icon={<HomeIcon />} title={`Nothing waiting on ${name}`}>
        Everything you&apos;ve swiped on, {name} has seen too.
      </EmptyState>
    ) : (
      <div className="flex flex-col gap-3 pt-2">
        <p className="px-2 text-sm text-muted">
          You&apos;ve had your say. {name} hasn&apos;t yet, and your vote stays a secret until they
          do.
        </p>
        <IdeaRows ideas={ideas} people={people} onMatch={onMatch} />
      </div>
    );
  }

  return ideas.length === 0 ? (
    <EmptyState icon={<IdeasIcon />} title="Nothing passed">
      The ideas either of you said no to will tuck themselves away here.
    </EmptyState>
  ) : (
    <details className="pt-2">
      <summary className="min-h-11 cursor-pointer px-2 py-2 font-semibold">
        Show {ideas.length} passed {ideas.length === 1 ? 'idea' : 'ideas'}
      </summary>
      <div className="pt-2">
        <IdeaRows ideas={ideas} people={people} onMatch={onMatch} />
      </div>
    </details>
  );
}
