import { fireEvent, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { mockApi, type Handler } from '../test/mockApi';
import { jsonResponse, renderApp } from '../test/renderApp';

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

const templates = [
  {
    id: 't1',
    name: 'Classic night',
    created_at: '2026-11-20T00:00:00Z',
    source_itinerary_id: 'i1',
    title: 'Dinner and a movie',
    stop_titles: ['Dinner', 'Movie'],
  },
  {
    id: 't2',
    name: 'Picnic',
    created_at: '2026-11-21T00:00:00Z',
    source_itinerary_id: null,
    title: 'Park picnic',
    stop_titles: ['Picnic'],
  },
];

function setup(extra: Record<string, Handler> = {}) {
  return mockApi({
    'GET /api/v1/auth/session': session,
    'GET /api/v1/templates': templates,
    'GET /api/v1/itineraries': [],
    'GET /api/v1/itineraries/new1': { id: 'new1', title: 'Again' },
    ...extra,
  });
}

afterEach(() => vi.restoreAllMocks());

describe('TemplatesPage', () => {
  it('lists templates with their title and stops', async () => {
    setup();
    renderApp('/templates');
    expect(await screen.findByRole('heading', { name: 'Classic night' })).toBeInTheDocument();
    expect(screen.getByText('Dinner and a movie')).toBeInTheDocument();
    expect(screen.getByText('Movie')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Picnic' })).toBeInTheDocument();
  });

  it('is reachable from the itineraries page', async () => {
    setup();
    const router = renderApp('/itineraries');
    await userEvent.click(await screen.findByRole('link', { name: 'Templates' }));
    expect(router.state.location.pathname).toBe('/templates');
  });

  it('teaches the empty state', async () => {
    setup({ 'GET /api/v1/templates': [] });
    renderApp('/templates');
    expect(await screen.findByText('No templates yet')).toBeInTheDocument();
  });

  it('shows a retry when loading fails', async () => {
    setup({
      'GET /api/v1/templates': jsonResponse({ error: { code: 'x', message: 'boom' } }, 500),
    });
    renderApp('/templates');
    expect(await screen.findByRole('alert')).toHaveTextContent(/went wrong on our side/);
  });

  it('replans a template at a chosen time and opens the new itinerary', async () => {
    const api = setup({
      'POST /api/v1/templates/t1/replan': jsonResponse({ id: 'new1' }, 201),
    });
    const router = renderApp('/templates');
    await userEvent.click(await screen.findByRole('button', { name: 'Replan Classic night' }));
    const submit = screen.getByRole('button', { name: 'Plan this date' });
    expect(submit).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Starts'), { target: { value: '2026-12-12T19:00' } });
    await userEvent.click(submit);

    await waitFor(() => expect(router.state.location.pathname).toBe('/itineraries/new1'));
    expect(api.body('POST', '/api/v1/templates/t1/replan')).toEqual({
      starts_at: new Date('2026-12-12T19:00').toISOString(),
      ends_at: null,
    });
  });

  it("shows the server's message when the planner is unavailable", async () => {
    setup({
      'POST /api/v1/templates/t1/replan': jsonResponse(
        { error: { code: 'ai_unavailable', message: 'The planner is resting.' } },
        503,
      ),
    });
    renderApp('/templates');
    await userEvent.click(await screen.findByRole('button', { name: 'Replan Classic night' }));
    fireEvent.change(screen.getByLabelText('Starts'), { target: { value: '2026-12-12T19:00' } });
    fireEvent.change(screen.getByLabelText('Ends (optional)'), {
      target: { value: '2026-12-12T18:00' },
    });
    expect(screen.getByRole('button', { name: 'Plan this date' })).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Ends (optional)'), {
      target: { value: '2026-12-12T22:00' },
    });
    await userEvent.click(screen.getByRole('button', { name: 'Plan this date' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('The planner is resting.');
  });

  it('deletes only after confirming', async () => {
    let remaining = templates;
    const api = setup({
      'GET /api/v1/templates': () => remaining,
      'DELETE /api/v1/templates/t2': () => {
        remaining = templates.slice(0, 1);
        return new Response(null, { status: 204 });
      },
    });
    renderApp('/templates');
    await userEvent.click(await screen.findByRole('button', { name: 'Delete Picnic' }));
    await userEvent.click(screen.getByRole('button', { name: 'Keep it' }));
    expect(api.find('DELETE', '/api/v1/templates/t2')).toHaveLength(0);

    await userEvent.click(screen.getByRole('button', { name: 'Delete Picnic' }));
    await userEvent.click(screen.getByRole('button', { name: 'Yes, delete it' }));
    await waitFor(() => expect(api.find('DELETE', '/api/v1/templates/t2')).toHaveLength(1));
    await waitFor(() =>
      expect(screen.queryByRole('heading', { name: 'Picnic' })).not.toBeInTheDocument(),
    );
  });
});
