import { useEffect, useState } from 'react';

const STAGES = [
  'Finding places near you…',
  'Checking the weather…',
  'Working out travel times…',
] as const;

const STAGE_MS = 3500;

/** Staged, polite progress text for the slow AI steps; it keeps the last stage once it gets there. */
export function BuildProgress({ label }: { label: string }) {
  const [stage, setStage] = useState(0);
  useEffect(() => {
    const timer = setInterval(() => setStage((s) => Math.min(s + 1, STAGES.length - 1)), STAGE_MS);
    return () => clearInterval(timer);
  }, []);
  return (
    <div className="flex flex-col gap-1 rounded-3xl border border-line bg-accent-soft p-4">
      <p className="font-semibold">{label}</p>
      <p role="status" aria-live="polite" className="text-muted">
        {STAGES[stage]}
      </p>
    </div>
  );
}
