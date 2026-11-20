import type { Schemas } from '../api/client';
import type { Session } from '../hooks/useSession';

export function profile(overrides: Partial<Schemas['ProfileOut']> = {}): Schemas['ProfileOut'] {
  return {
    id: 'p-husband',
    slot: 'owner',
    is_me: true,
    subject_joined: true,
    can_edit: true,
    display_name: 'Sam',
    liked_categories: [],
    disliked_categories: [],
    cuisines_liked: [],
    cuisines_disliked: [],
    energy: null,
    social_setting: null,
    indoor_outdoor: null,
    loves: null,
    hard_nos: null,
    dietary: [],
    allergies: [],
    accessibility: null,
    edited_by_user_id: null,
    confirmed_by_subject_at: null,
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  };
}

export const husbandProfile = profile();
export const wifeProfile = profile({
  id: 'p-wife',
  slot: 'partner',
  is_me: false,
  subject_joined: false,
  display_name: 'Alex',
});

export function session(displayName: string | null, role = 'owner'): Session {
  return {
    authenticated: true,
    user: {
      id: 'u1',
      email: 'me@example.com',
      display_name: displayName,
      timezone: 'UTC',
      couple_id: 'c1',
      role,
    },
  };
}

export const couple: Schemas['CoupleOut'] = {
  id: 'c1',
  name: null,
  timezone: 'UTC',
  status: 'active',
  home_area: null,
  members: [],
  partner_joined: false,
};

export const constraints: Schemas['ConstraintsOut'] = {
  alcohol: null,
  budget_min: null,
  budget_max: null,
  currency: 'USD',
  kids_pets_notes: null,
  max_radius_km: null,
  max_travel_minutes: null,
  transport_modes: [],
  updated_at: '2026-01-01T00:00:00Z',
  updated_by_user_id: null,
};
