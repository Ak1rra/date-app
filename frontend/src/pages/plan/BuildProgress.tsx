import { useEffect, useState } from 'react';

const PLAN_STAGES = [
  'Finding places near you…',
  'Checking the weather…',
  'Working out travel times…',
] as const;

/** Stage labels for the other slow AI steps (shared so sheets and pages word them the same). */
export const IDEA_STAGES = [
  'Finding spots…',
  "Checking what's on…",
  'Writing your ideas…',
] as const;

export const DATE_STAGES = [
  'Checking venues…',
  'Working out the timing…',
  'Polishing the plan…',
] as const;

const STAGE_MS = 3500;

/** Staged, polite progress text for the slow AI steps; it keeps the last stage once it gets there. */
export function BuildProgress({
  label,
  stages = PLAN_STAGES,
}: {
  label: string;
  stages?: readonly string[];
}) {
  const [stage, setStage] = useState(0);
  useEffect(() => {
    const timer = setInterval(() => setStage((s) => Math.min(s + 1, stages.length - 1)), STAGE_MS);
    return () => clearInterval(timer);
  }, [stages.length]);
  return (
    <div className="flex flex-col gap-1 rounded-3xl border border-line bg-accent-soft p-4">
      <p className="font-semibold">{label}</p>
      <p role="status" aria-live="polite" className="text-muted">
        {stages[stage]}
      </p>
    </div>
  );
}
