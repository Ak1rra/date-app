import type { ReactNode } from 'react';
import { ApiError } from '../api/http';
import type { Profile } from '../hooks/useChooseProfile';

export const PROFILES: { value: Profile; label: string }[] = [
  { value: 'husband', label: 'Husband' },
  { value: 'wife', label: 'Wife' },
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
