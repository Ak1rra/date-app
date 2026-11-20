import { screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mockSession, renderApp, signedIn } from '../test/renderApp';

const owner = { authenticated: true, user: { ...signedIn.user!, role: 'owner' } };
const partner = { authenticated: true, user: { ...signedIn.user!, role: 'partner' } };

const themeColors = () =>
  [...document.querySelectorAll<HTMLMetaElement>('meta[name="theme-color"]')].map((m) => m.content);

beforeEach(() => {
  document.head.insertAdjacentHTML(
    'beforeend',
    '<meta name="theme-color" content="#FFF4EE" media="(prefers-color-scheme: light)">' +
      '<meta name="theme-color" content="#1F1221" media="(prefers-color-scheme: dark)">',
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  delete document.documentElement.dataset.person;
  document.querySelectorAll('meta[name="theme-color"]').forEach((m) => m.remove());
});

describe('usePersonTheme', () => {
  it('uses the blue palette and theme colours for the husband (owner)', async () => {
    mockSession(owner);
    renderApp('/');
    await waitFor(() => expect(document.documentElement.dataset.person).toBe('husband'));
    expect(themeColors()).toEqual(['#eef4ff', '#0f1726']);
  });

  it('marks the wife (partner) and keeps the default theme colours', async () => {
    mockSession(partner);
    renderApp('/');
    await waitFor(() => expect(document.documentElement.dataset.person).toBe('wife'));
    expect(themeColors()).toEqual(['#FFF4EE', '#1F1221']);
  });

  it('has no person attribute when signed out', async () => {
    document.documentElement.dataset.person = 'husband';
    mockSession({ authenticated: false, user: null });
    renderApp('/signin');
    await screen.findByRole('heading', { name: 'Sign in' });
    await waitFor(() => expect(document.documentElement.dataset.person).toBeUndefined());
    expect(themeColors()).toEqual(['#FFF4EE', '#1F1221']);
  });
});
