import { Button } from '../../components/Button';
import { FormError } from '../../components/forms';
import { useConfirmProfile, type ProfileOut } from '../../hooks/useProfiles';
import { useViewerId } from '../../hooks/useSession';

/**
 * Shown on my own profile while my partner's hand-over is unconfirmed. Saving my own edits does
 * not confirm it, so the banner stays until I say "Looks right".
 */
export function HandoverBanner({
  profile,
  partnerName,
  onEdit,
}: {
  profile: ProfileOut;
  partnerName: string;
  onEdit: () => void;
}) {
  const viewerId = useViewerId();
  const confirm = useConfirmProfile(profile.id);
  const handedOver =
    profile.is_me &&
    !!profile.edited_by_user_id &&
    profile.edited_by_user_id !== viewerId &&
    !profile.confirmed_by_subject_at;
  if (!handedOver) return null;
  const who = partnerName.charAt(0).toUpperCase() + partnerName.slice(1);
  return (
    <div
      role="region"
      aria-label="Profile filled in by your partner"
      className="flex flex-col gap-3 rounded-2xl border-2 border-accent bg-accent-soft p-4"
    >
      <p className="font-semibold">{who} filled this in for you. Look it over and make it yours.</p>
      <FormError error={confirm.error} />
      <div className="flex flex-wrap gap-2">
        <Button onClick={() => confirm.mutate()} disabled={confirm.isPending}>
          {confirm.isPending ? 'Saving…' : 'Looks right'}
        </Button>
        <Button variant="secondary" onClick={onEdit} disabled={confirm.isPending}>
          Edit
        </Button>
      </div>
    </div>
  );
}
