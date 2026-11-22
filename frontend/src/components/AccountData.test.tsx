import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { mockApi } from '../test/mockApi';
import { getActiveProfile, setActiveProfile } from '../api/http';
import { jsonResponse, renderApp, renderAppWithClient } from '../test/renderApp';

const session = {
  authenticated: true,
  user: {
    id: 'u1',
    email: 'me@example.com',
    display_name: 'Sam',
    timezone: 'UTC',
    couple_id: 'c1',
    role: 'owner',
  },
};
const signedOut = { authenticated: false, user: null };
const meta = (noLogin: boolean) => ({ demo_mode: false, version: 'test', no_login: noLogin });

const exported = {
  exported_at: '2026-11-22T10:00:00Z',
  user: { id: 'u1', email: 'me@example.com' },
  votes: [],
};

/** Full URLs of the DELETE /me calls (mockApi's call log drops the query string). */
function deleteUrls(api: ReturnType<typeof mockApi>) {
  return api.fetchMock.mock.calls
    .filter(
      ([input, init]) => (input instanceof Request ? input.method : init?.method) === 'DELETE',
    )
    .map(([input]) => new URL(input instanceof Request ? input.url : String(input), 'http://x'));
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('AccountData', () => {
  it('downloads the export as a dated JSON file', async () => {
    const api = mockApi({
      'GET /api/v1/auth/session': session,
      'GET /api/v1/meta': meta(true),
      'GET /api/v1/me/export': exported,
    });
    const createObjectURL = vi.fn(() => 'blob:export');
    const revokeObjectURL = vi.fn();
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL, revokeObjectURL }));
    const downloads: string[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      downloads.push(`${this.download} ${this.href}`);
    });

    renderApp('/profile');
    await userEvent.click(await screen.findByRole('button', { name: 'Download my data' }));

    // Revoked after a short delay, so iOS Safari doesn't cancel the download.
    await waitFor(() => expect(revokeObjectURL).toHaveBeenCalledWith('blob:export'), {
      timeout: 3000,
    });
    expect(api.find('GET', '/api/v1/me/export')).toHaveLength(1);
    const blob = (createObjectURL.mock.calls[0] as unknown[])[0] as Blob;
    expect(blob.type).toBe('application/json');
    expect(downloads).toHaveLength(1);
    expect(downloads[0]).toMatch(/^dateapp-export-\d{4}-\d{2}-\d{2}\.json blob:export$/);
  });

  it('shows an error when the export fails', async () => {
    mockApi({
      'GET /api/v1/auth/session': session,
      'GET /api/v1/meta': meta(true),
      'GET /api/v1/me/export': jsonResponse(
        { error: { code: 'server_error', message: 'The export failed.' } },
        500,
      ),
    });
    renderApp('/profile');
    await userEvent.click(await screen.findByRole('button', { name: 'Download my data' }));
    expect(await screen.findByText('The export failed.')).toBeInTheDocument();
  });

  it('offers a reset in NO_LOGIN mode and only sends it after a second click', async () => {
    const api = mockApi({
      'GET /api/v1/auth/session': session,
      'GET /api/v1/meta': meta(true),
      'DELETE /api/v1/me': { mode: 'reset' },
    });
    renderApp('/profile');

    await userEvent.click(await screen.findByRole('button', { name: "Reset this profile's data" }));
    const dialog = await screen.findByRole('alertdialog');
    expect(dialog).toHaveTextContent(/Shared ideas and dates stay/);
    expect(screen.getByRole('button', { name: 'Yes, reset' })).toHaveFocus();

    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
    expect(api.find('DELETE', '/api/v1/me')).toHaveLength(0);

    await userEvent.click(screen.getByRole('button', { name: "Reset this profile's data" }));
    await userEvent.click(await screen.findByRole('button', { name: 'Yes, reset' }));
    expect(await screen.findByRole('status')).toHaveTextContent("This profile's data was reset.");
    expect(api.find('DELETE', '/api/v1/me')).toHaveLength(1);
    // The request names the profile that was on screen when the dialog opened.
    expect(deleteUrls(api).map((url) => url.searchParams.get('user_id'))).toEqual(['u1']);
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
  });

  it('signs out and goes to /signin after an account is deleted', async () => {
    let deleted = false;
    const api = mockApi({
      'GET /api/v1/auth/session': () => (deleted ? signedOut : session),
      'GET /api/v1/meta': meta(false),
      'DELETE /api/v1/me': () => {
        deleted = true;
        return { mode: 'deleted' };
      },
    });
    const router = renderApp('/profile');

    await userEvent.click(await screen.findByRole('button', { name: 'Delete my account' }));
    expect(await screen.findByRole('alertdialog')).toHaveTextContent(/within 30 days/);
    await userEvent.click(screen.getByRole('button', { name: 'Yes, delete' }));

    await waitFor(() => expect(router.state.location.pathname).toBe('/signin'));
    expect(api.find('DELETE', '/api/v1/me')).toHaveLength(1);
  });

  it('keeps the user on the page and explains when the delete fails', async () => {
    mockApi({
      'GET /api/v1/auth/session': session,
      'GET /api/v1/meta': meta(false),
      'DELETE /api/v1/me': jsonResponse(
        { error: { code: 'server_error', message: 'We could not delete your account.' } },
        500,
      ),
    });
    const router = renderApp('/profile');

    await userEvent.click(await screen.findByRole('button', { name: 'Delete my account' }));
    await userEvent.click(await screen.findByRole('button', { name: 'Yes, delete' }));

    expect(await screen.findByText('We could not delete your account.')).toBeInTheDocument();
    expect(router.state.location.pathname).toBe('/profile');
    expect(screen.getByRole('alertdialog')).toBeInTheDocument();
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  it('shows the server message and stays open when the profile changed under it (409)', async () => {
    mockApi({
      'GET /api/v1/auth/session': session,
      'GET /api/v1/meta': meta(true),
      'DELETE /api/v1/me': jsonResponse(
        {
          error: {
            code: 'profile_changed',
            message: "You switched profiles. Check which profile you're on and try again.",
          },
        },
        409,
      ),
    });
    renderApp('/profile');

    await userEvent.click(await screen.findByRole('button', { name: "Reset this profile's data" }));
    await userEvent.click(await screen.findByRole('button', { name: 'Yes, reset' }));

    expect(await screen.findByText(/You switched profiles/)).toBeInTheDocument();
    expect(screen.getByRole('alertdialog')).toBeInTheDocument();
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  it('still clears every cache when the page is left while the delete is in flight', async () => {
    let release: (() => void) | undefined;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    const deleteCache = vi.fn(async () => true);
    vi.stubGlobal('caches', { delete: deleteCache });
    setActiveProfile('u1');
    const api = mockApi({
      'GET /api/v1/auth/session': session,
      'GET /api/v1/meta': meta(false),
      'DELETE /api/v1/me': async () => {
        await held;
        return { mode: 'deleted' };
      },
    });
    const { router, queryClient } = renderAppWithClient('/profile');

    await userEvent.click(await screen.findByRole('button', { name: 'Delete my account' }));
    await userEvent.click(await screen.findByRole('button', { name: 'Yes, delete' }));
    await waitFor(() => expect(deleteUrls(api)).toHaveLength(1));
    queryClient.setQueryData(['viewer-data'], { secret: true });
    await router.navigate('/privacy'); // the card unmounts with the request still pending
    await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument());
    release?.();

    await waitFor(() => expect(getActiveProfile()).toBeNull());
    expect(queryClient.getQueryData(['viewer-data'])).toBeUndefined();
    expect(deleteCache).toHaveBeenCalledWith('api-offline');
  });
});
