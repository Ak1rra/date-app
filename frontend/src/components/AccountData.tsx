import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router';
import { Button } from './Button';
import { Card } from './EmptyState';
import { ProfileError } from './ProfileChoice';
import { useDeleteMe, useExportData } from '../hooks/useAccount';
import { useMeta } from '../hooks/useMeta';
import { useViewerId } from '../hooks/useSession';

function pad(value: number) {
  return String(value).padStart(2, '0');
}

/** `dateapp-export-YYYY-MM-DD.json`, with the viewer's local date. */
function exportFilename(now = new Date()) {
  return `dateapp-export-${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}.json`;
}

function saveJson(data: unknown) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = exportFilename();
  document.body.appendChild(link);
  link.click();
  link.remove();
  // Revoking in the same tick can cancel the download in iOS Safari.
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function AccountData() {
  const meta = useMeta();
  const viewerId = useViewerId();
  const navigate = useNavigate();
  const exportData = useExportData();
  const deleteMe = useDeleteMe();
  // The profile the person was looking at when they opened the confirmation; null while closed.
  const [target, setTarget] = useState<string | null>(null);
  const [wasReset, setWasReset] = useState(false);
  const confirmRef = useRef<HTMLButtonElement>(null);
  const openerRef = useRef<HTMLButtonElement>(null);
  const wasConfirming = useRef(false);
  // The wording depends on the server mode, so the destructive action waits until it is known.
  const known = meta.data !== undefined;
  const noLogin = meta.data?.no_login === true;
  const confirming = target !== null;

  useEffect(() => {
    if (confirming) confirmRef.current?.focus();
    // Closing the confirmation (Cancel or a finished reset) hands focus back to the button that opened it.
    else if (wasConfirming.current) openerRef.current?.focus();
    wasConfirming.current = confirming;
  }, [confirming]);

  const download = () => {
    setWasReset(false);
    exportData.mutate(undefined, { onSuccess: saveJson });
  };

  const remove = () => {
    if (target === null) return;
    // Cache cleanup is in useDeleteMe, so it still runs if this component unmounts first.
    deleteMe.mutate(target, {
      onSuccess: async (result) => {
        setTarget(null);
        if (result.mode === 'deleted') await navigate('/signin');
        else setWasReset(true);
      },
    });
  };

  const label = noLogin ? "Reset this profile's data" : 'Delete my account';
  const confirmLabel = noLogin ? 'Yes, reset' : 'Yes, delete';
  const explanation = noLogin
    ? "This clears this profile's preferences, votes, ratings, notes, private ideas and " +
      "chats. Shared ideas and dates stay, and the other profile is not touched. This can't " +
      'be undone.'
    : 'You will be signed out, and your data will be erased within 30 days. Shared ideas and ' +
      "dates stay with your partner. This can't be undone.";

  return (
    <Card title="Your data">
      <div className="flex flex-col gap-4 p-2">
        <p className="text-muted">
          Download a copy of everything you&apos;ve added here as a JSON file. It never includes
          your partner&apos;s private answers.
        </p>
        <div>
          <Button variant="secondary" onClick={download} disabled={exportData.isPending}>
            {exportData.isPending ? 'Preparing…' : 'Download my data'}
          </Button>
        </div>
        <ProfileError error={exportData.error} />
        {known ? (
          confirming ? (
            <div
              role="alertdialog"
              aria-labelledby="account-confirm-title"
              aria-describedby="account-confirm-text"
              className="flex flex-col gap-3 rounded-2xl border border-coral p-3"
            >
              <p id="account-confirm-title" className="font-semibold">
                {noLogin ? "Reset this profile's data?" : 'Delete your account?'}
              </p>
              <p id="account-confirm-text" className="text-muted">
                {explanation}
              </p>
              <div className="flex flex-wrap gap-3">
                <Button
                  ref={confirmRef}
                  variant="danger"
                  onClick={remove}
                  disabled={deleteMe.isPending}
                >
                  {deleteMe.isPending ? 'Working…' : confirmLabel}
                </Button>
                <Button
                  variant="secondary"
                  onClick={() => {
                    setTarget(null);
                    deleteMe.reset();
                  }}
                  disabled={deleteMe.isPending}
                >
                  Cancel
                </Button>
              </div>
              <ProfileError error={deleteMe.error} />
            </div>
          ) : (
            <div className="flex flex-col gap-3">
              <div>
                <Button
                  ref={openerRef}
                  variant="secondary"
                  onClick={() => {
                    if (!viewerId) return;
                    setWasReset(false);
                    deleteMe.reset();
                    setTarget(viewerId);
                  }}
                >
                  {label}
                </Button>
              </div>
              {wasReset ? (
                <p role="status" className="text-muted">
                  This profile&apos;s data was reset.
                </p>
              ) : null}
            </div>
          )
        ) : null}
      </div>
    </Card>
  );
}
