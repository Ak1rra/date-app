import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { jsonResponse, renderApp, signedIn } from '../test/renderApp';

const husband = { authenticated: true, user: { ...signedIn.user!, role: 'owner' } };
const wife = { authenticated: true, user: { ...signedIn.user!, role: 'partner' } };

const meta = (noLogin: boolean) => ({ demo_mode: false, version: 'test', no_login: noLogin });

/** Routes /meta and choose-profile to their bodies; everything else is the session. */
function mockApi(session: unknown, opts: { noLogin?: boolean; choose?: Response } = {}) {
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const url = String(input);
    if (url.endsWith('/api/v1/meta')) return jsonResponse(meta(opts.noLogin ?? true));
    if (url.endsWith('/auth/choose-profile') && opts.choose) return opts.choose.clone();
    return jsonResponse(session);
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('ProfilePage', () => {
  it('shows the current role and name, and switches profile in place', async () => {
    const fetchMock = mockApi(husband, { choose: jsonResponse(wife) });
    const router = renderApp('/profile');

    const line = await screen.findByText(/using Date Planner as/);
    expect(line).toHaveTextContent("You're using Date Planner as Husband");
    expect(screen.getByText('Sam')).toBeInTheDocument();
    expect(
      await screen.findByText('Anyone using this app can switch profiles.'),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Husband' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: 'Wife' })).toHaveAttribute('aria-pressed', 'false');

    await userEvent.click(screen.getByRole('button', { name: 'Wife' }));

    expect(await screen.findByText(/using Date Planner as/)).toHaveTextContent(
      "You're using Date Planner as Wife",
    );
    expect(screen.getByRole('button', { name: 'Wife' })).toHaveAttribute('aria-pressed', 'true');
    expect(router.state.location.pathname).toBe('/profile');
    const call = fetchMock.mock.calls.find(([u]) => String(u).endsWith('/auth/choose-profile'))!;
    expect(call[1]?.method).toBe('POST');
    expect(call[1]?.body).toBe(JSON.stringify({ profile: 'wife' }));
  });

  it('previews each person with their own colours on the switcher', async () => {
    mockApi(husband);
    renderApp('/profile');

    expect(await screen.findByRole('button', { name: 'Husband' })).toHaveClass(
      'bg-husband-soft',
      'border-husband',
      'text-husband',
    );
    expect(screen.getByRole('button', { name: 'Wife' })).toHaveClass(
      'bg-wife-soft',
      'border-wife',
      'text-wife',
    );
  });

  it('shows an inline alert when switching fails', async () => {
    mockApi(husband, {
      choose: jsonResponse(
        { error: { code: 'not_found', message: 'Profile picking is off.' } },
        404,
      ),
    });
    renderApp('/profile');

    await userEvent.click(await screen.findByRole('button', { name: 'Wife' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Profile picking is off.');
    expect(screen.getByRole('button', { name: 'Husband' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('marks the Profile tab active in the bottom bar', async () => {
    mockApi(husband);
    renderApp('/profile');
    const tabs = await screen.findByRole('navigation', { name: 'Main tabs' });
    expect(within(tabs).getByRole('link', { name: 'Profile' })).toHaveAttribute(
      'aria-current',
      'page',
    );
  });

  it('shows who you are but no switcher when no_login is off', async () => {
    const fetchMock = mockApi(husband, { noLogin: false });
    renderApp('/profile');

    expect(await screen.findByText(/using Date Planner as/)).toHaveTextContent(
      "You're using Date Planner as Husband",
    );
    expect(screen.getByText('Sam')).toBeInTheDocument();
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([u]) => String(u).endsWith('/api/v1/meta'))).toBe(true),
    );
    expect(screen.queryByRole('button', { name: 'Husband' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Wife' })).not.toBeInTheDocument();
    expect(
      screen.queryByText('Anyone using this app can switch profiles.'),
    ).not.toBeInTheDocument();
  });
});
