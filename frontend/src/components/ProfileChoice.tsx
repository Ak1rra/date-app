import type { ReactNode } from 'react';
import { ApiError } from '../api/http';
import type { Profile } from '../hooks/useChooseProfile';

/** `tone` previews each person's colours; `variant` is the matching Button variant. */
export const PROFILES: {
  value: Profile;
  label: string;
  variant: 'husband' | 'wife';
  tone: string;
}[] = [
  {
    value: 'husband',
    label: 'Husband',
    variant: 'husband',
    tone: 'bg-husband-soft border-husband text-husband',
  },
  { value: 'wife', label: 'Wife', variant: 'wife', tone: 'bg-wife-soft border-wife text-wife' },
];

/** Friendly inline text for a failed choose-profile call. */
export function ProfileError({ error }: { error: unknown }): ReactNode {
  if (!error) return null;
  const message =
    error instanceof ApiError ? error.message : "Can't reach the server. Check your connection.";
  return (
    <p
      role="alert"
      className="rounded-2xl border border-coral bg-surface px-3 py-2 text-sm text-ink"
    >
      {message}
    </p>
  );
}
