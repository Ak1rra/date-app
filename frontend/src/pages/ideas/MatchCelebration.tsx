import { Button } from '../../components/Button';
import { Sheet } from '../../components/Sheet';

/**
 * "It's a match" overlay. The hearts only move under `motion-safe`, so with reduced motion
 * it is a plain static card. Escape or the button dismisses it.
 */
export function MatchCelebration({
  idea,
  onClose,
}: {
  idea: { title: string };
  onClose: () => void;
}) {
  return (
    <Sheet
      labelledBy="match-title"
      onClose={onClose}
      overlayClassName="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      className="flex w-full max-w-sm flex-col items-center gap-4 rounded-3xl border-2 border-accent bg-surface p-6 text-center shadow-lg"
    >
      <div aria-hidden="true" className="flex gap-3 text-4xl">
        <span className="motion-safe:animate-bounce">💘</span>
        <span className="motion-safe:animate-pulse">✨</span>
        <span className="motion-safe:animate-bounce">💘</span>
      </div>
      <h2 id="match-title" className="text-2xl font-bold">
        It&apos;s a match! 💘
      </h2>
      <p>
        You both said yes to <strong>{idea.title}</strong>. Someone&apos;s getting a date.
      </p>
      <Button onClick={onClose}>Keep swiping</Button>
    </Sheet>
  );
}
