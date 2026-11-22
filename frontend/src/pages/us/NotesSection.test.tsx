import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { constraints, couple, husbandProfile, session, wifeProfile } from '../../test/fixtures';
import { mockApi, type Handler } from '../../test/mockApi';
import { renderApp } from '../../test/renderApp';

afterEach(() => {
  vi.unstubAllGlobals();
});

const noAvailability = { windows: [], blocks: [] };
const longText = `Wants to try the pottery class ${'downtown '.repeat(12)}`.trim();

function note(kind: 'note' | 'mentioned_idea') {
  return {
    id: `n-${kind}`,
    about_profile_id: 'p-wife',
    kind,
    text: kind === 'mentioned_idea' ? longText : 'Gets cold easily',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    archived_at: null,
  };
}

// The mock ignores the ?kind= query, so both sections list whatever this returns.
function api(notes: unknown[], extra: Record<string, Handler> = {}) {
  return mockApi({
    'GET /api/v1/auth/session': session('Sam'),
    'GET /api/v1/meta': { demo_mode: false, version: 'test', no_login: true },
    'GET /api/v1/profiles': [husbandProfile, wifeProfile],
    'GET /api/v1/profiles/p-husband/suggestions': [],
    'GET /api/v1/profiles/p-wife/suggestions': [],
    'GET /api/v1/profiles/p-husband/availability': noAvailability,
    'GET /api/v1/profiles/p-wife/availability': noAvailability,
    'GET /api/v1/couple': couple,
    'GET /api/v1/couple/constraints': constraints,
    'GET /api/v1/couple/important-dates': [],
    'GET /api/v1/notes': notes,
    ...extra,
  });
}

describe('NotesSection: make it an idea', () => {
  it('prefills the idea form from the note and saves a private idea', async () => {
    const mock = api([note('mentioned_idea')], { 'POST /api/v1/ideas': { id: 'i1' } });
    renderApp('/us');
    const mentioned = await screen.findByRole('region', { name: 'Ideas Alex mentioned' });
    await userEvent.click(
      await within(mentioned).findByRole('button', { name: 'Make it an idea' }),
    );

    expect(within(mentioned).getByLabelText('Title')).toHaveValue(longText.slice(0, 80));
    expect(within(mentioned).getByLabelText('Description')).toHaveValue(longText);

    await userEvent.click(within(mentioned).getByRole('button', { name: 'Save idea' }));
    await waitFor(() =>
      expect(mock.body('POST', '/api/v1/ideas')).toEqual({
        title: longText.slice(0, 80).trim(),
        pitch: longText,
        est_cost: null,
        tags: [],
      }),
    );
    expect(await within(mentioned).findByText(/Added to your ideas/)).toBeInTheDocument();
    expect(within(mentioned).getByRole('link', { name: 'See your ideas' })).toHaveAttribute(
      'href',
      '/ideas',
    );
  });

  it('is not offered on a plain note', async () => {
    api([note('note')]);
    renderApp('/us');
    await screen.findAllByText('Gets cold easily');
    expect(screen.queryByRole('button', { name: 'Make it an idea' })).not.toBeInTheDocument();
  });
});
