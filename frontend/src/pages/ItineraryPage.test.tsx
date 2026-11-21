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

const dinner = itinerary.stops[0]!;
const dessert: ItineraryOut['stops'][number] = {
  ...dinner,
  id: 's2',
  position: 1,
  title: 'Dessert',
  place_id: 'p-dessert',
  starts_at: '2026-12-10T19:30:00Z',
  ends_at: '2026-12-10T20:30:00Z',
  est_cost: null,
  venue: null,
};
const twoStops: ItineraryOut = { ...itinerary, stops: [dinner, dessert] };

function setup(data: ItineraryOut | Response = itinerary, extra: Record<string, unknown> = {}) {
  return mockApi({
    'PUT /api/v1/itineraries/i1/stops': twoStops,
    'PATCH /api/v1/itineraries/i1': itinerary,
    'GET /api/v1/places/search': [
      {
        place_id: 'p-new',
        name: 'Moonlight Mini Golf',
        address: '9 Park Ave',
        rating: 4.6,
        price_level: 1,
        lat: null,
        lng: null,
        open_now: null,
        attributions: [],
      },
    ],
    'GET /api/v1/auth/session': session,
    'GET /api/v1/itineraries/i1': data,
    'PATCH /api/v1/itineraries/i1/checklist/c1': { ...itinerary.checklist[0], done: true },
    'POST /api/v1/itineraries/i1/checklist': itinerary.checklist[0],
    'POST /api/v1/itineraries/i1/schedule': itinerary,
    'POST /api/v1/itineraries/i1/cancel': itinerary,
    'POST /api/v1/itineraries/i1/react': itinerary,
    'DELETE /api/v1/itineraries/i1/checklist/c1': new Response(null, { status: 204 }),
    ...extra,
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

  it('saves the itinerary as a template with a chosen name, then offers a link', async () => {
    const api = setup(itinerary, {
      'POST /api/v1/itineraries/i1/template': jsonResponse({ id: 't1' }, 201),
    });
    renderApp('/itineraries/i1');
    await userEvent.click(await screen.findByRole('button', { name: 'Save as template' }));
    const name = screen.getByLabelText('Template name');
    expect(name).toHaveValue('A very good night');
    await userEvent.clear(name);
    await userEvent.type(name, 'Our classic night');
    await userEvent.click(screen.getByRole('button', { name: 'Save template' }));

    expect(await screen.findByRole('status')).toHaveTextContent('Saved');
    expect(api.body('POST', '/api/v1/itineraries/i1/template')).toEqual({
      name: 'Our classic night',
    });
    expect(screen.getByRole('link', { name: 'See templates' })).toHaveAttribute(
      'href',
      '/templates',
    );
  });

  it('truncates a long default template name to 80 characters', async () => {
    setup({ ...itinerary, title: 'x'.repeat(100) });
    renderApp('/itineraries/i1');
    await userEvent.click(await screen.findByRole('button', { name: 'Save as template' }));
    expect(screen.getByLabelText('Template name')).toHaveValue('x'.repeat(80));
  });

  it('offers no save-as-template to the partner', async () => {
    setup({ ...itinerary, is_planner: false });
    renderApp('/itineraries/i1');
    await screen.findByRole('button', { name: /I'm in/ });
    expect(screen.queryByRole('button', { name: 'Save as template' })).not.toBeInTheDocument();
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

describe('ItineraryPage editing', () => {
  it('reorders stops and saves the full ordered list', async () => {
    const api = setup(twoStops);
    renderApp('/itineraries/i1');
    await userEvent.click(await screen.findByRole('button', { name: 'Edit stops' }));
    expect(screen.getByRole('button', { name: 'Move Dinner up' })).toBeDisabled();
    await userEvent.click(screen.getByRole('button', { name: 'Move Dinner down' }));
    await userEvent.click(screen.getByRole('button', { name: 'Save stops' }));
    await waitFor(() =>
      expect(api.body('PUT', '/api/v1/itineraries/i1/stops')).toEqual({
        stops: [
          expect.objectContaining({
            id: 's2',
            title: 'Dessert',
            place_id: 'p-dessert',
            starts_at: '2026-12-10T19:30:00Z',
            ends_at: '2026-12-10T20:30:00Z',
          }),
          expect.objectContaining({
            id: 's1',
            title: 'Dinner',
            place_id: null,
            starts_at: '2026-12-10T18:00:00Z',
            ends_at: '2026-12-10T19:30:00Z',
            description: 'A table for two',
            est_cost: 95,
          }),
        ],
      }),
    );
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: 'Save stops' })).not.toBeInTheDocument(),
    );
  });

  it('retimes a stop', async () => {
    const api = setup();
    renderApp('/itineraries/i1');
    await userEvent.click(await screen.findByRole('button', { name: 'Edit stops' }));
    fireEvent.change(screen.getByLabelText('End time for Dinner'), {
      target: { value: '2026-12-10T21:00' },
    });
    await userEvent.click(screen.getByRole('button', { name: 'Save stops' }));
    await waitFor(() =>
      expect(api.body('PUT', '/api/v1/itineraries/i1/stops')).toMatchObject({
        stops: [
          {
            id: 's1',
            starts_at: '2026-12-10T18:00:00Z',
            ends_at: new Date('2026-12-10T21:00').toISOString(),
          },
        ],
      }),
    );
  });

  it('rejects an end before the start without calling the server', async () => {
    const api = setup();
    renderApp('/itineraries/i1');
    await userEvent.click(await screen.findByRole('button', { name: 'Edit stops' }));
    fireEvent.change(screen.getByLabelText('End time for Dinner'), {
      target: { value: '2000-01-01T00:00' },
    });
    await userEvent.click(screen.getByRole('button', { name: 'Save stops' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Dinner needs to end after it starts',
    );
    expect(api.find('PUT', '/api/v1/itineraries/i1/stops')).toHaveLength(0);
  });

  it('adds a stop found by venue search', async () => {
    const api = setup();
    renderApp('/itineraries/i1');
    await userEvent.click(await screen.findByRole('button', { name: 'Edit stops' }));
    await userEvent.click(screen.getByRole('button', { name: 'Add a stop' }));
    await userEvent.type(screen.getByLabelText('Search for a venue'), 'golf');
    const result = await screen.findByRole('button', { name: /Moonlight Mini Golf/ });
    expect(result).toHaveTextContent('9 Park Ave');
    expect(result).toHaveTextContent('4.6');
    await userEvent.click(result);
    await userEvent.click(screen.getByRole('button', { name: 'Save stops' }));
    await waitFor(() =>
      expect(api.body('PUT', '/api/v1/itineraries/i1/stops')).toMatchObject({
        stops: [
          { id: 's1' },
          {
            title: 'Moonlight Mini Golf',
            place_id: 'p-new',
            starts_at: new Date('2026-12-10T19:30:00Z').toISOString(),
            ends_at: new Date('2026-12-10T20:30:00Z').toISOString(),
          },
        ],
      }),
    );
  });

  it('adds a custom stop without a place', async () => {
    const api = setup();
    renderApp('/itineraries/i1');
    await userEvent.click(await screen.findByRole('button', { name: 'Edit stops' }));
    await userEvent.click(screen.getByRole('button', { name: 'Add a stop' }));
    await userEvent.type(screen.getByLabelText('Or add a custom stop'), 'Sunset walk');
    await userEvent.click(screen.getByRole('button', { name: 'Add custom stop' }));
    await userEvent.click(screen.getByRole('button', { name: 'Save stops' }));
    await waitFor(() =>
      expect(api.body('PUT', '/api/v1/itineraries/i1/stops')).toMatchObject({
        stops: [{ id: 's1' }, { title: 'Sunset walk', place_id: null }],
      }),
    );
  });

  it('says so when venue search is unavailable', async () => {
    setup(itinerary, {
      'GET /api/v1/places/search': jsonResponse(
        { error: { code: 'unavailable', message: 'nope' } },
        503,
      ),
    });
    renderApp('/itineraries/i1');
    await userEvent.click(await screen.findByRole('button', { name: 'Edit stops' }));
    await userEvent.click(screen.getByRole('button', { name: 'Add a stop' }));
    await userEvent.type(screen.getByLabelText('Search for a venue'), 'golf');
    expect(await screen.findByText('Venue search is unavailable right now')).toBeInTheDocument();
  });

  it('removes a stop after confirming', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    const api = setup(twoStops);
    renderApp('/itineraries/i1');
    await userEvent.click(await screen.findByRole('button', { name: 'Edit stops' }));
    await userEvent.click(screen.getByRole('button', { name: 'Remove Dinner' }));
    await userEvent.click(screen.getByRole('button', { name: 'Save stops' }));
    await waitFor(() => {
      const body = api.body('PUT', '/api/v1/itineraries/i1/stops') as { stops: { id: string }[] };
      expect(body.stops.map((stop) => stop.id)).toEqual(['s2']);
    });
  });

  it('keeps a stop when removal is not confirmed, and discards on cancel', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false);
    const api = setup();
    renderApp('/itineraries/i1');
    await userEvent.click(await screen.findByRole('button', { name: 'Edit stops' }));
    await userEvent.click(screen.getByRole('button', { name: 'Remove Dinner' }));
    expect(screen.getByLabelText('Name of stop 1')).toHaveValue('Dinner');
    await userEvent.click(screen.getByRole('button', { name: 'Discard changes' }));
    expect(screen.getByRole('heading', { name: 'Dinner' })).toBeInTheDocument();
    expect(api.find('PUT', '/api/v1/itineraries/i1/stops')).toHaveLength(0);
  });

  it("shows the server's message when saving stops fails", async () => {
    setup(itinerary, {
      'PUT /api/v1/itineraries/i1/stops': jsonResponse(
        { error: { code: 'invalid_stops', message: 'Stops overlap in time.' } },
        422,
      ),
    });
    renderApp('/itineraries/i1');
    await userEvent.click(await screen.findByRole('button', { name: 'Edit stops' }));
    await userEvent.click(screen.getByRole('button', { name: 'Save stops' }));
    expect(await screen.findByText(/Stops overlap in time\./)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save stops' })).toBeInTheDocument();
  });

  it('sends only the changed details', async () => {
    const api = setup();
    renderApp('/itineraries/i1');
    await userEvent.click(await screen.findByRole('button', { name: 'Edit details' }));
    const title = screen.getByLabelText('Title');
    await userEvent.clear(title);
    await userEvent.type(title, 'A lovelier night');
    await userEvent.clear(screen.getByLabelText('Backup plan'));
    await userEvent.click(screen.getByRole('button', { name: 'Save details' }));
    await waitFor(() =>
      expect(api.body('PATCH', '/api/v1/itineraries/i1')).toEqual({
        title: 'A lovelier night',
        backup_plan: null,
      }),
    );
  });

  it('shows no editing controls to the partner', async () => {
    setup({ ...itinerary, is_planner: false });
    renderApp('/itineraries/i1');
    expect(await screen.findByRole('button', { name: /I'm in/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Edit stops' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Edit details' })).not.toBeInTheDocument();
  });

  it('disables Remove on the last stop and says why', async () => {
    setup();
    renderApp('/itineraries/i1');
    await userEvent.click(await screen.findByRole('button', { name: 'Edit stops' }));
    expect(screen.getByRole('button', { name: 'Remove Dinner' })).toBeDisabled();
    expect(screen.getByText('A date needs at least one stop.')).toBeInTheDocument();
  });

  it('shows the checklist read-only on a completed itinerary', async () => {
    setup({ ...itinerary, status: 'completed' });
    renderApp('/itineraries/i1');
    expect(await screen.findByText('Reserve the table')).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: 'Mark Reserve the table done' })).toBeDisabled();
    expect(
      screen.queryByRole('button', { name: 'Delete Reserve the table' }),
    ).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Add a finishing touch')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Add item' })).not.toBeInTheDocument();
  });

  it('hides editing on a completed itinerary', async () => {
    setup({ ...itinerary, status: 'completed' });
    renderApp('/itineraries/i1');
    expect(await screen.findByRole('heading', { name: 'A very good night' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Edit stops' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Edit details' })).not.toBeInTheDocument();
  });

  it('asks for another stop with a note and shows the replacement', async () => {
    const replaced: ItineraryOut = {
      ...itinerary,
      stops: [{ ...dinner, id: 's9', title: 'Rooftop tapas' }],
    };
    const api = setup(itinerary, {
      'POST /api/v1/itineraries/i1/stops/s1/regenerate': replaced,
    });
    renderApp('/itineraries/i1');
    await userEvent.click(await screen.findByRole('button', { name: 'Try another for Dinner' }));
    await userEvent.type(screen.getByLabelText('What should change? (optional)'), 'Outdoors');
    await userEvent.click(screen.getByRole('button', { name: 'Find something new' }));
    expect(await screen.findByRole('heading', { name: 'Rooftop tapas' })).toBeInTheDocument();
    expect(api.body('POST', '/api/v1/itineraries/i1/stops/s1/regenerate')).toEqual({
      note: 'Outdoors',
    });
    expect(screen.queryByRole('heading', { name: 'Dinner' })).not.toBeInTheDocument();
  });

  it('sends no body when the note is empty and disables the form while pending', async () => {
    let release: (value: ItineraryOut) => void = () => undefined;
    const held = new Promise<ItineraryOut>((resolve) => (release = resolve));
    const api = setup(itinerary, {
      'POST /api/v1/itineraries/i1/stops/s1/regenerate': () => held,
    });
    renderApp('/itineraries/i1');
    await userEvent.click(await screen.findByRole('button', { name: 'Try another for Dinner' }));
    await userEvent.click(screen.getByRole('button', { name: 'Find something new' }));
    expect(await screen.findByRole('button', { name: 'Finding something new…' })).toBeDisabled();
    expect(api.body('POST', '/api/v1/itineraries/i1/stops/s1/regenerate')).toBeUndefined();
    release(itinerary);
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: 'Finding something new…' })).toBeNull(),
    );
  });

  it("disables another stop's Try another while one regeneration is pending", async () => {
    let release: (value: ItineraryOut) => void = () => undefined;
    const held = new Promise<ItineraryOut>((resolve) => (release = resolve));
    setup(twoStops, {
      'POST /api/v1/itineraries/i1/stops/s1/regenerate': () => held,
    });
    renderApp('/itineraries/i1');
    await userEvent.click(await screen.findByRole('button', { name: 'Try another for Dinner' }));
    const other = screen.getByRole('button', { name: `Try another for ${dessert.title}` });
    expect(other).toBeEnabled();
    await userEvent.click(screen.getByRole('button', { name: 'Find something new' }));
    await waitFor(() => expect(other).toBeDisabled());
    // Every other change to the itinerary waits for the replacement too.
    for (const name of ['Schedule', 'Edit stops', 'Edit details', 'Cancel']) {
      expect(screen.getByRole('button', { name })).toBeDisabled();
    }
    release(twoStops);
    await waitFor(() => expect(other).toBeEnabled());
    expect(screen.getByRole('button', { name: 'Schedule' })).toBeEnabled();
  });

  it('shows the planner message when regenerating fails with ai_failed', async () => {
    setup(itinerary, {
      'POST /api/v1/itineraries/i1/stops/s1/regenerate': jsonResponse(
        {
          error: {
            code: 'ai_failed',
            message: "The planner couldn't put that together. Try again.",
          },
        },
        502,
      ),
    });
    renderApp('/itineraries/i1');
    await userEvent.click(await screen.findByRole('button', { name: 'Try another for Dinner' }));
    await userEvent.click(screen.getByRole('button', { name: 'Find something new' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(
      "The planner couldn't put that together. Try again.",
    );
  });

  it('offers Try another only to the planner of a draft or scheduled date', async () => {
    setup({ ...itinerary, is_planner: false });
    renderApp('/itineraries/i1');
    await screen.findByRole('button', { name: /I'm in/ });
    expect(screen.queryByRole('button', { name: /Try another/ })).not.toBeInTheDocument();
  });

  it('hides Try another on a completed itinerary', async () => {
    setup({ ...itinerary, status: 'completed' });
    renderApp('/itineraries/i1');
    await screen.findByRole('heading', { name: 'Dinner' });
    expect(screen.queryByRole('button', { name: /Try another/ })).not.toBeInTheDocument();
  });

  it('polishes the invitation message and can undo it', async () => {
    const api = setup(itinerary, {
      'POST /api/v1/itineraries/i1/polish-invitation': { text: 'Dinner is on me, my love.' },
    });
    renderApp('/itineraries/i1');
    await userEvent.click(await screen.findByRole('button', { name: 'Schedule' }));
    const polish = screen.getByRole('button', { name: 'Polish it' });
    expect(polish).toBeDisabled();
    const field = screen.getByLabelText('Invitation message (optional)');
    await userEvent.type(field, 'dinner on me');
    await userEvent.click(polish);
    await waitFor(() => expect(field).toHaveValue('Dinner is on me, my love.'));
    expect(api.body('POST', '/api/v1/itineraries/i1/polish-invitation')).toEqual({
      draft: 'dinner on me',
    });
    await userEvent.click(screen.getByRole('button', { name: 'Undo' }));
    expect(field).toHaveValue('dinner on me');
    expect(screen.queryByRole('button', { name: 'Undo' })).not.toBeInTheDocument();
  });

  it('locks the message, Undo and Schedule date while polishing', async () => {
    let release: (value: { text: string }) => void = () => undefined;
    const held = new Promise<{ text: string }>((resolve) => (release = resolve));
    let calls = 0;
    setup(itinerary, {
      'POST /api/v1/itineraries/i1/polish-invitation': () =>
        ++calls === 1 ? { text: 'Dinner is on me.' } : held,
    });
    renderApp('/itineraries/i1');
    await userEvent.click(await screen.findByRole('button', { name: 'Schedule' }));
    const field = screen.getByLabelText('Invitation message (optional)');
    await userEvent.type(field, 'dinner on me');
    await userEvent.click(screen.getByRole('button', { name: 'Polish it' }));
    await waitFor(() => expect(field).toHaveValue('Dinner is on me.'));
    await userEvent.click(screen.getByRole('button', { name: 'Polish it' }));
    expect(await screen.findByRole('button', { name: 'Polishing…' })).toBeDisabled();
    expect(field).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Undo' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Schedule date' })).toBeDisabled();
    release({ text: 'Dinner is on me, my love.' });
    await waitFor(() => expect(field).toHaveValue('Dinner is on me, my love.'));
    expect(field).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Undo' })).toBeEnabled();
  });

  it('shows the planner message when polishing is unavailable', async () => {
    setup(itinerary, {
      'POST /api/v1/itineraries/i1/polish-invitation': jsonResponse(
        { error: { code: 'planner_unavailable', message: 'The planner is resting.' } },
        503,
      ),
    });
    renderApp('/itineraries/i1');
    await userEvent.click(await screen.findByRole('button', { name: 'Schedule' }));
    await userEvent.type(screen.getByLabelText('Invitation message (optional)'), 'hi');
    await userEvent.click(screen.getByRole('button', { name: 'Polish it' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('The planner is resting.');
  });
});
