import type { Schemas } from '../../api/client';

type ProfileIn = Schemas['ProfileIn'];
type ConstraintsIn = Schemas['ConstraintsIn'];

/** The enum values the API accepts (typed from the generated schema, so a drift fails typecheck). */
export const CATEGORIES = [
  'food',
  'outdoors',
  'arts',
  'music',
  'sports',
  'games',
  'learning',
  'nightlife',
  'wellness',
  'travel',
  'shopping',
  'nature',
  'culture',
  'adventure',
  'relaxing',
] as const satisfies readonly ProfileIn['liked_categories'][number][];

export const DIETARY = [
  'vegetarian',
  'vegan',
  'pescatarian',
  'gluten_free',
  'dairy_free',
  'halal',
  'kosher',
  'nut_free',
  'low_carb',
] as const satisfies readonly ProfileIn['dietary'][number][];

export const TRANSPORT_MODES = [
  'car',
  'transit',
  'walk',
  'rideshare',
  'bike',
] as const satisfies readonly ConstraintsIn['transport_modes'][number][];

export const INDOOR_OUTDOOR: { value: NonNullable<ProfileIn['indoor_outdoor']>; label: string }[] =
  [
    { value: 'indoor', label: 'Indoor' },
    { value: 'outdoor', label: 'Outdoor' },
    { value: 'either', label: 'Either' },
  ];

export const ALCOHOL: { value: NonNullable<ConstraintsIn['alcohol']>; label: string }[] = [
  { value: 'yes', label: 'Yes' },
  { value: 'some', label: 'Some' },
  { value: 'none', label: 'None' },
];

/** Display text for an enum value: "gluten_free" -> "Gluten free". */
export function pretty(value: string): string {
  const spaced = value.replace(/_/g, ' ');
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

export const WEEKDAYS = [
  'Monday',
  'Tuesday',
  'Wednesday',
  'Thursday',
  'Friday',
  'Saturday',
  'Sunday',
] as const;

/** Slot owner is the husband; the partner slot is the wife. */
export function personOf(slot: 'owner' | 'partner'): 'husband' | 'wife' {
  return slot === 'owner' ? 'husband' : 'wife';
}

/** Whole-card tint per person. Full class names so Tailwind can see them. */
export const PERSON_CARD: Record<'husband' | 'wife', string> = {
  husband: 'border-husband bg-husband-soft',
  wife: 'border-wife bg-wife-soft',
};
