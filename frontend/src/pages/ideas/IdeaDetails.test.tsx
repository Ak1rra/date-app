import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { Schemas } from '../../api/client';
import { IdeaDetails } from './IdeaDetails';

function idea(overrides: Partial<Schemas['IdeaOut']> = {}): Schemas['IdeaOut'] {
  return {
    id: 'i1',
    title: 'Pottery night',
    pitch: 'Get messy together.',
    est_cost: null,
    currency: 'USD',
    tags: [],
    category: null,
    best_time: null,
    duration_minutes: null,
    indoor_outdoor: null,
    event: null,
    venue: null,
    grounded: false,
    may_have_changed: false,
    attributions: [],
    created_by_me: false,
    source: 'manual',
    status: 'active',
    visibility: 'shared',
    valid_until: null,
    votes: { mine: null, partner: null },
    ...overrides,
  };
}

describe('IdeaDetails', () => {
  it('shows only the title and pitch for a bare idea', () => {
    render(<IdeaDetails idea={idea()} />);
    expect(screen.getByRole('heading', { level: 2, name: 'Pottery night' })).toBeInTheDocument();
    expect(screen.getByText('Get messy together.')).toBeInTheDocument();
    expect(screen.queryByRole('list', { name: 'Details' })).not.toBeInTheDocument();
    expect(screen.queryByRole('list', { name: 'Venue' })).not.toBeInTheDocument();
    expect(screen.queryByText('Live pick')).not.toBeInTheDocument();
    expect(screen.queryByText('Evergreen idea')).not.toBeInTheDocument();
    expect(screen.queryByText(/^Data:/)).not.toBeInTheDocument();
  });

  it('renders duration, best time, indoor/outdoor and the event date and time', () => {
    render(
      <IdeaDetails
        titleLevel={3}
        idea={idea({
          duration_minutes: 120,
          best_time: 'evening',
          indoor_outdoor: 'indoor',
          event: { name: 'Class', local_date: '2030-05-04', local_time: '19:30:00', url: null },
        })}
      />,
    );
    expect(screen.getByRole('heading', { level: 3 })).toBeInTheDocument();
    const details = screen.getByRole('list', { name: 'Details' });
    expect(details).toHaveTextContent('About 2 h');
    expect(details).toHaveTextContent('Best in the evening');
    expect(details).toHaveTextContent('Indoors');
    expect(details).toHaveTextContent(/May\b/);
    expect(details).toHaveTextContent(/7:30/);
  });

  it('shows a date-only event without inventing a time', () => {
    render(
      <IdeaDetails
        idea={idea({
          event: { name: 'Class', local_date: '2030-05-04', local_time: null, url: null },
        })}
      />,
    );
    const details = screen.getByRole('list', { name: 'Details' });
    expect(details).toHaveTextContent(/May\b/);
    expect(details).not.toHaveTextContent(/\d:\d\d/);
    expect(details).not.toHaveTextContent(/\bat\b/);
  });

  it('shows nothing for an event with neither a date nor a time', () => {
    render(
      <IdeaDetails
        idea={idea({ event: { name: 'Class', local_date: null, local_time: null, url: null } })}
      />,
    );
    expect(screen.queryByRole('list', { name: 'Details' })).not.toBeInTheDocument();
  });

  it('formats short and mixed durations', () => {
    const { rerender } = render(<IdeaDetails idea={idea({ duration_minutes: 45 })} />);
    expect(screen.getByText('About 45 min')).toBeInTheDocument();
    rerender(<IdeaDetails idea={idea({ duration_minutes: 90 })} />);
    expect(screen.getByText('About 1 h 30 min')).toBeInTheDocument();
  });

  it('renders the venue name, rating, price level and distance in miles', () => {
    render(
      <IdeaDetails
        idea={idea({
          venue: {
            name: 'Clay Studio',
            address: null,
            rating: 4.5,
            price_level: 2,
            distance_km: 10,
          },
        })}
      />,
    );
    const venue = screen.getByRole('list', { name: 'Venue' });
    expect(venue).toHaveTextContent('Clay Studio');
    expect(venue).toHaveTextContent('4.5★');
    expect(venue).toHaveTextContent('$$');
    expect(venue).toHaveTextContent('6.2 mi away');
  });

  it('shows a free venue (price level 0) as "Free"', () => {
    render(
      <IdeaDetails
        idea={idea({
          venue: {
            name: 'City Park',
            address: null,
            rating: null,
            price_level: 0,
            distance_km: null,
          },
        })}
      />,
    );
    expect(screen.getByRole('list', { name: 'Venue' })).toHaveTextContent('Free');
  });

  it('omits venue fields that are absent', () => {
    render(
      <IdeaDetails
        idea={idea({
          venue: {
            name: 'Clay Studio',
            address: null,
            rating: null,
            price_level: null,
            distance_km: null,
          },
        })}
      />,
    );
    const venue = screen.getByRole('list', { name: 'Venue' });
    expect(venue).toHaveTextContent('Clay Studio');
    expect(venue).not.toHaveTextContent('★');
    expect(venue).not.toHaveTextContent('$');
    expect(venue).not.toHaveTextContent('mi away');
  });

  it('labels a live AI idea "Live pick" and an ungrounded one "Evergreen idea"', () => {
    const { rerender } = render(<IdeaDetails idea={idea({ source: 'ai', grounded: true })} />);
    expect(screen.getByText('Live pick')).toBeInTheDocument();
    rerender(<IdeaDetails idea={idea({ source: 'ai', grounded: false })} />);
    expect(screen.getByText('Evergreen idea')).toBeInTheDocument();
    expect(screen.queryByText('Live pick')).not.toBeInTheDocument();
  });

  it('shows no badge on a manual or mentioned idea', () => {
    const { rerender } = render(<IdeaDetails idea={idea({ source: 'manual' })} />);
    expect(screen.queryByText('Evergreen idea')).not.toBeInTheDocument();
    rerender(<IdeaDetails idea={idea({ source: 'mentioned' })} />);
    expect(screen.queryByText('Evergreen idea')).not.toBeInTheDocument();
  });

  it('shows the attributions as muted text', () => {
    render(<IdeaDetails idea={idea({ attributions: ['Google', 'Ticketmaster'] })} />);
    expect(screen.getByText('Data: Google, Ticketmaster')).toBeInTheDocument();
  });

  it('links the venue name to its web address in a new tab', () => {
    render(
      <IdeaDetails
        idea={idea({
          venue: {
            name: 'Clay Studio',
            address: null,
            distance_km: null,
            price_level: null,
            rating: null,
            url: 'https://clay.example/visit',
          },
        })}
      />,
    );
    const link = screen.getByRole('link', { name: /Clay Studio/ });
    expect(link).toHaveAttribute('href', 'https://clay.example/visit');
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
    expect(link).toHaveTextContent('(opens in a new tab)');
  });

  it('calls a nameless venue link "Link"', () => {
    render(
      <IdeaDetails
        idea={idea({
          venue: {
            name: null,
            address: null,
            distance_km: null,
            price_level: null,
            rating: null,
            url: 'https://clay.example/',
          },
        })}
      />,
    );
    expect(screen.getByRole('link', { name: /^Link/ })).toHaveAttribute(
      'href',
      'https://clay.example/',
    );
  });

  it('renders no link for a stored url that is not http or https', () => {
    render(
      <IdeaDetails
        idea={idea({
          venue: {
            name: 'Clay Studio',
            address: null,
            distance_km: null,
            price_level: null,
            rating: null,
            url: 'javascript:alert(1)',
          },
        })}
      />,
    );
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
    expect(screen.getByRole('list', { name: 'Venue' })).toHaveTextContent('Clay Studio');
  });
});
