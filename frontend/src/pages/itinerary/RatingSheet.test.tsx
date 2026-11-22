import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { RatingOut } from '../../hooks/useFeedback';
import { mockApi } from '../../test/mockApi';
import { RatingSheet } from './RatingSheet';

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

const stops = [
  { id: 's1', title: 'Dinner' },
  { id: 's2', title: 'Dessert' },
];

const saved: RatingOut = {
  stars: 4,
  would_again: false,
  comment: 'Lovely',
  stop_thumbs: { s1: 'up' },
  updated_at: '2026-12-11T00:00:00Z',
};

function setup(initial: RatingOut | null = null) {
  const api = mockApi({
    'GET /api/v1/auth/session': session,
    'PUT /api/v1/itineraries/i1/rating': saved,
  });
  const onClose = vi.fn();
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <RatingSheet itineraryId="i1" stops={stops} initial={initial} onClose={onClose} />
    </QueryClientProvider>,
  );
  return { api, onClose };
}

afterEach(() => vi.restoreAllMocks());

describe('RatingSheet', () => {
  it('needs stars and a would-again answer before it can be saved', async () => {
    setup();
    const save = screen.getByRole('button', { name: 'Save rating' });
    expect(save).toBeDisabled();
    await userEvent.click(screen.getByRole('radio', { name: '5 stars' }));
    expect(save).toBeDisabled();
    await userEvent.click(screen.getByRole('button', { name: 'Yes, again' }));
    expect(save).toBeEnabled();
  });

  it('submits stars, would-again, comment and per-stop thumbs', async () => {
    const { api, onClose } = setup();
    await userEvent.click(screen.getByRole('radio', { name: '5 stars' }));
    await userEvent.click(screen.getByRole('button', { name: 'Yes, again' }));
    await userEvent.type(screen.getByLabelText('A few words (optional)'), 'Perfect night');
    expect(screen.getByText('13/280')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Thumbs up for Dinner' }));
    await userEvent.click(screen.getByRole('button', { name: 'Thumbs down for Dessert' }));
    await userEvent.click(screen.getByRole('button', { name: 'Save rating' }));

    await waitFor(() =>
      expect(api.body('PUT', '/api/v1/itineraries/i1/rating')).toEqual({
        stars: 5,
        would_again: true,
        comment: 'Perfect night',
        stop_thumbs: { s1: 'up', s2: 'down' },
      }),
    );
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it('prefills an existing rating and lets a thumb be cleared', async () => {
    const { api } = setup(saved);
    expect(screen.getByRole('radio', { name: '4 stars' })).toBeChecked();
    expect(screen.getByRole('button', { name: 'Not again' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(screen.getByLabelText('A few words (optional)')).toHaveValue('Lovely');
    await userEvent.click(screen.getByRole('button', { name: 'Thumbs up for Dinner' }));
    await userEvent.click(screen.getByRole('button', { name: 'Save rating' }));
    await waitFor(() =>
      expect(api.body('PUT', '/api/v1/itineraries/i1/rating')).toMatchObject({
        stars: 4,
        would_again: false,
        stop_thumbs: {},
      }),
    );
  });

  it('limits the comment to 280 characters', () => {
    setup();
    expect(screen.getByLabelText('A few words (optional)')).toHaveAttribute('maxlength', '280');
    expect(screen.getByText('0/280')).toBeInTheDocument();
  });

  it('cuts an overlong comment to 280 characters before sending', async () => {
    const { api } = setup();
    await userEvent.click(screen.getByRole('radio', { name: '3 stars' }));
    await userEvent.click(screen.getByRole('button', { name: 'Yes, again' }));
    fireEvent.change(screen.getByLabelText('A few words (optional)'), {
      target: { value: 'y'.repeat(300) },
    });
    await userEvent.click(screen.getByRole('button', { name: 'Save rating' }));
    await waitFor(() =>
      expect(api.body('PUT', '/api/v1/itineraries/i1/rating')).toMatchObject({
        comment: 'y'.repeat(280),
      }),
    );
  });
});
