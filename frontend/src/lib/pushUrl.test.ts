import { describe, expect, it } from 'vitest';
import { safeUrl } from './pushUrl';

const ORIGIN = 'https://app.example.com';

describe('safeUrl', () => {
  it('keeps same-app paths, queries and hashes', () => {
    expect(safeUrl('/ok', ORIGIN)).toBe('/ok');
    expect(safeUrl('/plan/1?x=2#top', ORIGIN)).toBe('/plan/1?x=2#top');
  });

  it.each([
    '/\\evil.com',
    '//evil.com',
    'https://evil.com',
    '/x\u0085y',
    '/x\ny',
    '/x\u007fy',
    'javascript:alert(1)',
    'relative',
    '',
    42,
    null,
    undefined,
    { url: '/ok' },
  ])('falls back to home for %j', (raw) => {
    expect(safeUrl(raw, ORIGIN)).toBe('/');
  });
});
