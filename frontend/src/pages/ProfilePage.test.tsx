import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { constraints, husbandProfile, wifeProfile } from '../test/fixtures';
import { mockApi as routeApi } from '../test/mockApi';
import { jsonResponse, renderApp, renderAppWithClient, signedIn } from '../test/renderApp';

const husband = {
  authenticated: true,
  user: { ...signedIn.user!, id: 'u-husband', role: 'owner' },
};
const wife = { authenticated: true, user: { ...signedIn.user!, id: 'u-wife', role: 'partner' } };

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

  it('drops every cached viewer-specific query on a switch, so private notes never carry over', async () => {
    let viewer: 'husband' | 'wife' = 'husband';
    const note = (text: string) => ({
      id: `n-${text}`,
      about_profile_id: 'p-wife',
      kind: 'note',
      text,
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
      archived_at: null,
    });
    const noAvailability = { windows: [], blocks: [] };
    routeApi({
      'GET /api/v1/auth/session': () => (viewer === 'husband' ? husband : wife),
      'GET /api/v1/meta': meta(true),
      'POST /api/v1/auth/choose-profile': () => {
        viewer = 'wife';
        return wife;
      },
      'GET /api/v1/profiles': () =>
        viewer === 'husband'
          ? [husbandProfile, wifeProfile]
          : [
              { ...husbandProfile, is_me: false, can_edit: false },
              { ...wifeProfile, is_me: true, can_edit: true },
            ],
      'GET /api/v1/profiles/p-husband/availability': noAvailability,
      'GET /api/v1/profiles/p-wife/availability': noAvailability,
      'GET /api/v1/couple': { id: 'c1', timezone: 'UTC', status: 'active', members: [] },
      'GET /api/v1/couple/constraints': constraints,
      'GET /api/v1/couple/important-dates': [],
      'GET /api/v1/notes': () => [note(viewer === 'husband' ? 'Husband secret' : 'Wife secret')],
    });
    const { router, queryClient } = renderAppWithClient('/us');

    expect((await screen.findAllByText('Husband secret')).length).toBeGreaterThan(0);
    await router.navigate('/profile');
    await userEvent.click(await screen.findByRole('button', { name: 'Wife' }));
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Wife' })).toHaveAttribute('aria-pressed', 'true'),
    );

    const roots = queryClient
      .getQueryCache()
      .getAll()
      .map((q) => String(q.queryKey[0]));
    expect(roots.sort()).toEqual(['meta', 'session']);

    await router.navigate('/us');
    expect(screen.queryByText('Husband secret')).not.toBeInTheDocument();
    expect((await screen.findAllByText('Wife secret')).length).toBeGreaterThan(0);
    expect(screen.queryByText('Husband secret')).not.toBeInTheDocument();
  });

  it('does not let an in-flight session fetch from before the switch overwrite the new session', async () => {
    let calls = 0;
    let release!: () => void;
    const held = new Promise<void>((resolve) => (release = resolve));
    routeApi({
      'GET /api/v1/auth/session': async () => {
        calls += 1;
        if (calls === 2) {
          await held; // a slow refetch that started while the user was still Husband
          return husband;
        }
        return calls === 1 ? husband : wife;
      },
      'GET /api/v1/meta': meta(true),
      'POST /api/v1/auth/choose-profile': wife,
    });
    const { queryClient } = renderAppWithClient('/profile');

    await screen.findByText(/using Date Planner as/);
    void queryClient.invalidateQueries({ queryKey: ['session'] });
    await waitFor(() => expect(calls).toBe(2));
    await userEvent.click(await screen.findByRole('button', { name: 'Wife' }));
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Wife' })).toHaveAttribute('aria-pressed', 'true'),
    );
    release();
    await new Promise((resolve) => setTimeout(resolve, 50));

    expect(queryClient.getQueryData<typeof wife>(['session'])?.user?.role).toBe('partner');
    expect(screen.getByRole('button', { name: 'Wife' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('clears cached viewer data when another tab announces a profile switch', async () => {
    let viewer: 'husband' | 'wife' = 'husband';
    const note = (text: string) => ({
      id: `n-${text}`,
      about_profile_id: 'p-wife',
      kind: 'note',
      text,
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
      archived_at: null,
    });
    const noAvailability = { windows: [], blocks: [] };
    const husbandUser = { ...husband, user: { ...husband.user, id: 'u-husband' } };
    const wifeUser = { ...wife, user: { ...wife.user, id: 'u-wife' } };
    routeApi({
      'GET /api/v1/auth/session': () => (viewer === 'husband' ? husbandUser : wifeUser),
      'GET /api/v1/meta': meta(true),
      'GET /api/v1/profiles': [husbandProfile, wifeProfile],
      'GET /api/v1/profiles/p-husband/availability': noAvailability,
      'GET /api/v1/profiles/p-wife/availability': noAvailability,
      'GET /api/v1/couple': { id: 'c1', timezone: 'UTC', status: 'active', members: [] },
      'GET /api/v1/couple/constraints': constraints,
      'GET /api/v1/couple/important-dates': [],
      'GET /api/v1/notes': () => [note(viewer === 'husband' ? 'Husband secret' : 'Wife secret')],
    });
    renderAppWithClient('/us');
    expect((await screen.findAllByText('Husband secret')).length).toBeGreaterThan(0);

    // The cookie now says Wife, and the other tab tells us so.
    viewer = 'wife';
    const otherTab = new BroadcastChannel('dateapp-session');
    otherTab.postMessage({ type: 'profile-changed', userId: 'u-wife' });
    otherTab.close();

    expect((await screen.findAllByText('Wife secret')).length).toBeGreaterThan(0);
    expect(screen.queryByText('Husband secret')).not.toBeInTheDocument();
  });

  it('still resolves a pending /meta when another tab announces a switch', async () => {
    let release!: () => void;
    const held = new Promise<void>((resolve) => (release = resolve));
    routeApi({
      'GET /api/v1/auth/session': husband,
      'GET /api/v1/meta': async () => {
        await held;
        return meta(true);
      },
    });
    renderAppWithClient('/profile');

    await screen.findByText(/using Date Planner as/);
    const otherTab = new BroadcastChannel('dateapp-session');
    otherTab.postMessage({ type: 'profile-changed', userId: 'u-wife' });
    otherTab.close();
    await new Promise((resolve) => setTimeout(resolve, 50));
    release();

    expect(
      await screen.findByText('Anyone using this app can switch profiles.'),
    ).toBeInTheDocument();
  });
});
