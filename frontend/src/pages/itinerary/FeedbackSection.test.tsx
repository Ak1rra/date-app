import { fireEvent, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { PlannerReadOut, RatingOut, RatingsOut } from '../../hooks/useFeedback';
import type { ItineraryOut } from '../../hooks/useItineraries';
import { mockApi } from '../../test/mockApi';
import { renderApp } from '../../test/renderApp';

const session = {
  authenticated: true,
  user: {
    id: 'u1',
    email: 'me@example.com',
    display_name: 'Sam',
    timezone: 'UTC',
    couple_id: 'c1',
    role: 'planner',
  },
};

const itinerary: ItineraryOut = {
  id: 'i1',
  idea_id: null,
  planner_user_id: 'u1',
  is_planner: true,
  title: 'A very good night',
  starts_at: '2026-11-10T18:00:00Z',
  ends_at: '2026-11-10T22:00:00Z',
  status: 'completed',
  announced_at: null,
  invitation_message: null,
  is_surprise: false,
  reveal_at: null,
  revealed_at: null,
  seen_by_partner_at: null,
  budget: null,
  est_total: null,
  currency: 'USD',
  weather: null,
  backup_plan: null,
  bring_wear: null,
  warnings: [],
  partner_reaction: null,
  created_at: '2026-11-01T00:00:00Z',
  updated_at: '2026-11-01T00:00:00Z',
  stops: [
    {
      id: 's1',
      position: 0,
      title: 'Dinner',
      description: null,
      starts_at: '2026-11-10T18:00:00Z',
      ends_at: '2026-11-10T19:30:00Z',
      est_cost: null,
      notes: null,
      place_id: null,
      reveal_policy: 'with_itinerary',
      booking_url: null,
      maps: { google: null, apple: null, directions_from_previous: null },
      venue: null,
      event: null,
      warnings: [],
      travel_to_next: null,
    },
  ],
  checklist: [],
  attributions: [],
};

const mine: RatingOut = {
  stars: 4,
  would_again: true,
  comment: 'So sweet',
  stop_thumbs: {},
  updated_at: '2026-11-11T00:00:00Z',
};
const partner: RatingOut = {
  stars: 5,
  would_again: true,
  comment: 'Best night ever',
  stop_thumbs: {},
  updated_at: '2026-11-11T00:00:00Z',
};
const read: PlannerReadOut = {
  enjoyment: 3,
  highlights: 'The dessert',
  misses: 'The queue',
  updated_at: '2026-11-11T00:00:00Z',
};
const unrated: RatingsOut = {
  mine: null,
  partner: null,
  partner_revealed: false,
  planner_read: null,
};

function setup(
  ratings: RatingsOut = unrated,
  data: ItineraryOut = itinerary,
  extra: Record<string, unknown> = {},
) {
  return mockApi({
    'GET /api/v1/auth/session': session,
    'GET /api/v1/itineraries/i1': data,
    'GET /api/v1/itineraries/i1/ratings': ratings,
    'PUT /api/v1/itineraries/i1/rating': mine,
    'PUT /api/v1/itineraries/i1/planner-read': read,
    ...extra,
  });
}

afterEach(() => vi.restoreAllMocks());

describe('FeedbackSection', () => {
  it('is not shown until the date is completed', async () => {
    const api = setup(unrated, { ...itinerary, status: 'scheduled' });
    renderApp('/itineraries/i1');
    expect(await screen.findByRole('heading', { name: 'A very good night' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Rate this date' })).not.toBeInTheDocument();
    expect(api.find('GET', '/api/v1/itineraries/i1/ratings')).toHaveLength(0);
  });

  it('lets me rate a completed date and hides the partner until revealed', async () => {
    const api = setup();
    renderApp('/itineraries/i1');
    await userEvent.click(await screen.findByRole('button', { name: 'Rate this date' }));
    expect(screen.getByRole('dialog', { name: 'How was your date?' })).toBeInTheDocument();
    expect(screen.getByText(/Hidden until you've both rated/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('radio', { name: '4 stars' }));
    await userEvent.click(screen.getByRole('button', { name: 'Yes, again' }));
    await userEvent.click(screen.getByRole('button', { name: 'Save rating' }));
    await waitFor(() =>
      expect(api.body('PUT', '/api/v1/itineraries/i1/rating')).toMatchObject({
        stars: 4,
        would_again: true,
      }),
    );
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });

  it('shows my rating with an Edit button, and the partner rating once revealed', async () => {
    setup({ ...unrated, mine, partner, partner_revealed: true });
    renderApp('/itineraries/i1');
    expect(await screen.findByText(/So sweet/)).toBeInTheDocument();
    expect(screen.getByText(/Best night ever/)).toBeInTheDocument();
    expect(screen.queryByText(/Hidden until/)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Edit' }));
    expect(screen.getByRole('radio', { name: '4 stars' })).toBeChecked();
  });

  it('keeps a partner rating hidden when it is not revealed', async () => {
    setup({ ...unrated, mine, partner: null, partner_revealed: false });
    renderApp('/itineraries/i1');
    expect(await screen.findByText(/Hidden until you've both rated/)).toBeInTheDocument();
    expect(screen.queryByText(/Best night ever/)).not.toBeInTheDocument();
  });

  it('shows the planner read form to the planner only', async () => {
    const api = setup();
    renderApp('/itineraries/i1');
    expect(await screen.findByText(/Private to you/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('radio', { name: 'Enjoyment 3 of 5' }));
    await userEvent.type(screen.getByLabelText('What they loved'), 'The dessert');
    await userEvent.click(screen.getByRole('button', { name: 'Save my read' }));
    await waitFor(() =>
      expect(api.body('PUT', '/api/v1/itineraries/i1/planner-read')).toEqual({
        enjoyment: 3,
        highlights: 'The dessert',
        misses: null,
      }),
    );
  });

  it('hides the planner read form from the partner', async () => {
    setup(unrated, { ...itinerary, is_planner: false });
    renderApp('/itineraries/i1');
    expect(await screen.findByRole('button', { name: 'Rate this date' })).toBeInTheDocument();
    expect(screen.queryByText('How did they like it?')).not.toBeInTheDocument();
  });

  it('shows a saved planner read', async () => {
    setup({ ...unrated, planner_read: read });
    renderApp('/itineraries/i1');
    expect(await screen.findByText('The queue')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Edit my read' })).toBeInTheDocument();
  });

  it('opens the rating sheet once for ?rate=1, then drops the param', async () => {
    setup();
    const router = renderApp('/itineraries/i1?rate=1');
    expect(await screen.findByRole('dialog', { name: 'How was your date?' })).toBeInTheDocument();
    await waitFor(() => expect(router.state.location.search).toBe(''));
    await userEvent.click(screen.getByRole('button', { name: 'Close rating' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('does not open the sheet for ?rate=1 once I have rated', async () => {
    setup({ ...unrated, mine });
    const router = renderApp('/itineraries/i1?rate=1');
    expect(await screen.findByText(/So sweet/)).toBeInTheDocument();
    await waitFor(() => expect(router.state.location.search).toBe(''));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('opens the sheet again for ?rate=1 on a different date', async () => {
    setup(unrated, itinerary, {
      'GET /api/v1/itineraries/i2': { ...itinerary, id: 'i2', title: 'Another night' },
      'GET /api/v1/itineraries/i2/ratings': unrated,
    });
    const router = renderApp('/itineraries/i1?rate=1');
    expect(await screen.findByRole('dialog', { name: 'How was your date?' })).toBeInTheDocument();
    await waitFor(() => expect(router.state.location.search).toBe(''));
    await userEvent.click(screen.getByRole('button', { name: 'Close rating' }));

    await router.navigate('/itineraries/i2?rate=1');
    expect(await screen.findByRole('heading', { name: 'Another night' })).toBeInTheDocument();
    expect(await screen.findByRole('dialog', { name: 'How was your date?' })).toBeInTheDocument();
    await waitFor(() => expect(router.state.location.search).toBe(''));
  });

  it('does not carry an unsaved planner read draft to another date', async () => {
    setup(unrated, itinerary, {
      'GET /api/v1/itineraries/i2': { ...itinerary, id: 'i2', title: 'Another night' },
      'GET /api/v1/itineraries/i2/ratings': unrated,
    });
    const router = renderApp('/itineraries/i1');
    await userEvent.type(await screen.findByLabelText('What they loved'), 'Draft for the first');
    await router.navigate('/itineraries/i2');
    expect(await screen.findByRole('heading', { name: 'Another night' })).toBeInTheDocument();
    expect(await screen.findByLabelText('What they loved')).toHaveValue('');
  });

  it('trims the planner read text to 1000 characters before sending', async () => {
    const api = setup();
    renderApp('/itineraries/i1');
    await userEvent.click(await screen.findByRole('radio', { name: 'Enjoyment 2 of 5' }));
    fireEvent.change(screen.getByLabelText('What they loved'), {
      target: { value: 'x'.repeat(1200) },
    });
    await userEvent.click(screen.getByRole('button', { name: 'Save my read' }));
    await waitFor(() =>
      expect(api.body('PUT', '/api/v1/itineraries/i1/planner-read')).toMatchObject({
        highlights: 'x'.repeat(1000),
      }),
    );
  });
});
