// Control characters (C0, DEL, C1) are never part of a legitimate in-app path.
// eslint-disable-next-line no-control-regex
const CONTROL_CHARS = /[\u0000-\u001f\u007f-\u009f]/;

/**
 * Only same-app relative paths are allowed; anything else opens the home screen. Backslashes are
 * rejected outright because URL parsing treats them as slashes ("/\evil.com" becomes
 * "https://evil.com/"). The result is rebuilt from the parsed URL, never the raw string.
 */
export function safeUrl(raw: unknown, origin: string = self.location.origin): string {
  if (typeof raw !== 'string' || raw.includes('\\') || CONTROL_CHARS.test(raw)) return '/';
  if (!raw.startsWith('/') || raw.startsWith('//')) return '/';
  try {
    const url = new URL(raw, origin);
    if (url.origin !== origin) return '/';
    return url.pathname + url.search + url.hash;
  } catch {
    return '/';
  }
}
