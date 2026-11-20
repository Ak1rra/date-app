import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { CSRF_COOKIE, CSRF_HEADER } from '../api/http';
import { jsonResponse, renderApp, signedIn } from '../test/renderApp';

const signedOut = { authenticated: false, user: null };
const wifeSession = {
  authenticated: true,
  user: { ...signedIn.user!, role: 'partner' },
};

function mockApi(
  noLogin: boolean,
  choose?: () => Response | Promise<Response>,
  meta?: () => Response | Promise<Response>,
) {
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const url = String(input);
    if (url.endsWith('/api/v1/meta')) {
      if (meta) return meta();
      return jsonResponse({ demo_mode: false, version: 'test', no_login: noLogin });
    }
    if (url.endsWith('/api/v1/auth/choose-profile')) return choose!();
    return jsonResponse(signedOut);
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
  document.cookie = `${CSRF_COOKIE}=; max-age=0`;
});

describe('SignInPage (no-login profile picker)', () => {
  it('shows Husband and Wife; tapping Wife posts the profile with CSRF and lands on /', async () => {
    document.cookie = `${CSRF_COOKIE}=tok123`;
    const fetchMock = mockApi(true, () => jsonResponse(wifeSession));
    const router = renderApp('/signin');

    expect(await screen.findByRole('button', { name: 'Husband' })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Wife' }));

    expect(await screen.findByRole('link', { name: 'Plan a date' })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe('/');

    const call = fetchMock.mock.calls.find(([u]) => String(u).endsWith('/auth/choose-profile'))!;
    expect(call[1]?.method).toBe('POST');
    expect(call[1]?.body).toBe(JSON.stringify({ profile: 'wife' }));
    expect(new Headers(call[1]?.headers).get(CSRF_HEADER)).toBe('tok123');
  });

  it('disables both buttons and shows Opening… on the tapped one while pending', async () => {
    let release!: (r: Response) => void;
    mockApi(true, () => new Promise<Response>((resolve) => (release = resolve)));
    renderApp('/signin');

    await userEvent.click(await screen.findByRole('button', { name: 'Husband' }));
    expect(await screen.findByRole('button', { name: 'Opening…' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Wife' })).toBeDisabled();
    release(jsonResponse(wifeSession));
  });

  it('shows the error inline and stays on /signin', async () => {
    mockApi(true, () =>
      jsonResponse({ error: { code: 'forbidden', message: 'Pick a profile again.' } }, 403),
    );
    const router = renderApp('/signin');

    await userEvent.click(await screen.findByRole('button', { name: 'Wife' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Pick a profile again.');
    expect(router.state.location.pathname).toBe('/signin');
    expect(screen.getByRole('button', { name: 'Wife' })).toBeEnabled();
  });

  it('keeps the placeholder, not the buttons, when no_login is off', async () => {
    mockApi(false);
    renderApp('/signin');

    expect(
      await screen.findByText('Sign-in with an emailed magic link is coming soon.'),
    ).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Husband' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Wife' })).not.toBeInTheDocument();
  });

  it('reports a /meta server error with an alert, a retry, and neither buttons nor placeholder', async () => {
    let fail = true;
    const fetchMock = mockApi(true, undefined, () =>
      fail
        ? jsonResponse({ error: { code: 'internal', message: 'Boom' } }, 500)
        : jsonResponse({ demo_mode: false, version: 'test', no_login: true }),
    );
    renderApp('/signin');

    expect(await screen.findByRole('alert')).toHaveTextContent('Something went wrong on our side.');
    expect(
      screen.getByRole('heading', { name: "Can't open Date Planner right now" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Husband' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Wife' })).not.toBeInTheDocument();
    expect(
      screen.queryByText('Sign-in with an emailed magic link is coming soon.'),
    ).not.toBeInTheDocument();

    const metaCalls = () =>
      fetchMock.mock.calls.filter(([u]) => String(u).endsWith('/api/v1/meta')).length;
    const before = metaCalls();
    fail = false;
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(await screen.findByRole('button', { name: 'Husband' })).toBeInTheDocument();
    expect(metaCalls()).toBeGreaterThan(before);
  });

  it('reports a /meta network failure as a connection problem', async () => {
    mockApi(true, undefined, () => {
      throw new TypeError('Failed to fetch');
    });
    renderApp('/signin');

    expect(await screen.findByRole('alert')).toHaveTextContent(
      "We couldn't reach the server. Check your connection.",
    );
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
  });
});
