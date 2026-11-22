import { useEffect, useState, type FormEvent } from 'react';
import { Link } from 'react-router';
import { Button } from './Button';
import { Card } from './EmptyState';
import {
  useAddPushSubscription,
  useDeletePushSubscription,
  useNotificationSettings,
  usePushConfig,
  useUpdateNotificationSettings,
  type NotificationSettings,
} from '../hooks/useNotifications';
import { isIos, isStandalone } from '../lib/install';

/** Friendly names for the backend's notification kinds (KINDS in backend/app/schemas/notify.py). */
const KIND_LABELS: Record<string, string> = {
  reminder_day_before: 'Day-before reminder',
  reminder_hour_before: 'Hour-before reminder',
  checklist_due: 'Checklist nudges',
  match: "It's a match",
  ideas_added: 'New shared ideas',
  date_announced: 'New dates',
  date_rescheduled: 'Date moved',
  date_cancelled: 'Date cancelled',
  hint_released: 'Surprise hints',
  surprise_revealed: 'Surprise revealed',
  partner_reaction: 'Partner reactions',
  rate_prompt: 'Rate your date',
  plan_nudge: 'Weekly plan nudges',
};

function base64UrlToBytes(value: string): Uint8Array {
  const normalized = value.replace(/-/g, '+').replace(/_/g, '/');
  const padded = normalized.padEnd(Math.ceil(normalized.length / 4) * 4, '=');
  const binary = atob(padded);
  return Uint8Array.from(binary, (character) => character.charCodeAt(0));
}

function browserSupportsPush() {
  return (
    typeof navigator !== 'undefined' &&
    'serviceWorker' in navigator &&
    typeof PushManager !== 'undefined' &&
    typeof Notification !== 'undefined'
  );
}

function PushSettingsContent() {
  const supportsPush = browserSupportsPush();
  const config = usePushConfig();
  const addSubscription = useAddPushSubscription();
  const deleteSubscription = useDeletePushSubscription();
  const settings = useNotificationSettings();
  const updateSettings = useUpdateNotificationSettings();
  const [subscription, setSubscription] = useState<PushSubscription | null>(null);
  const [permission, setPermission] = useState<NotificationPermission | undefined>(
    typeof Notification === 'undefined' ? undefined : Notification.permission,
  );
  const [pushError, setPushError] = useState<string | null>(null);
  const [draft, setDraft] = useState<NotificationSettings | null>(null);
  const validSettings =
    settings.data && Object.prototype.hasOwnProperty.call(settings.data, 'kinds')
      ? settings.data
      : null;
  const form = draft ?? validSettings;

  useEffect(() => {
    if (!config.data?.public_key || !browserSupportsPush()) return;
    let cancelled = false;
    void navigator.serviceWorker.ready
      .then((registration) => registration.pushManager.getSubscription())
      .then((current) => {
        if (!cancelled) setSubscription(current);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [config.data?.public_key]);

  const enable = async () => {
    setPushError(null);
    let permissionRequestFailed = false;
    try {
      if (typeof Notification === 'undefined') return;
      let nextPermission = Notification.permission;
      if (Notification.permission === 'default') {
        permissionRequestFailed = true;
        nextPermission = await Notification.requestPermission();
      }
      permissionRequestFailed = false;
      setPermission(nextPermission);
      if (nextPermission !== 'granted') return;
      const registration = await navigator.serviceWorker.ready;
      const nextSubscription = await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: base64UrlToBytes(config.data!.public_key!) as unknown as BufferSource,
      });
      const json = nextSubscription.toJSON();
      if (!json.endpoint || !json.keys?.p256dh || !json.keys.auth) {
        throw new Error('The browser returned an incomplete push subscription.');
      }
      await addSubscription.mutateAsync({
        endpoint: json.endpoint,
        keys: { p256dh: json.keys.p256dh, auth: json.keys.auth },
      });
      setSubscription(nextSubscription);
    } catch (error) {
      setPushError(
        permissionRequestFailed
          ? "We couldn't ask for notification permission. Try again."
          : error instanceof Error
            ? error.message
            : "We couldn't ask for notification permission. Try again.",
      );
    }
  };

  const disable = async () => {
    if (!subscription) return;
    setPushError(null);
    try {
      const endpoint = subscription.endpoint;
      await subscription.unsubscribe();
      await deleteSubscription.mutateAsync(endpoint);
      setSubscription(null);
    } catch (error) {
      setPushError(
        error instanceof Error ? error.message : 'Notifications could not be turned off.',
      );
    }
  };

  const saveSettings = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!form) return;
    await updateSettings.mutateAsync(form);
  };

  const unsupported = isIos() && !isStandalone();
  const canPush = !!config.data?.public_key && supportsPush;

  return (
    <div className="flex flex-col gap-6">
      <Card title="Push notifications">
        <div className="flex flex-col gap-3 p-2">
          {unsupported ? (
            <>
              <p className="text-muted">Install the app to your Home Screen first.</p>
              <Link className="font-semibold text-accent underline" to="/install">
                Learn how to install it
              </Link>
            </>
          ) : !canPush ? (
            <p className="text-muted">
              Push notifications aren&apos;t available in this browser or are disabled for this app.
            </p>
          ) : permission === 'denied' ? (
            <p className="text-muted">
              Notifications are blocked. Re-enable them in your browser or device settings.
            </p>
          ) : subscription ? (
            <>
              <p className="text-muted">
                You&apos;ll get a nudge when something needs your attention.
              </p>
              <Button
                variant="secondary"
                onClick={() => void disable()}
                disabled={deleteSubscription.isPending}
              >
                Turn off
              </Button>
            </>
          ) : (
            <>
              <p className="text-muted">Get timely nudges about dates and plans.</p>
              <Button onClick={() => void enable()} disabled={addSubscription.isPending}>
                Enable notifications
              </Button>
            </>
          )}
          {pushError ? (
            <p role="alert" className="text-danger">
              {pushError}
            </p>
          ) : null}
        </div>
      </Card>
      <Card title="Notification settings">
        {settings.isPending || !form ? (
          <p className="p-2 text-muted">Loading notification settings…</p>
        ) : (
          <form className="flex flex-col gap-4 p-2" onSubmit={(event) => void saveSettings(event)}>
            <fieldset className="flex flex-col gap-2">
              <legend className="font-semibold">Nudges</legend>
              {Object.entries(form.kinds).map(([kind, enabled]) => (
                <label key={kind} className="flex min-h-11 items-center gap-3">
                  <input
                    type="checkbox"
                    checked={enabled}
                    onChange={(event) =>
                      setDraft((current) =>
                        current
                          ? {
                              ...current,
                              kinds: { ...current.kinds, [kind]: event.target.checked },
                            }
                          : current,
                      )
                    }
                  />
                  <span>{KIND_LABELS[kind] ?? kind.replaceAll('_', ' ')}</span>
                </label>
              ))}
            </fieldset>
            <div className="grid grid-cols-2 gap-3">
              <label className="flex flex-col gap-1 font-semibold">
                Quiet hours start
                <input
                  type="time"
                  value={form.quiet_start}
                  onChange={(event) => setDraft({ ...form, quiet_start: event.target.value })}
                  className="min-h-11 rounded-xl border border-line bg-surface px-3"
                />
              </label>
              <label className="flex flex-col gap-1 font-semibold">
                Quiet hours end
                <input
                  type="time"
                  value={form.quiet_end}
                  onChange={(event) => setDraft({ ...form, quiet_end: event.target.value })}
                  className="min-h-11 rounded-xl border border-line bg-surface px-3"
                />
              </label>
            </div>
            <Button type="submit" disabled={updateSettings.isPending}>
              {updateSettings.isPending ? 'Saving…' : 'Save settings'}
            </Button>
          </form>
        )}
      </Card>
    </div>
  );
}

export function PushSettings() {
  return <PushSettingsContent />;
}
