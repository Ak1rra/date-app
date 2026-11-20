import { screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { mockSession, renderApp, signedIn } from '../test/renderApp';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('HomePage', () => {
  it('leads with a "Plan a date" action that links to /plan', async () => {
    mockSession(signedIn);
    renderApp('/');

    const plan = await screen.findByRole('link', { name: 'Plan a date' });
    expect(plan).toHaveAttribute('href', '/plan');

    const main = screen.getByRole('main');
    const firstAction = main.querySelector<HTMLElement>('a[href], button');
    expect(firstAction).toBe(plan);
  });

  it('shows teaching empty states for the three Home cards', async () => {
    mockSession(signedIn);
    renderApp('/');
    for (const name of ['Next date', 'Finishing touches due', 'Upcoming occasions']) {
      expect(await screen.findByRole('region', { name })).toBeInTheDocument();
    }
  });
});
