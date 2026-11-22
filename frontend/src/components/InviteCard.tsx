import { useRef, useState } from 'react';
import { Button } from './Button';
import { Card } from './EmptyState';
import { ProfileError } from './ProfileChoice';
import { Skeleton } from './forms';
import { useCouple } from '../hooks/useCouple';
import {
  useCreateInvite,
  useCurrentInvite,
  useEmailMode,
  useRevokeInvite,
} from '../hooks/usePairing';
import { useSession } from '../hooks/useSession';

const expiryFormatter = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' });

/**
 * Pairing for email-login mode: the owner makes a link and a short code for their partner. Only
 * shown to the owner, only while nobody has joined, and never in NO_LOGIN mode (the routes 404).
 */
export function InviteCard() {
  const emailMode = useEmailMode();
  const session = useSession();
  const couple = useCouple();
  const isOwner = session.data?.user?.role === 'owner';
  const open = emailMode && isOwner && couple.data?.partner_joined === false;
  const current = useCurrentInvite(open);
  const create = useCreateInvite();
  const revoke = useRevokeInvite();
  const linkRef = useRef<HTMLInputElement>(null);
  const [copied, setCopied] = useState(false);

  if (!open) return null;

  // Make and stop must not overlap: a late POST would resurrect an invite the person just stopped.
  const busy = create.isPending || revoke.isPending;

  const link = create.data?.link;
  const invite = create.data ?? current.data ?? null;
  const canShare = typeof navigator !== 'undefined' && typeof navigator.share === 'function';

  const copy = async () => {
    if (!link) return;
    try {
      await navigator.clipboard.writeText(link);
      setCopied(true);
    } catch {
      // No clipboard access (or it was refused): select the text so a long-press can copy it.
      setCopied(false);
      linkRef.current?.select();
    }
  };

  const share = () => {
    if (!link) return;
    // Rejects when the person dismisses the share sheet, which is not an error.
    navigator.share({ title: 'Join me on Date Planner', url: link }).catch(() => undefined);
  };

  const stop = () =>
    revoke.mutate(undefined, {
      onSuccess: () => {
        create.reset();
        setCopied(false);
      },
    });

  const make = () => {
    setCopied(false);
    create.mutate();
  };

  return (
    <div id="invite">
      <Card title="Invite your partner">
        <div className="flex flex-col gap-4 p-2">
          {current.isPending && !create.data ? (
            <Skeleton label="Checking for an invite" />
          ) : invite ? (
            <>
              {link ? (
                <div className="flex flex-col gap-2">
                  <label htmlFor="invite-link" className="text-sm font-semibold">
                    Invite link
                  </label>
                  <input
                    id="invite-link"
                    ref={linkRef}
                    readOnly
                    value={link}
                    onFocus={(e) => e.currentTarget.select()}
                    className="min-h-11 w-full rounded-xl border border-line bg-surface px-3 py-2 text-ink"
                  />
                  <div className="flex flex-wrap gap-3">
                    <Button onClick={() => void copy()}>Copy link</Button>
                    {canShare ? (
                      <Button variant="secondary" onClick={share}>
                        Share
                      </Button>
                    ) : null}
                  </div>
                  {copied ? (
                    <p role="status" className="text-sm text-muted">
                      Link copied.
                    </p>
                  ) : null}
                </div>
              ) : (
                <p className="text-muted">
                  Your partner has a link waiting. For safety we only show it once, right after you
                  make it.
                </p>
              )}
              <div className="flex flex-col gap-1">
                <p className="text-sm text-muted">
                  {link ? 'Or they can type this code' : 'Their code'}
                </p>
                <p className="text-3xl font-bold tracking-[0.3em] select-all">
                  {invite.short_code}
                </p>
              </div>
              <p className="text-sm text-muted">
                Expires{' '}
                <time dateTime={invite.expires_at}>
                  {expiryFormatter.format(new Date(invite.expires_at))}
                </time>
              </p>
              <div className="flex flex-wrap gap-3">
                {link ? null : (
                  <Button variant="secondary" onClick={make} disabled={busy}>
                    {create.isPending ? 'Working…' : 'Make a new link'}
                  </Button>
                )}
                <Button variant="secondary" onClick={stop} disabled={busy}>
                  {revoke.isPending ? 'Working…' : 'Stop sharing'}
                </Button>
              </div>
            </>
          ) : (
            <>
              <p className="text-muted">
                Planning is better together. Make a private link or short code and send it to your
                partner; once they join you&apos;ll share ideas, dates and chats.
              </p>
              <div>
                <Button onClick={make} disabled={busy}>
                  {create.isPending ? 'Working…' : 'Invite your partner'}
                </Button>
              </div>
            </>
          )}
          <ProfileError error={create.error ?? revoke.error ?? current.error} />
        </div>
      </Card>
    </div>
  );
}
