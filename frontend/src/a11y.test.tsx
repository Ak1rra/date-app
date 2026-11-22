import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, it, vi } from 'vitest';
import type { Schemas } from './api/client';
import type { ItineraryOut } from './hooks/useItineraries';
import { expectNoAxeViolations } from './test/axe';
import { constraints, couple, husbandProfile, session, wifeProfile } from './test/fixtures';
import { mockApi, type Handler } from './test/mockApi';
import { renderApp } from './test/renderApp';

// An automated axe pass over every top-level page, once its data has loaded. Colour contrast is
// off in the helper (jsdom can't compute it), so contrast is still a by-eye check.

const meta = { demo_mode: false, version: 'test', no_login: true };
const noAvailability = { windows: [], blocks: [] };
const alex = { ...wifeProfile, subject_joined: true };
const hoursFromNow = (hours: number) => new Date(Date.now() + hours * 3_600_000).toISOString();

const itinerary: ItineraryOut = {
  id: 'i1',
  idea_id: null,
  planner_user_id: 'u1',
  is_planner: true,
  title: 'A very good night',
  starts_at: hoursFromNow(48),
  ends_at: hoursFromNow(52),
  status: 'scheduled',
  announced_at: null,
  invitation_message: null,
  is_surprise: false,
  reveal_at: null,
  revealed_at: null,
  seen_by_partner_at: null,
  budget: 120,
  est_total: 95,
  currency: 'USD',
  weather: null,
  backup_plan: 'Movie and snacks',
  bring_wear: 'Wear something cute',
  warnings: [],
  partner_reaction: null,
  created_at: '2026-11-01T00:00:00Z',
  updated_at: '2026-11-01T00:00:00Z',
  stops: [
    {
      id: 's1',
      position: 0,
      title: 'Dinner',
      description: 'A table for two',
      starts_at: hoursFromNow(48),
      ends_at: hoursFromNow(50),
      est_cost: 95,
      notes: null,
      place_id: null,
      reveal_policy: 'with_itinerary',
      booking_url: null,
      evergreen: false,
      maps: { google: 'https://maps.google.com', apple: null, directions_from_previous: null },
      venue: { name: 'The Cozy Table', address: '1 Main St', rating: 4.8, price_level: 2 },
      event: null,
      warnings: [],
      travel_to_next: null,
    },
  ],
  has_evergreen: false,
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
  attributions: [],
};

const completed: ItineraryOut = {
  ...itinerary,
  status: 'completed',
  starts_at: '2026-11-10T18:00:00Z',
  ends_at: '2026-11-10T22:00:00Z',
};

function idea(overrides: Partial<Schemas['IdeaOut']> = {}): Schemas['IdeaOut'] {
  return {
    id: 'i1',
    title: 'Pottery night',
    pitch: 'Get messy together.',
    est_cost: 60,
    currency: 'USD',
    tags: ['creative', 'cozy'],
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

const anniversary = {
  id: 'd1',
  kind: 'anniversary',
  name: 'Anniversary',
  date: '2026-12-24',
  profile_id: null,
  recurring: true,
};

const at = (y: number, m: number, d: number, h = 0) => new Date(y, m - 1, d, h).toISOString();

/** What every signed-in page and the app shell ask for; a page's own routes go on top. */
const shared: Record<string, Handler> = {
  'GET /api/v1/auth/session': session('Sam'),
  'GET /api/v1/meta': meta,
  'GET /api/v1/profiles': [husbandProfile, alex],
  'GET /api/v1/notifications/unread-count': { count: 1 },
  'GET /api/v1/itineraries/i1': itinerary,
};

interface Case {
  name: string;
  path: string;
  routes?: Record<string, Handler>;
  /** What to wait for so the page has settled; anything findable that only shows once loaded. */
  ready: () => Promise<unknown>;
}

const cases: Case[] = [
  {
    name: 'Home',
    path: '/',
    routes: {
      'GET /api/v1/itineraries': [
        {
          id: 'i1',
          title: 'A very good night',
          status: 'scheduled',
          starts_at: itinerary.starts_at,
          ends_at: itinerary.ends_at,
          stop_count: 1,
          announced: true,
          is_planner: true,
          is_surprise: false,
        },
      ],
      'GET /api/v1/surprises': [],
      'GET /api/v1/couple/important-dates': [anniversary],
    },
    ready: () => screen.findByText('A very good night'),
  },
  {
    name: 'Ideas',
    path: '/ideas',
    routes: {
      'GET /api/v1/deck': {
        cards: [idea(), idea({ id: 'i2', title: 'Rooftop movie', pitch: 'Blankets and popcorn.' })],
        partner_joined: true,
      },
      'GET /api/v1/ideas/lists': { matches: [], maybes: [], pending_partner: [], passed: [] },
      'GET /api/v1/ideas': [],
    },
    ready: () => screen.findByRole('heading', { name: 'Pottery night' }),
  },
  {
    name: 'Plan',
    path: '/plan',
    routes: { 'GET /api/v1/plan/sessions': [] },
    ready: () => screen.findByLabelText(/What are you in the mood for/),
  },
  {
    name: 'Calendar',
    path: '/calendar',
    routes: {
      'GET /api/v1/calendar': [
        {
          id: 'e1',
          kind: 'date',
          title: 'Sushi night',
          starts_at: at(2026, 11, 24, 19),
          ends_at: at(2026, 11, 24, 22),
          all_day: false,
          announced: true,
          is_surprise: false,
          status: 'scheduled',
          itinerary_id: 'i1',
        },
        {
          id: 'e4',
          kind: 'occasion',
          title: 'Alex birthday',
          starts_at: at(2026, 11, 28),
          ends_at: at(2026, 11, 29),
          all_day: true,
        },
      ],
      'GET /api/v1/surprises': [],
    },
    ready: () => screen.findAllByText('Sushi night'),
  },
  {
    name: 'Itineraries',
    path: '/itineraries',
    routes: {
      'GET /api/v1/itineraries': [
        {
          id: 'i1',
          title: 'A very good night',
          status: 'scheduled',
          starts_at: itinerary.starts_at,
          ends_at: itinerary.ends_at,
          stop_count: 1,
          announced: true,
          is_planner: true,
          is_surprise: false,
        },
      ],
      'GET /api/v1/past-dates': [
        {
          id: 'p1',
          title: 'Picnic in the park',
          starts_at: '2026-11-10T18:00:00Z',
          ends_at: null,
          is_surprise: false,
          planner_user_id: 'u1',
          mine: {
            stars: 4,
            would_again: true,
            comment: null,
            stop_thumbs: {},
            updated_at: '2026-11-11T00:00:00Z',
          },
          partner: null,
          planner_read: null,
        },
      ],
    },
    ready: () => screen.findByText('Picnic in the park'),
  },
  {
    name: 'Itinerary (completed, with feedback)',
    path: '/itineraries/i1',
    routes: {
      'GET /api/v1/itineraries/i1': completed,
      'GET /api/v1/itineraries/i1/ratings': {
        mine: {
          stars: 4,
          would_again: true,
          comment: 'So sweet',
          stop_thumbs: {},
          updated_at: '2026-11-11T00:00:00Z',
        },
        partner: null,
        partner_revealed: false,
        planner_read: {
          enjoyment: 3,
          highlights: 'The dessert',
          misses: 'The queue',
          updated_at: '2026-11-11T00:00:00Z',
        },
      },
    },
    ready: () => screen.findByText(/So sweet/),
  },
  {
    name: 'Chat',
    path: '/chat',
    routes: {
      'GET /api/v1/chat/threads': [
        { id: 't-mine', scope: 'private', title: null, updated_at: '2026-01-01T00:00:00Z' },
      ],
      'GET /api/v1/chat/threads/t-mine/messages': {
        messages: [
          {
            id: 'm1',
            role: 'user',
            content: 'Plan something cosy',
            actions: [],
            author_is_me: true,
            author_user_id: 'u1',
            created_at: '2026-01-01T10:00:00Z',
          },
          {
            id: 'm2',
            role: 'assistant',
            content: 'How about pottery?',
            actions: [
              {
                id: 'a1',
                type: 'idea',
                summary: 'Try a pottery class',
                payload: { title: 'Pottery night' },
                result: null,
                applied_at: null,
              },
            ],
            author_is_me: false,
            author_user_id: null,
            created_at: '2026-01-01T10:00:05Z',
          },
        ],
        has_more: false,
      },
    },
    ready: () => screen.findByText('How about pottery?'),
  },
  {
    name: 'Us',
    path: '/us',
    routes: {
      'GET /api/v1/profiles/p-husband/availability': noAvailability,
      'GET /api/v1/profiles/p-wife/availability': noAvailability,
      'GET /api/v1/couple': couple,
      'GET /api/v1/couple/constraints': constraints,
      'GET /api/v1/couple/important-dates': [anniversary],
      'GET /api/v1/notes': [],
    },
    ready: () => screen.findByRole('heading', { name: 'You' }),
  },
  {
    name: 'Profile',
    path: '/profile',
    routes: {
      'GET /api/v1/notifications/push-config': { public_key: 'AQID-_8' },
      'GET /api/v1/me/notification-settings': {
        kinds: { reminder_day_before: true, match: false },
        quiet_start: '22:00',
        quiet_end: '08:00',
      },
    },
    ready: () => screen.findByLabelText('Quiet hours start'),
  },
  {
    name: 'Notifications',
    path: '/notifications',
    routes: {
      'GET /api/v1/notifications': [
        {
          id: 'n1',
          title: 'Your date is ready',
          body: 'The plan is waiting for you.',
          kind: 'itinerary_ready',
          data: { url: '/itineraries/i1' },
          created_at: hoursFromNow(-1),
          deliver_after: hoursFromNow(-1),
          pushed_at: null,
          read_at: null,
        },
      ],
    },
    ready: () => screen.findByRole('button', { name: /Your date is ready/ }),
  },
  {
    name: 'Templates',
    path: '/templates',
    routes: {
      'GET /api/v1/templates': [
        {
          id: 't1',
          name: 'Classic night',
          created_at: '2026-11-20T00:00:00Z',
          source_itinerary_id: 'i1',
          title: 'Dinner and a movie',
          stop_titles: ['Dinner', 'Movie'],
        },
      ],
      'GET /api/v1/itineraries': [],
    },
    ready: () => screen.findByRole('heading', { name: 'Classic night' }),
  },
  {
    name: 'Install',
    path: '/install',
    ready: () => screen.findByRole('heading', { level: 1, name: /Get Date Planner/ }),
  },
  {
    name: 'Privacy',
    path: '/privacy',
    ready: () => screen.findByRole('heading', { level: 1 }),
  },
  {
    name: 'SignIn',
    path: '/signin',
    routes: { 'GET /api/v1/auth/session': { authenticated: false, user: null } },
    ready: () => screen.findByRole('heading', { level: 1 }),
  },
  {
    name: 'Verify',
    path: '/auth/verify',
    routes: { 'GET /api/v1/auth/session': { authenticated: false, user: null } },
    ready: () => screen.findByRole('heading', { level: 1, name: 'Checking your link' }),
  },
  {
    name: 'Join',
    path: '/join',
    routes: { 'GET /api/v1/meta': { ...meta, no_login: false } },
    ready: () => screen.findByRole('button', { name: 'Join' }),
  },
  {
    name: 'Setup',
    path: '/setup',
    routes: { 'GET /api/v1/auth/session': session(null) },
    ready: () => screen.findByLabelText('Your first name'),
  },
  {
    name: 'Surprise',
    path: '/surprise/s1',
    routes: {
      'GET /api/v1/surprises/s1': {
        id: 's1',
        starts_at: hoursFromNow(48),
        ends_at: hoursFromNow(51),
        reveal_at: hoursFromNow(48),
        hints: [
          {
            id: 'h1',
            kind: 'dress_code',
            text: 'Something cosy',
            released_at: hoursFromNow(-30),
          },
          { id: 'h2', kind: 'bring', text: 'A scarf', released_at: hoursFromNow(-3) },
        ],
      },
    },
    ready: () => screen.findByText('A scarf'),
  },
];

let layout: HTMLStyleElement;

beforeEach(() => {
  // No Tailwind in jsdom: hide what the phone layout hides, or the shell shows both its headers.
  layout = document.createElement('style');
  layout.textContent = '.hidden { display: none; }';
  document.head.appendChild(layout);
  // jsdom has no matchMedia; the install and swipe code asks it for display-mode / motion.
  vi.stubGlobal(
    'matchMedia',
    vi.fn((query: string) => ({
      matches: false,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  );
});

afterEach(() => {
  layout.remove();
  vi.unstubAllGlobals();
});

describe('accessibility (axe)', () => {
  for (const { name, path, routes, ready } of cases) {
    it(`${name} has no axe violations`, async () => {
      mockApi({ ...shared, ...routes });
      renderApp(path);
      await ready();
      await expectNoAxeViolations(document.body);
    });
  }

  it('Ideas lists and My ideas tabs have no axe violations', async () => {
    const mine = idea({
      id: 'i3',
      title: 'Board game cafe',
      created_by_me: true,
      visibility: 'private',
    });
    mockApi({
      ...shared,
      'GET /api/v1/deck': { cards: [], partner_joined: true },
      'GET /api/v1/ideas/lists': {
        matches: [idea({ votes: { mine: 'yes', partner: 'yes' } })],
        maybes: [],
        pending_partner: [],
        passed: [],
      },
      'GET /api/v1/ideas': [mine],
    });
    renderApp('/ideas');
    await userEvent.click(await screen.findByRole('tab', { name: /Matches/ }));
    await screen.findByRole('heading', { name: 'Pottery night' });
    await expectNoAxeViolations(document.body);
    await userEvent.click(screen.getByRole('tab', { name: 'My ideas' }));
    await screen.findByRole('heading', { name: 'Board game cafe' });
    await expectNoAxeViolations(document.body);
  });

  it('Profile with the "Your data" confirm panel open has no axe violations', async () => {
    mockApi({
      ...shared,
      'GET /api/v1/notifications/push-config': { public_key: 'AQID-_8' },
      'GET /api/v1/me/notification-settings': {
        kinds: { reminder_day_before: true },
        quiet_start: '22:00',
        quiet_end: '08:00',
      },
    });
    renderApp('/profile');
    await userEvent.click(await screen.findByRole('button', { name: "Reset this profile's data" }));
    await screen.findByRole('alertdialog');
    await expectNoAxeViolations(document.body);
  });
});
