import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { couple, session } from '../test/fixtures';
import { mockApi, type Handler } from '../test/mockApi';
import { jsonResponse, renderApp } from '../test/renderApp';

const meta = (noLogin: boolean) => ({ demo_mode: false, version: 'test', no_login: noLogin });
const created = {
  link: 'https://dates.example/join#token=abc123',
  short_code: 'ABCD2345',
  expires_at: '2026-12-01T10:00:00Z',
};
const noInvite = () => jsonResponse({ error: { code: 'no_invite', message: 'No invite.' } }, 404);

function setup(routes: Record<string, Handler> = {}) {
  return mockApi({
    'GET /api/v1/auth/session': session('Sam'),
    'GET /api/v1/meta': meta(false),
    'GET /api/v1/couple': couple,
    'GET /api/v1/couple/invites/current': noInvite,
    ...routes,
  });
}

afterEach(() => {
  Reflect.deleteProperty(navigator, 'share');
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('InviteCard', () => {
  it('creates an invite and shows the link, the code and the expiry', async () => {
    const api = setup({ 'POST /api/v1/couple/invites': jsonResponse(created, 201) });
    const user = userEvent.setup();
    const writeText = vi.spyOn(navigator.clipboard, 'writeText').mockResolvedValue();
    renderApp('/us');

    await user.click(await screen.findByRole('button', { name: 'Invite your partner' }));

    expect(await screen.findByLabelText('Invite link')).toHaveValue(created.link);
    expect(screen.getByText('ABCD2345')).toBeInTheDocument();
    expect(screen.getByText('Or they can type this code')).toBeInTheDocument();
    expect(screen.getByText(/Expires/)).toBeInTheDocument();
    expect(api.find('POST', '/api/v1/couple/invites')).toHaveLength(1);

    await user.click(screen.getByRole('button', { name: 'Copy link' }));
    expect(writeText).toHaveBeenCalledWith(created.link);
    expect(await screen.findByText('Link copied.')).toBeInTheDocument();
  });

  it('only shows the code and expiry on a later visit, with a way to make a new link', async () => {
    setup({
      'GET /api/v1/couple/invites/current': {
        short_code: 'WXYZ6789',
        expires_at: '2026-12-01T10:00:00Z',
      },
    });
    renderApp('/us');

    expect(await screen.findByText('WXYZ6789')).toBeInTheDocument();
    expect(screen.queryByLabelText('Invite link')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Make a new link' })).toBeInTheDocument();
  });

  it('offers Share when the browser can share', async () => {
    setup({ 'POST /api/v1/couple/invites': jsonResponse(created, 201) });
    const share = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'share', { value: share, configurable: true });
    const user = userEvent.setup();
    renderApp('/us');

    await user.click(await screen.findByRole('button', { name: 'Invite your partner' }));
    await user.click(await screen.findByRole('button', { name: 'Share' }));
    expect(share).toHaveBeenCalledWith({ title: 'Join me on Date Planner', url: created.link });
  });

  it('selects the link when the clipboard is refused', async () => {
    setup({ 'POST /api/v1/couple/invites': jsonResponse(created, 201) });
    const user = userEvent.setup();
    vi.spyOn(navigator.clipboard, 'writeText').mockRejectedValue(new Error('denied'));
    renderApp('/us');

    await user.click(await screen.findByRole('button', { name: 'Invite your partner' }));
    const input = await screen.findByLabelText<HTMLInputElement>('Invite link');
    await user.click(screen.getByRole('button', { name: 'Copy link' }));

    await waitFor(() => expect(input.selectionEnd).toBe(created.link.length));
    expect(input.selectionStart).toBe(0);
    expect(screen.queryByText('Link copied.')).not.toBeInTheDocument();
  });

  it('revokes the invite', async () => {
    let active = true;
    const api = setup({
      'GET /api/v1/couple/invites/current': () =>
        active ? { short_code: 'WXYZ6789', expires_at: '2026-12-01T10:00:00Z' } : noInvite(),
      'DELETE /api/v1/couple/invites/current': () => {
        active = false;
        return new Response(null, { status: 204 });
      },
    });
    renderApp('/us');

    await userEvent.click(await screen.findByRole('button', { name: 'Stop sharing' }));

    expect(await screen.findByRole('button', { name: 'Invite your partner' })).toBeInTheDocument();
    expect(api.find('DELETE', '/api/v1/couple/invites/current')).toHaveLength(1);
    expect(screen.queryByText('WXYZ6789')).not.toBeInTheDocument();
  });

  it('disables make and stop while either request is in flight', async () => {
    let release!: () => void;
    const held = new Promise<void>((resolve) => (release = resolve));
    setup({
      'GET /api/v1/couple/invites/current': {
        short_code: 'WXYZ6789',
        expires_at: '2026-12-01T10:00:00Z',
      },
      'POST /api/v1/couple/invites': async () => {
        await held;
        return jsonResponse(created, 201);
      },
    });
    renderApp('/us');

    await userEvent.click(await screen.findByRole('button', { name: 'Make a new link' }));

    expect(screen.getByRole('button', { name: 'Stop sharing' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Working…' })).toBeDisabled();
    release();
    expect(await screen.findByLabelText('Invite link')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Stop sharing' })).toBeEnabled();
  });

  it('disables make while a stop is in flight', async () => {
    let release!: () => void;
    const held = new Promise<void>((resolve) => (release = resolve));
    setup({
      'GET /api/v1/couple/invites/current': {
        short_code: 'WXYZ6789',
        expires_at: '2026-12-01T10:00:00Z',
      },
      'DELETE /api/v1/couple/invites/current': async () => {
        await held;
        return new Response(null, { status: 204 });
      },
    });
    renderApp('/us');

    await userEvent.click(await screen.findByRole('button', { name: 'Stop sharing' }));

    expect(screen.getByRole('button', { name: 'Make a new link' })).toBeDisabled();
    release();
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Make a new link' })).toBeEnabled(),
    );
  });

  it('is hidden in NO_LOGIN mode and never calls the invite routes', async () => {
    const api = setup({ 'GET /api/v1/meta': meta(true) });
    renderApp('/us');

    await screen.findByRole('heading', { name: 'Us' });
    await waitFor(() => expect(api.find('GET', '/api/v1/couple').length).toBeGreaterThan(0));
    expect(screen.queryByRole('button', { name: 'Invite your partner' })).not.toBeInTheDocument();
    expect(api.calls.filter((c) => c.path.includes('/couple/invites'))).toHaveLength(0);
  });

  it('is hidden for the partner role', async () => {
    const api = setup({ 'GET /api/v1/auth/session': session('Sam', 'partner') });
    renderApp('/us');

    await screen.findByRole('heading', { name: 'Us' });
    await waitFor(() => expect(api.find('GET', '/api/v1/couple').length).toBeGreaterThan(0));
    expect(screen.queryByRole('button', { name: 'Invite your partner' })).not.toBeInTheDocument();
    expect(api.calls.filter((c) => c.path.includes('/couple/invites'))).toHaveLength(0);
  });

  it('is hidden once a partner has joined', async () => {
    const api = setup({ 'GET /api/v1/couple': { ...couple, partner_joined: true } });
    renderApp('/us');

    await screen.findByRole('heading', { name: 'Us' });
    await waitFor(() => expect(api.find('GET', '/api/v1/couple').length).toBeGreaterThan(0));
    expect(screen.queryByRole('button', { name: 'Invite your partner' })).not.toBeInTheDocument();
  });
});
