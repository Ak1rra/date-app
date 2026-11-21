import { useState } from 'react';
import { Link } from 'react-router';
import { isIos, isStandalone } from '../lib/install';

const DISMISS_KEY = 'dateapp-install-banner-dismissed';

function wasDismissed(): boolean {
  try {
    return localStorage.getItem(DISMISS_KEY) === '1';
  } catch {
    return false;
  }
}

/** A one-line nudge, shown only in iOS Safari (not yet installed) until it is dismissed. */
export function InstallBanner() {
  const [dismissed, setDismissed] = useState(wasDismissed);
  if (dismissed || !isIos() || isStandalone()) return null;

  const dismiss = () => {
    try {
      localStorage.setItem(DISMISS_KEY, '1');
    } catch {
      // Storage unavailable: hide it for this visit only.
    }
    setDismissed(true);
  };

  return (
    <div className="flex items-center gap-3 border-b border-line bg-accent-soft px-4 py-2 text-sm">
      <p className="flex-1">
        <Link to="/install" className="font-semibold text-accent">
          Add me to your Home Screen for the full experience 😉
        </Link>
      </p>
      <button
        type="button"
        onClick={dismiss}
        aria-label="Dismiss install banner"
        className="min-h-11 min-w-11 rounded-full text-lg text-muted"
      >
        ×
      </button>
    </div>
  );
}
