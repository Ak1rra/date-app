const KEY = 'dateapp.setupSkipped';

/** True when this device's user chose "Skip for now" on the home-area step. Storage failures read as not skipped. */
export function isSetupSkipped(): boolean {
  try {
    return window.localStorage.getItem(KEY) === '1';
  } catch {
    return false;
  }
}

export function setSetupSkipped(skipped: boolean): void {
  try {
    if (skipped) window.localStorage.setItem(KEY, '1');
    else window.localStorage.removeItem(KEY);
  } catch {
    // Storage is blocked; setup will simply be offered again next time.
  }
}
