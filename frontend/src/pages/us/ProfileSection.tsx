import { useState } from 'react';
import { Button } from '../../components/Button';
import { EmptyState } from '../../components/EmptyState';
import {
  ChipGroup,
  FormError,
  LoadError,
  Segmented,
  SliderField,
  Skeleton,
  TagInput,
  TextAreaField,
  TextField,
} from '../../components/forms';
import {
  toProfileIn,
  useProfiles,
  useSaveProfile,
  type ProfileIn,
  type ProfileOut,
} from '../../hooks/useProfiles';
import { CATEGORIES, DIETARY, INDOOR_OUTDOOR, PERSON_CARD, personOf, pretty } from './options';

const FALLBACK_NAME = 'your partner';

export function profileName(p: ProfileOut): string {
  return p.display_name || (p.is_me ? 'You' : FALLBACK_NAME);
}

function hasAnyDetail(p: ProfileOut): boolean {
  return Boolean(
    p.liked_categories.length ||
    p.disliked_categories.length ||
    p.cuisines_liked.length ||
    p.cuisines_disliked.length ||
    p.dietary.length ||
    p.allergies.length ||
    p.energy ||
    p.social_setting ||
    p.indoor_outdoor ||
    p.loves ||
    p.hard_nos ||
    p.accessibility,
  );
}

function Row({ term, children }: { term: string; children: string | null | undefined }) {
  if (!children) return null;
  return (
    <div>
      <dt className="text-sm font-semibold text-muted">{term}</dt>
      <dd>{children}</dd>
    </div>
  );
}

const list = (values: string[]) => values.map(pretty).join(', ');

function ProfileSummary({ profile }: { profile: ProfileOut }) {
  return (
    <dl className="flex flex-col gap-3">
      <Row term="Likes">{list(profile.liked_categories)}</Row>
      <Row term="Not into">{list(profile.disliked_categories)}</Row>
      <Row term="Cuisines they like">{profile.cuisines_liked.join(', ')}</Row>
      <Row term="Cuisines they avoid">{profile.cuisines_disliked.join(', ')}</Row>
      <Row term="Energy">
        {profile.energy ? `${profile.energy} of 5 (chill to adventurous)` : null}
      </Row>
      <Row term="Social setting">
        {profile.social_setting ? `${profile.social_setting} of 5 (quiet to lively)` : null}
      </Row>
      <Row term="Indoor or outdoor">
        {profile.indoor_outdoor ? pretty(profile.indoor_outdoor) : null}
      </Row>
      <Row term="Loves">{profile.loves}</Row>
      <Row term="Hard no's">{profile.hard_nos}</Row>
      <Row term="Dietary">{list(profile.dietary)}</Row>
      <Row term="Allergies">{profile.allergies.join(', ')}</Row>
      <Row term="Accessibility">{profile.accessibility}</Row>
    </dl>
  );
}

function ProfileForm({ profile, onDone }: { profile: ProfileOut; onDone: () => void }) {
  const [form, setForm] = useState<ProfileIn>(() => toProfileIn(profile));
  const save = useSaveProfile();
  const set = <K extends keyof ProfileIn>(key: K, value: ProfileIn[K]) =>
    setForm((f) => ({ ...f, [key]: value }));

  return (
    <form
      className="flex flex-col gap-5"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate({ id: profile.id, body: form }, { onSuccess: onDone });
      }}
    >
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
      <FormError error={save.error} />
      <div className="flex flex-wrap gap-2">
        <Button type="submit" disabled={save.isPending}>
          {save.isPending ? 'Saving…' : 'Save profile'}
        </Button>
        <Button variant="secondary" onClick={onDone} disabled={save.isPending}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

function ProfileCard({ profile }: { profile: ProfileOut }) {
  const [editing, setEditing] = useState(false);
  const person = personOf(profile.slot);
  const name = profileName(profile);
  const heading = profile.is_me ? 'You' : `Here's what you know about ${name}`;
  return (
    <section
      aria-labelledby={`profile-${profile.id}`}
      data-person={person}
      className={`flex flex-col gap-4 rounded-3xl border-2 p-4 shadow-sm ${PERSON_CARD[person]}`}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id={`profile-${profile.id}`} className="text-xl font-bold">
          {heading}
        </h2>
        <span className="rounded-full border-2 border-current px-3 py-1 text-sm font-semibold text-ink">
          {person === 'husband' ? 'Husband' : 'Wife'}
          {profile.is_me ? ' (you)' : ''}
        </span>
      </div>
      {!profile.can_edit ? (
        <p className="rounded-xl border border-line bg-surface p-3 text-sm">
          {name} keeps their own profile, so this one is read-only for you.
        </p>
      ) : null}
      {editing && profile.can_edit ? (
        <ProfileForm profile={profile} onDone={() => setEditing(false)} />
      ) : (
        <>
          {hasAnyDetail(profile) ? (
            <ProfileSummary profile={profile} />
          ) : (
            <EmptyState title="Nothing here yet" headingLevel={3}>
              {profile.is_me
                ? 'Tell us what you enjoy so date ideas fit you.'
                : `Add what you know about ${name} so date ideas fit them.`}
            </EmptyState>
          )}
          {profile.can_edit ? (
            <div>
              <Button variant={person} onClick={() => setEditing(true)}>
                {profile.is_me ? 'Edit my profile' : `Edit ${name}'s profile`}
              </Button>
            </div>
          ) : null}
        </>
      )}
    </section>
  );
}

export function ProfileSection() {
  const profiles = useProfiles();
  if (profiles.isPending) return <Skeleton label="Loading profiles" />;
  if (profiles.isError) {
    return <LoadError error={profiles.error} onRetry={() => void profiles.refetch()} />;
  }
  return (
    <div className="flex flex-col gap-6">
      {profiles.data.map((p) => (
        <ProfileCard key={p.id} profile={p} />
      ))}
    </div>
  );
}
