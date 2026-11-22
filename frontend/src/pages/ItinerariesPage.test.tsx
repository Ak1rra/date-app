import { screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { PastDateOut, RatingOut } from '../hooks/useFeedback';
import { mockApi } from '../test/mockApi';
import { renderApp } from '../test/renderApp';

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

const rating = (stars: number): RatingOut => ({
  stars,
  would_again: true,
  comment: null,
  stop_thumbs: {},
  updated_at: '2026-11-11T00:00:00Z',
});

const base: PastDateOut = {
  id: 'p1',
  title: 'Picnic in the park',
  starts_at: '2026-11-10T18:00:00Z',
  ends_at: null,
  is_surprise: false,
  planner_user_id: 'u1',
  mine: null,
  partner: null,
  planner_read: null,
};

function setup(past: PastDateOut[]) {
  return mockApi({
    'GET /api/v1/auth/session': session,
    'GET /api/v1/itineraries': [],
    'GET /api/v1/past-dates': past,
  });
}

afterEach(() => vi.restoreAllMocks());

describe('ItinerariesPage past dates', () => {
  it('shows an empty state when there are none', async () => {
    setup([]);
    renderApp('/itineraries');
    expect(await screen.findByText('No past dates yet')).toBeInTheDocument();
  });

  it('lists past dates with my stars, a Rate it prompt and the partner stars', async () => {
    setup([
      { ...base, mine: rating(4), partner: rating(5) },
      { ...base, id: 'p2', title: 'Mini golf', mine: null },
    ]);
    renderApp('/itineraries');
    const first = await screen.findByRole('link', { name: /Picnic in the park/ });
    expect(first).toHaveAttribute('href', '/itineraries/p1');
    expect(first).toHaveTextContent('You:');
    expect(first).toHaveTextContent('Partner:');
    const stars = screen.getAllByRole('img', { name: /out of 5 stars/ });
    expect(stars.map((node) => node.getAttribute('aria-label'))).toEqual([
      '4 out of 5 stars',
      '5 out of 5 stars',
    ]);
    const second = screen.getByRole('link', { name: /Mini golf/ });
    expect(second).toHaveAttribute('href', '/itineraries/p2');
    expect(second).toHaveTextContent('Rate it');
    expect(second).not.toHaveTextContent('Partner:');
  });
});
