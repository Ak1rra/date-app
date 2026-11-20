import { screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { constraints, couple, husbandProfile, session, wifeProfile } from '../test/fixtures';
import { mockApi } from '../test/mockApi';
import { renderAppWithClient } from '../test/renderApp';

// The tidy-up in profileSwitch is switched off here: scoped query keys alone must keep one
// person's cached data from rendering for another.
vi.mock('./profileSwitch', () => ({
  resetForProfile: vi.fn(),
  useProfileSwitchSync: () => {},
}));

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('per-viewer query keys', () => {
  it("never renders the previous person's cached notes or profiles, even with no clearing", async () => {
    const husbandNote = {
      id: 'n1',
      about_profile_id: 'p-wife',
      kind: 'note',
      text: 'Husband private note',
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
      archived_at: null,
    };
    let releaseNotes!: () => void;
    const heldNotes = new Promise<void>((resolve) => (releaseNotes = resolve));
    const noAvailability = { windows: [], blocks: [] };
    const wifeSession = session('Alex', 'partner');
    wifeSession.user!.id = 'u-wife';
    mockApi({
      'GET /api/v1/auth/session': wifeSession,
      'GET /api/v1/meta': { demo_mode: false, version: 'test', no_login: true },
      'GET /api/v1/profiles': [
        { ...husbandProfile, is_me: false, can_edit: false, display_name: 'Sam (wife view)' },
        { ...wifeProfile, is_me: true, can_edit: true },
      ],
      'GET /api/v1/profiles/p-husband/availability': noAvailability,
      'GET /api/v1/profiles/p-wife/availability': noAvailability,
      'GET /api/v1/couple': couple,
      'GET /api/v1/couple/constraints': constraints,
      'GET /api/v1/couple/important-dates': [],
      'GET /api/v1/notes': async () => {
        await heldNotes;
        return [];
      },
    });

    // The husband's entries are sitting in the cache, under his key, when the wife opens the app.
    const { queryClient } = renderAppWithClient('/us', (client) => {
      client.setQueryData(['notes', 'u-husband', 'note'], [husbandNote]);
      client.setQueryData(['notes', 'u-husband', 'mentioned_idea'], [husbandNote]);
      client.setQueryData(
        ['profiles', 'u-husband'],
        [{ ...husbandProfile, display_name: 'Stale husband view', can_edit: true }],
      );
    });

    expect(await screen.findByText('My notes about Sam (wife view)')).toBeInTheDocument();
    expect(screen.queryByText('Husband private note')).not.toBeInTheDocument();
    expect(screen.queryByText('Stale husband view')).not.toBeInTheDocument();
    releaseNotes();
    await vi.waitFor(() =>
      expect(queryClient.getQueryData(['notes', 'u-wife', 'note'])).toEqual([]),
    );
    expect(screen.queryByText('Husband private note')).not.toBeInTheDocument();
    // His entries were never touched: they are simply a different cache entry.
    expect(queryClient.getQueryData(['notes', 'u-husband', 'note'])).toEqual([husbandNote]);
  });
});
