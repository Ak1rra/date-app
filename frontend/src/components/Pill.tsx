import type { ReactNode } from 'react';

/** A small muted label, e.g. "Evergreen idea". */
export function Pill({ children }: { children: ReactNode }) {
  return (
    <span className="rounded-full border border-line bg-surface-2 px-3 py-1 text-sm font-semibold text-ink">
      {children}
    </span>
  );
}
