import { Button } from '../../components/Button';
import { FormError } from '../../components/forms';
import {
  useAcceptSuggestion,
  useDeclineSuggestion,
  useSuggestions,
  type ProfileIn,
  type ProfileOut,
  type ProfileSuggestionOut,
} from '../../hooks/useProfiles';
import { FIELD_LABELS, formatField } from './ProfileFields';

/** The changed fields of a suggestion, in the form's order, as "Field: old → new". */
export function describeChanges(profile: ProfileOut, suggestion: ProfileSuggestionOut) {
  return (Object.keys(FIELD_LABELS) as (keyof ProfileIn)[])
    .filter((key) => key in suggestion.changes)
    .map((key) => ({
      key,
      text: `${FIELD_LABELS[key]}: ${formatField(key, profile[key])} → ${formatField(key, suggestion.changes[key])}`,
    }));
}

function SuggestionCard({
  profile,
  suggestion,
  partnerName,
}: {
  profile: ProfileOut;
  suggestion: ProfileSuggestionOut;
  partnerName: string;
}) {
  const accept = useAcceptSuggestion(profile.id);
  const decline = useDeclineSuggestion(profile.id);
  const busy = accept.isPending || decline.isPending;
  return (
    <li className="flex flex-col gap-3 rounded-2xl border border-line bg-surface p-3">
      <ul className="flex flex-col gap-1">
        {describeChanges(profile, suggestion).map((change) => (
          <li key={change.key}>{change.text}</li>
        ))}
      </ul>
      {suggestion.note ? (
        <p className="text-sm text-muted">
          {partnerName} says: &ldquo;{suggestion.note}&rdquo;
        </p>
      ) : null}
      <FormError error={accept.error ?? decline.error} />
      <div className="flex flex-wrap gap-2">
        <Button disabled={busy} onClick={() => accept.mutate(suggestion.id)}>
          Accept
        </Button>
        <Button variant="secondary" disabled={busy} onClick={() => decline.mutate(suggestion.id)}>
          Decline
        </Button>
      </div>
    </li>
  );
}

/** My own profile: the changes my partner suggested, for me to accept or decline. */
export function SuggestionsReview({
  profile,
  partnerName,
}: {
  profile: ProfileOut;
  partnerName: string;
}) {
  const suggestions = useSuggestions(profile.id, profile.is_me);
  const pending = suggestions.data?.filter((s) => s.status === 'pending') ?? [];
  if (!profile.is_me || !pending.length) return null;
  return (
    <section aria-labelledby={`suggestions-${profile.id}`} className="flex flex-col gap-3">
      <h3 id={`suggestions-${profile.id}`} className="text-lg font-semibold">
        Suggested changes from {partnerName}
      </h3>
      <ul className="flex flex-col gap-3">
        {pending.map((s) => (
          <SuggestionCard key={s.id} profile={profile} suggestion={s} partnerName={partnerName} />
        ))}
      </ul>
    </section>
  );
}
