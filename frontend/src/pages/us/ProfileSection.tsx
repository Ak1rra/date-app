import { useState } from 'react';
import { Button } from '../../components/Button';
import { EmptyState } from '../../components/EmptyState';
import { FormError, LoadError, Skeleton } from '../../components/forms';
import {
  toProfileIn,
  useProfiles,
  useSaveProfile,
  type ProfileIn,
  type ProfileOut,
} from '../../hooks/useProfiles';
import { HandoverBanner } from './HandoverBanner';
import { FIELD_LABELS, ProfileFields } from './ProfileFields';
import { SuggestChange } from './SuggestChangeSheet';
import { SuggestionsReview } from './SuggestionsReview';
import { PERSON_CARD, personOf, pretty } from './options';

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
      <Row term={FIELD_LABELS.liked_categories}>{list(profile.liked_categories)}</Row>
      <Row term={FIELD_LABELS.disliked_categories}>{list(profile.disliked_categories)}</Row>
      <Row term={FIELD_LABELS.cuisines_liked}>{profile.cuisines_liked.join(', ')}</Row>
      <Row term={FIELD_LABELS.cuisines_disliked}>{profile.cuisines_disliked.join(', ')}</Row>
      <Row term={FIELD_LABELS.energy}>
        {profile.energy ? `${profile.energy} of 5 (chill to adventurous)` : null}
      </Row>
      <Row term={FIELD_LABELS.social_setting}>
        {profile.social_setting ? `${profile.social_setting} of 5 (quiet to lively)` : null}
      </Row>
      <Row term={FIELD_LABELS.indoor_outdoor}>
        {profile.indoor_outdoor ? pretty(profile.indoor_outdoor) : null}
      </Row>
      <Row term={FIELD_LABELS.loves}>{profile.loves}</Row>
      <Row term={FIELD_LABELS.hard_nos}>{profile.hard_nos}</Row>
      <Row term={FIELD_LABELS.dietary}>{list(profile.dietary)}</Row>
      <Row term={FIELD_LABELS.allergies}>{profile.allergies.join(', ')}</Row>
      <Row term={FIELD_LABELS.accessibility}>{profile.accessibility}</Row>
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
      <ProfileFields form={form} set={set} />
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

function ProfileCard({ profile, partnerName }: { profile: ProfileOut; partnerName: string }) {
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
          <HandoverBanner
            profile={profile}
            partnerName={partnerName}
            onEdit={() => setEditing(true)}
          />
          <SuggestionsReview profile={profile} partnerName={partnerName} />
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
          <SuggestChange profile={profile} name={name} />
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
  const partner = profiles.data.find((p) => !p.is_me);
  const partnerName = partner ? profileName(partner) : FALLBACK_NAME;
  return (
    <div className="flex flex-col gap-6">
      {profiles.data.map((p) => (
        <ProfileCard key={p.id} profile={p} partnerName={partnerName} />
      ))}
    </div>
  );
}
