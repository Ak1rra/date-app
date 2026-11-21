import {
  ChipGroup,
  Segmented,
  SliderField,
  TagInput,
  TextAreaField,
  TextField,
} from '../../components/forms';
import type { ProfileIn } from '../../hooks/useProfiles';
import { CATEGORIES, DIETARY, INDOOR_OUTDOOR, pretty } from './options';

/** The labels the profile summary and the suggestion diffs use for each editable field. */
export const FIELD_LABELS: Record<keyof ProfileIn, string> = {
  display_name: 'Name',
  liked_categories: 'Likes',
  disliked_categories: 'Not into',
  cuisines_liked: 'Cuisines they like',
  cuisines_disliked: 'Cuisines they avoid',
  energy: 'Energy',
  social_setting: 'Social setting',
  indoor_outdoor: 'Indoor or outdoor',
  loves: 'Loves',
  hard_nos: "Hard no's",
  dietary: 'Dietary',
  allergies: 'Allergies',
  accessibility: 'Accessibility',
};

/** A field's value as short text ("Not set" when empty), for showing old and new values. */
export function formatField(key: keyof ProfileIn, value: unknown): string {
  if (value === null || value === undefined || value === '') return 'Not set';
  if (Array.isArray(value)) {
    if (!value.length) return 'Not set';
    const enumValued =
      key === 'liked_categories' || key === 'disliked_categories' || key === 'dietary';
    return value.map((v) => (enumValued ? pretty(String(v)) : String(v))).join(', ');
  }
  if (key === 'energy' || key === 'social_setting') return `${String(value)} of 5`;
  if (key === 'indoor_outdoor') return pretty(String(value));
  return String(value);
}

/** The shared profile form fields: the edit form and the suggest-a-change sheet both render these. */
export function ProfileFields({
  form,
  set,
}: {
  form: ProfileIn;
  set: <K extends keyof ProfileIn>(key: K, value: ProfileIn[K]) => void;
}) {
  return (
    <>
      <TextField
        label="First name"
        value={form.display_name ?? ''}
        onChange={(e) => set('display_name', e.target.value)}
      />
      <ChipGroup
        label="Kinds of dates they like"
        options={CATEGORIES}
        value={form.liked_categories}
        onChange={(v) => set('liked_categories', v)}
        describe={pretty}
      />
      <ChipGroup
        label="Kinds of dates they are not into"
        options={CATEGORIES}
        value={form.disliked_categories}
        onChange={(v) => set('disliked_categories', v)}
        describe={pretty}
      />
      <TagInput
        label="Cuisines they like"
        value={form.cuisines_liked}
        onChange={(v) => set('cuisines_liked', v)}
        placeholder="e.g. Thai"
      />
      <TagInput
        label="Cuisines they avoid"
        value={form.cuisines_disliked}
        onChange={(v) => set('cuisines_disliked', v)}
        placeholder="e.g. Seafood"
      />
      <SliderField
        label="Energy"
        low="Chill"
        high="Adventurous"
        value={form.energy}
        onChange={(v) => set('energy', v)}
      />
      <SliderField
        label="Social setting"
        low="Quiet"
        high="Lively"
        value={form.social_setting}
        onChange={(v) => set('social_setting', v)}
      />
      <Segmented
        label="Indoor or outdoor"
        options={INDOOR_OUTDOOR}
        value={form.indoor_outdoor}
        onChange={(v) => set('indoor_outdoor', v)}
      />
      <TextAreaField
        label="Loves"
        hint="Things that always make them happy."
        value={form.loves ?? ''}
        onChange={(e) => set('loves', e.target.value)}
      />
      <TextAreaField
        label="Hard no's"
        hint="Anything that is off the table."
        value={form.hard_nos ?? ''}
        onChange={(e) => set('hard_nos', e.target.value)}
      />
      <ChipGroup
        label="Dietary needs"
        options={DIETARY}
        value={form.dietary}
        onChange={(v) => set('dietary', v)}
        describe={pretty}
      />
      <TagInput
        label="Allergies"
        value={form.allergies}
        onChange={(v) => set('allergies', v)}
        placeholder="e.g. Peanuts"
      />
      <TextAreaField
        label="Accessibility"
        hint="Anything that affects where you can go, like stairs or long walks."
        value={form.accessibility ?? ''}
        onChange={(e) => set('accessibility', e.target.value)}
      />
    </>
  );
}
