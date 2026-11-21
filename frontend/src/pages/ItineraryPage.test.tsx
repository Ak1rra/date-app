import { fireEvent, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { ItineraryOut } from '../hooks/useItineraries';
import { mockApi } from '../test/mockApi';
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

const itinerary: ItineraryOut = {
  id: 'i1',
  idea_id: null,
  planner_user_id: 'u1',
  is_planner: true,
  title: 'A very good night',
  starts_at: '2026-12-10T18:00:00Z',
  ends_at: '2026-12-10T22:00:00Z',
  status: 'draft',
  announced_at: null,
  invitation_message: null,
  is_surprise: false,
  reveal_at: null,
  revealed_at: null,
  seen_by_partner_at: null,
  budget: 120,
  est_total: 95,
  currency: 'USD',
  weather: { summary: 'Clear', hourly: [{ temp_c: 10 }] },
  backup_plan: 'Movie and snacks',
  bring_wear: 'Wear something cute',
  warnings: [],
  partner_reaction: null,
  created_at: '2026-11-20T00:00:00Z',
  updated_at: '2026-11-20T00:00:00Z',
  stops: [
    {
      id: 's1',
      position: 0,
      title: 'Dinner',
      description: 'A table for two',
      starts_at: '2026-12-10T18:00:00Z',
      ends_at: '2026-12-10T19:30:00Z',
      est_cost: 95,
      notes: null,
      place_id: null,
      reveal_policy: 'with_itinerary',
      booking_url: null,
      maps: { google: 'https://maps.google.com', apple: null, directions_from_previous: null },
      venue: { name: 'The Cozy Table', address: '1 Main St', rating: 4.8, price_level: 2 },
      event: null,
      warnings: [],
      travel_to_next: null,
    },
  ],
  checklist: [
    {
      id: 'c1',
      text: 'Reserve the table',
      kind: 'reserve',
      done: false,
      done_at: null,
      due_at: null,
      link: null,
      position: 0,
    },
  ],
  attributions: ['Weather data by Open-Meteo.com'],
};

function setup(data: ItineraryOut | Response = itinerary) {
  return mockApi({
    'GET /api/v1/auth/session': session,
    'GET /api/v1/itineraries/i1': data,
    'PATCH /api/v1/itineraries/i1/checklist/c1': { ...itinerary.checklist[0], done: true },
    'POST /api/v1/itineraries/i1/checklist': itinerary.checklist[0],
    'POST /api/v1/itineraries/i1/schedule': itinerary,
    'POST /api/v1/itineraries/i1/cancel': itinerary,
    'POST /api/v1/itineraries/i1/react': itinerary,
    'DELETE /api/v1/itineraries/i1/checklist/c1': new Response(null, { status: 204 }),
  });
}

afterEach(() => vi.restoreAllMocks());

describe('ItineraryPage', () => {
  it('shows planner actions and checklist editing', async () => {
    const api = setup();
    renderApp('/itineraries/i1');

    expect(await screen.findByRole('heading', { name: 'A very good night' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Schedule' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('checkbox', { name: 'Mark Reserve the table done' }));
    await waitFor(() =>
      expect(api.body('PATCH', '/api/v1/itineraries/i1/checklist/c1')).toEqual({ done: true }),
    );
    await userEvent.type(screen.getByLabelText('Add a finishing touch'), 'Buy flowers');
    await userEvent.click(screen.getByRole('button', { name: 'Add item' }));
    await waitFor(() =>
      expect(api.body('POST', '/api/v1/itineraries/i1/checklist')).toMatchObject({
        text: 'Buy flowers',
        kind: 'other',
      }),
    );
  });

  it('shows partner reaction controls instead of planner controls', async () => {
    setup({ ...itinerary, is_planner: false });
    renderApp('/itineraries/i1');
    expect(await screen.findByRole('button', { name: /I'm in/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Schedule' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Can we move it?' })).toBeInTheDocument();
  });

  it('submits schedule details from the sheet', async () => {
    const api = setup();
    renderApp('/itineraries/i1');
    await userEvent.click(await screen.findByRole('button', { name: 'Schedule' }));
    await userEvent.click(screen.getByRole('button', { name: 'Surprise' }));
    expect(
      screen.getByText(/Surprise — they won't see it until you reveal it/),
    ).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Schedule date' }));
    await waitFor(() =>
      expect(api.find('POST', '/api/v1/itineraries/i1/schedule')).toHaveLength(1),
    );
  });

  it('shows a friendly missing-date state', async () => {
    setup(jsonResponse({ error: { code: 'not_found', message: 'Not found' } }, 404));
    renderApp('/itineraries/i1');
    expect(await screen.findByText("That date isn't here")).toBeInTheDocument();
  });

  it('preselects Surprise when scheduling an existing surprise', async () => {
    const api = setup({ ...itinerary, is_surprise: true });
    renderApp('/itineraries/i1');
    await userEvent.click(await screen.findByRole('button', { name: 'Schedule' }));
    expect(screen.getByRole('button', { name: 'Surprise' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    await userEvent.click(screen.getByRole('button', { name: 'Schedule date' }));
    await waitFor(() =>
      expect(api.body('POST', '/api/v1/itineraries/i1/schedule')).toMatchObject({
        mode: 'surprise',
      }),
    );
  });

  it('shows weather temperatures in Fahrenheit', async () => {
    setup({ ...itinerary, weather: { summary: 'Clear, 10 to 20 C', hourly: [] } });
    renderApp('/itineraries/i1');
    expect(await screen.findByText(/Clear, 50 to 68°F/)).toBeInTheDocument();
    expect(screen.queryByText(/20 C/)).not.toBeInTheDocument();
  });

  it('leaves a no-forecast weather summary unchanged', async () => {
    setup({ ...itinerary, weather: { summary: 'Too far out for a forecast' } });
    renderApp('/itineraries/i1');
    expect(await screen.findByText(/Too far out for a forecast/)).toBeInTheDocument();
  });

  it('shows both dates for a window that crosses midnight, one otherwise', async () => {
    const full = (date: Date) =>
      new Intl.DateTimeFormat(undefined, { dateStyle: 'full' }).format(date);
    const start = new Date(2026, 11, 10, 23, 0);
    const end = new Date(2026, 11, 11, 1, 0);
    setup({ ...itinerary, starts_at: start.toISOString(), ends_at: end.toISOString() });
    renderApp('/itineraries/i1');
    const heading = await screen.findByRole('heading', { name: 'A very good night' });
    const window = heading.nextElementSibling as HTMLElement;
    expect(window.textContent).toContain(full(start));
    expect(window.textContent).toContain(full(end));
  });

  it('shows a single date for a same-day window', async () => {
    const full = (date: Date) =>
      new Intl.DateTimeFormat(undefined, { dateStyle: 'full' }).format(date);
    const start = new Date(2026, 11, 10, 18, 0);
    const end = new Date(2026, 11, 10, 22, 0);
    setup({ ...itinerary, starts_at: start.toISOString(), ends_at: end.toISOString() });
    renderApp('/itineraries/i1');
    const heading = await screen.findByRole('heading', { name: 'A very good night' });
    const text = (heading.nextElementSibling as HTMLElement).textContent ?? '';
    expect(text.split(full(start))).toHaveLength(2);
  });

  it('formats amounts in the itinerary currency', async () => {
    setup({ ...itinerary, currency: 'EUR' });
    renderApp('/itineraries/i1');
    const total = await screen.findByText(/Estimated total:/);
    expect(total.parentElement?.textContent).toMatch(/€|EUR/);
    expect(total.parentElement?.textContent).not.toContain('$');
  });

  it('formats USD amounts with a dollar sign', async () => {
    setup();
    renderApp('/itineraries/i1');
    const total = await screen.findByText(/Estimated total:/);
    expect(total.parentElement?.textContent).toContain('$95.00');
  });

  it('sends a move suggestion with a start and a note', async () => {
    const api = setup({ ...itinerary, is_planner: false });
    renderApp('/itineraries/i1');
    await userEvent.click(await screen.findByRole('button', { name: 'Can we move it?' }));
    fireEvent.change(screen.getByLabelText('Suggested start (optional)'), {
      target: { value: '2026-12-11T19:00' },
    });
    await userEvent.type(screen.getByLabelText('A note (optional)'), 'Later please');
    await userEvent.click(screen.getByRole('button', { name: 'Send suggestion' }));
    await waitFor(() =>
      expect(api.body('POST', '/api/v1/itineraries/i1/react')).toEqual({
        kind: 'move',
        suggested_start: new Date('2026-12-11T19:00').toISOString(),
        note: 'Later please',
      }),
    );
  });

  it('sends a heart reaction', async () => {
    const api = setup({ ...itinerary, is_planner: false });
    renderApp('/itineraries/i1');
    await userEvent.click(await screen.findByRole('button', { name: /I'm in/ }));
    await waitFor(() =>
      expect(api.body('POST', '/api/v1/itineraries/i1/react')).toEqual({ kind: 'heart' }),
    );
  });

  it('does not retry a 404 before showing the friendly state', async () => {
    const api = setup(jsonResponse({ error: { code: 'not_found', message: 'Not found' } }, 404));
    renderApp('/itineraries/i1');
    expect(await screen.findByText("That date isn't here")).toBeInTheDocument();
    expect(api.find('GET', '/api/v1/itineraries/i1')).toHaveLength(1);
  });

  it('formats stop costs in the itinerary currency', async () => {
    setup({ ...itinerary, currency: 'EUR' });
    renderApp('/itineraries/i1');
    const stop = (await screen.findByRole('heading', { name: 'Dinner' })).closest('article');
    const cost = stop?.querySelector('p.font-semibold:not(.text-muted)');
    expect(cost?.textContent).toMatch(/€|EUR/);
    expect(cost?.textContent).not.toContain('$');
  });

  it('hides Schedule and Cancel on a completed itinerary', async () => {
    setup({ ...itinerary, status: 'completed' });
    renderApp('/itineraries/i1');
    expect(await screen.findByRole('heading', { name: 'A very good night' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Schedule' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Cancel' })).not.toBeInTheDocument();
  });

  it('deletes a checklist item as the planner', async () => {
    const api = setup();
    renderApp('/itineraries/i1');
    await userEvent.click(await screen.findByRole('button', { name: 'Delete Reserve the table' }));
    await waitFor(() =>
      expect(api.find('DELETE', '/api/v1/itineraries/i1/checklist/c1')).toHaveLength(1),
    );
  });
});
