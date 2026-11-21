import { useRef, useState, type PointerEvent } from 'react';
import type { IdeaOut, VoteValue } from '../../hooks/useIdeas';
import { IdeaDetails } from './IdeaDetails';

/** How far (px) a drag has to travel before it counts as a vote. */
export const SWIPE_THRESHOLD = 80;

/** The vote a drag of (dx, dy) would cast, or null while it is still under the threshold. */
export function voteForDrag(dx: number, dy: number): VoteValue | null {
  if (dy < -SWIPE_THRESHOLD && Math.abs(dy) > Math.abs(dx)) return 'maybe';
  if (dx > SWIPE_THRESHOLD) return 'yes';
  if (dx < -SWIPE_THRESHOLD) return 'no';
  return null;
}

const STAMPS: Record<VoteValue, { word: string; className: string }> = {
  yes: { word: 'Yes please', className: 'border-husband text-husband' },
  no: { word: 'Nope', className: 'border-coral text-ink' },
  maybe: { word: 'Maybe', className: 'border-accent text-accent' },
};

interface SwipeCardProps {
  idea: IdeaOut;
  /** False while a vote is in flight, or under reduced motion: then only buttons and keys work. */
  draggable: boolean;
  onVote: (value: VoteValue) => void;
}

/** One idea card that follows the pointer: right = yes, left = no, up = maybe. */
export function SwipeCard({ idea, draggable, onVote }: SwipeCardProps) {
  const start = useRef<{ x: number; y: number } | null>(null);
  const [drag, setDrag] = useState<{ dx: number; dy: number } | null>(null);
  const pending = drag ? voteForDrag(drag.dx, drag.dy) : null;

  const onPointerDown = (e: PointerEvent<HTMLDivElement>) => {
    if (!draggable || (e.target as HTMLElement).closest('a,button')) return;
    start.current = { x: e.clientX, y: e.clientY };
    e.currentTarget.setPointerCapture?.(e.pointerId);
    setDrag({ dx: 0, dy: 0 });
  };
  const onPointerMove = (e: PointerEvent<HTMLDivElement>) => {
    if (!start.current) return;
    setDrag({ dx: e.clientX - start.current.x, dy: e.clientY - start.current.y });
  };
  const finish = (e: PointerEvent<HTMLDivElement>, cast: boolean) => {
    if (!start.current) return;
    const value = cast
      ? voteForDrag(e.clientX - start.current.x, e.clientY - start.current.y)
      : null;
    start.current = null;
    setDrag(null);
    if (value) onVote(value);
  };

  const style = drag
    ? { transform: `translate(${drag.dx}px, ${drag.dy}px) rotate(${drag.dx / 20}deg)` }
    : undefined;

  return (
    <div
      data-testid="swipe-card"
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={(e) => finish(e, true)}
      onPointerCancel={(e) => finish(e, false)}
      style={style}
      className={`relative rounded-3xl border border-line bg-surface p-5 shadow-md select-none ${
        draggable ? 'cursor-grab touch-none' : ''
      } ${drag ? '' : 'motion-safe:transition-transform'}`}
    >
      {pending ? (
        <span
          aria-hidden="true"
          className={`absolute top-3 right-3 rounded-xl border-2 bg-surface px-3 py-1 text-sm font-bold uppercase ${STAMPS[pending].className}`}
        >
          {STAMPS[pending].word}
        </span>
      ) : null}
      <IdeaDetails idea={idea} />
    </div>
  );
}
