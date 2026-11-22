import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router';
import { Button } from './Button';
import { Card } from './EmptyState';
import { ProfileError } from './ProfileChoice';
import { useCouple } from '../hooks/useCouple';
import { useEmailMode, useLeave } from '../hooks/usePairing';

/** Email-login mode only: leave the shared space, with a two-step inline confirm. */
export function LeaveCouple() {
  // A separate body, so NO_LOGIN mode never even loads the couple for this card.
  return useEmailMode() ? <LeaveCoupleBody /> : null;
}

function LeaveCoupleBody() {
  const couple = useCouple();
  const navigate = useNavigate();
  const leave = useLeave();
  const [confirming, setConfirming] = useState(false);
  const confirmRef = useRef<HTMLButtonElement>(null);
  const openerRef = useRef<HTMLButtonElement>(null);
  const wasConfirming = useRef(false);

  useEffect(() => {
    if (confirming) confirmRef.current?.focus();
    // Cancelling hands focus back to the button that opened the confirmation.
    else if (wasConfirming.current) openerRef.current?.focus();
    wasConfirming.current = confirming;
  }, [confirming]);

  if (couple.data?.partner_joined !== true) return null;

  const remove = async () => {
    try {
      await leave.mutateAsync();
    } catch {
      return; // leave.error is shown inside the confirmation
    }
    // The couple refetch can unmount this card before a per-call callback would run.
    setConfirming(false);
    await navigate('/');
  };

  return (
    <Card title="Our shared space">
      <div className="flex flex-col gap-4 p-2">
        {confirming ? (
          <div
            role="alertdialog"
            aria-labelledby="leave-confirm-title"
            aria-describedby="leave-confirm-text"
            className="flex flex-col gap-3 rounded-2xl border border-coral p-3"
          >
            <p id="leave-confirm-title" className="font-semibold">
              Leave our shared space?
            </p>
            <p id="leave-confirm-text" className="text-muted">
              You&apos;ll each get your own space back with your own profile. Shared ideas, dates
              and chats are deleted after 30 days.
            </p>
            <div className="flex flex-wrap gap-3">
              <Button
                ref={confirmRef}
                variant="danger"
                onClick={() => void remove()}
                disabled={leave.isPending}
              >
                {leave.isPending ? 'Working…' : 'Yes, leave'}
              </Button>
              <Button
                variant="secondary"
                onClick={() => {
                  setConfirming(false);
                  leave.reset();
                }}
                disabled={leave.isPending}
              >
                Cancel
              </Button>
            </div>
            <ProfileError error={leave.error} />
          </div>
        ) : (
          <div>
            <Button
              ref={openerRef}
              variant="secondary"
              onClick={() => {
                leave.reset();
                setConfirming(true);
              }}
            >
              Leave our shared space
            </Button>
          </div>
        )}
      </div>
    </Card>
  );
}
