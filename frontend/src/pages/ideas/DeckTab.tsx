import { useEffect, useState } from 'react';
import { Button, ButtonLink } from '../../components/Button';
import { EmptyState } from '../../components/EmptyState';
import { IdeasIcon, UsIcon } from '../../components/Icons';
import { LoadError, Skeleton } from '../../components/forms';
import {
  isMatch,
  useDeck,
  useUndoVote,
  useVote,
  type IdeaOut,
  type VoteValue,
} from '../../hooks/useIdeas';
import { useEmailMode } from '../../hooks/usePairing';
import { usePrefersReducedMotion } from '../../hooks/useReducedMotion';
import { SwipeCard } from './SwipeCard';

const KEY_VOTES: Record<string, VoteValue> = {
  ArrowRight: 'yes',
  ArrowLeft: 'no',
  ArrowUp: 'maybe',
};

const NOTHING_TO_UNDO =
  'Nothing to take back right now. Only your latest swipe can be undone, and only for a little while.';

interface DeckTabProps {
  partnerName: string;
  /** True while another layer (the match overlay, the generate sheet) owns the keyboard. */
  paused: boolean;
  onMatch: (idea: IdeaOut) => void;
  /** Opens the generate sheet, which the page owns so a deck refetch can't unmount it. */
  onGenerate: () => void;
}

/** The swipe deck: one card at a time, voted by drag, buttons or arrow keys. */
export function DeckTab({ partnerName, paused, onMatch, onGenerate }: DeckTabProps) {
  const deck = useDeck();
  const emailMode = useEmailMode();
  const vote = useVote();
  const undo = useUndoVote();
  const reducedMotion = usePrefersReducedMotion();
  // Cards voted on this visit leave the stack at once instead of waiting for the refetch.
  const [voted, setVoted] = useState<ReadonlySet<string>>(new Set());
  const card = deck.data?.cards.find((c) => !voted.has(c.id));
  const remaining = deck.data ? deck.data.cards.filter((c) => !voted.has(c.id)).length : 0;
  const busy = vote.isPending || undo.isPending;

  const cast = (value: VoteValue) => {
    if (!card || busy) return;
    undo.reset();
    vote.mutate(
      { id: card.id, value },
      {
        onSuccess: (idea) => {
          setVoted((prev) => new Set(prev).add(card.id));
          if (isMatch(idea)) onMatch(idea);
        },
      },
    );
  };

  const takeBack = () => {
    if (busy) return;
    vote.reset();
    undo.mutate(undefined, {
      onSuccess: (idea) => {
        if (!idea) return;
        setVoted((prev) => {
          const next = new Set(prev);
          next.delete(idea.id);
          return next;
        });
      },
    });
  };

  useEffect(() => {
    if (paused) return;
    const onKey = (e: KeyboardEvent) => {
      const value = KEY_VOTES[e.key];
      if (!value || e.defaultPrevented || e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
      const target = e.target as HTMLElement | null;
      if (target?.closest('input,textarea,select,[contenteditable],[role="tab"]')) return;
      e.preventDefault();
      cast(value);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });

  if (deck.isPending) return <Skeleton label="Loading the deck" />;
  if (deck.isError) return <LoadError error={deck.error} onRetry={() => void deck.refetch()} />;

  if (!deck.data.partner_joined) {
    return (
      <EmptyState
        icon={<UsIcon />}
        title="Swipe on ideas together"
        action={
          emailMode ? (
            <ButtonLink to="/us#invite" variant="secondary">
              Invite your partner to swipe together
            </ButtonLink>
          ) : (
            <Button variant="secondary">Invite your partner to swipe together</Button>
          )
        }
      >
        Once your partner joins, you&apos;ll each swipe on date ideas and see where you match.
      </EmptyState>
    );
  }

  return (
    <div className="flex flex-col gap-4 pt-2">
      {card ? (
        <>
          <p className="px-2 text-sm text-muted">
            {remaining === 1 ? 'Last one for now.' : `${remaining} ideas waiting for your verdict.`}
          </p>
          <SwipeCard key={card.id} idea={card} draggable={!reducedMotion && !busy} onVote={cast} />
          <div className="flex flex-wrap justify-center gap-3">
            <Button variant="secondary" disabled={busy} onClick={() => cast('no')}>
              Nope
            </Button>
            <Button variant="secondary" disabled={busy} onClick={() => cast('maybe')}>
              Maybe
            </Button>
            <Button disabled={busy} onClick={() => cast('yes')}>
              Yes please
            </Button>
          </div>
          <p className="text-center text-sm text-muted">
            {reducedMotion ? 'Use the buttons' : 'Swipe the card, use the buttons'}, or press ← for
            nope, → for yes, ↑ for maybe.
          </p>
        </>
      ) : (
        <EmptyState
          icon={<IdeasIcon />}
          title="You're all caught up"
          action={<Button onClick={onGenerate}>Generate ideas</Button>}
        >
          Nothing left to swipe on. Check back soon, and we&apos;ll have more sparks for you and{' '}
          {partnerName}.
        </EmptyState>
      )}
      <div className="flex flex-col items-center gap-2">
        <Button variant="secondary" disabled={busy} onClick={takeBack}>
          Undo
        </Button>
        {vote.isError ? (
          <p role="alert" className="rounded-xl border border-coral p-3 text-sm">
            That swipe didn&apos;t go through. Try again.
          </p>
        ) : null}
        {undo.isSuccess && undo.data === null ? (
          <p role="status" className="rounded-xl border border-line p-3 text-sm">
            {NOTHING_TO_UNDO}
          </p>
        ) : null}
        {undo.isError ? (
          <p role="alert" className="rounded-xl border border-coral p-3 text-sm">
            We couldn&apos;t undo that. Try again in a moment.
          </p>
        ) : null}
      </div>
    </div>
  );
}
