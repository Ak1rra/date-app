import { useProfiles } from '../hooks/useProfiles';
import { AvailabilitySection } from './us/AvailabilitySection';
import { ConstraintsSection } from './us/ConstraintsSection';
import { HomeAreaSection } from './us/HomeAreaSection';
import { ImportantDatesSection } from './us/ImportantDatesSection';
import { NotesSection } from './us/NotesSection';
import { ProfileSection } from './us/ProfileSection';

export function UsPage() {
  const profiles = useProfiles();
  // Profile loading and failures are shown by ProfileSection; the notes need the other profile's id.
  const other = profiles.data?.find((p) => !p.is_me);
  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-bold">Us</h1>
      <ProfileSection />
      <AvailabilitySection />
      <ConstraintsSection />
      <HomeAreaSection />
      <ImportantDatesSection />
      {other ? <NotesSection other={other} /> : null}
    </div>
  );
}
