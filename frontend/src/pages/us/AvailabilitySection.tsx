import { useState } from 'react';
import { Button } from '../../components/Button';
import { Card } from '../../components/EmptyState';
import { FormError, LoadError, SelectField, Skeleton, TextField } from '../../components/forms';
import {
  useAvailability,
  useProfiles,
  useSaveAvailability,
  type AvailabilityIO,
  type ProfileOut,
} from '../../hooks/useProfiles';
import { PERSON_CARD, WEEKDAYS, personOf } from './options';
import { profileName } from './ProfileSection';

/** The API returns "HH:MM:SS"; the time inputs want "HH:MM". */
const hhmm = (t: string) => t.slice(0, 5);

function AvailabilityForm({ profile, initial }: { profile: ProfileOut; initial: AvailabilityIO }) {
  const [windows, setWindows] = useState(() =>
    initial.windows.map((w) => ({
      ...w,
      start_time: hhmm(w.start_time),
      end_time: hhmm(w.end_time),
    })),
  );
  const [blocks, setBlocks] = useState(initial.blocks);
  const save = useSaveAvailability(profile.id);
  const name = profileName(profile);
  const editable = profile.can_edit;

  if (!editable) {
    return (
      <div className="flex flex-col gap-3">
        <p className="text-sm">{name} keeps their own availability, so it is read-only for you.</p>
        {windows.length === 0 && blocks.length === 0 ? (
          <p className="text-muted">Nothing shared yet.</p>
        ) : (
          <ul className="flex flex-col gap-1">
            {windows.map((w, i) => (
              <li key={`w${i}`}>
                {WEEKDAYS[w.weekday]}, {w.start_time} to {w.end_time}
              </li>
            ))}
            {blocks.map((b, i) => (
              <li key={`b${i}`}>
                Busy on {b.date}
                {b.note ? ` (${b.note})` : ''}
              </li>
            ))}
          </ul>
        )}
      </div>
    );
  }

  return (
    <form
      className="flex flex-col gap-5"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate({ windows, blocks });
      }}
    >
      <fieldset className="flex flex-col gap-3">
        <legend className="text-sm font-semibold">Usually free on</legend>
        {windows.length === 0 ? (
          <p className="text-muted">
            No weekly windows yet. Add the times that usually work, like Friday evenings.
          </p>
        ) : null}
        {windows.map((w, i) => (
          <div key={i} className="grid grid-cols-2 items-end gap-2 sm:grid-cols-[2fr_1fr_1fr_auto]">
            <SelectField
              label={`Day, window ${i + 1}`}
              value={w.weekday}
              onChange={(e) =>
                setWindows((all) =>
                  all.map((x, j) => (j === i ? { ...x, weekday: Number(e.target.value) } : x)),
                )
              }
            >
              {WEEKDAYS.map((d, n) => (
                <option key={d} value={n}>
                  {d}
                </option>
              ))}
            </SelectField>
            <TextField
              label={`Start, window ${i + 1}`}
              type="time"
              value={w.start_time}
              onChange={(e) =>
                setWindows((all) =>
                  all.map((x, j) => (j === i ? { ...x, start_time: e.target.value } : x)),
                )
              }
            />
            <TextField
              label={`End, window ${i + 1}`}
              type="time"
              value={w.end_time}
              onChange={(e) =>
                setWindows((all) =>
                  all.map((x, j) => (j === i ? { ...x, end_time: e.target.value } : x)),
                )
              }
            />
            <Button
              variant="secondary"
              aria-label={`Remove window ${i + 1}`}
              onClick={() => setWindows((all) => all.filter((_, j) => j !== i))}
            >
              Remove
            </Button>
          </div>
        ))}
        <div>
          <Button
            variant="secondary"
            onClick={() =>
              setWindows((all) => [...all, { weekday: 4, start_time: '18:00', end_time: '22:00' }])
            }
          >
            Add a weekly window
          </Button>
        </div>
      </fieldset>

      <fieldset className="flex flex-col gap-3">
        <legend className="text-sm font-semibold">Dates that are blocked</legend>
        {blocks.length === 0 ? (
          <p className="text-muted">No blocked dates. Add trips, deadlines or anything else.</p>
        ) : null}
        {blocks.map((b, i) => (
          <div key={i} className="grid grid-cols-2 items-end gap-2 sm:grid-cols-[1fr_2fr_auto]">
            <TextField
              label={`Blocked date ${i + 1}`}
              type="date"
              value={b.date}
              onChange={(e) =>
                setBlocks((all) =>
                  all.map((x, j) => (j === i ? { ...x, date: e.target.value } : x)),
                )
              }
            />
            <TextField
              label={`Note for blocked date ${i + 1}`}
              value={b.note ?? ''}
              onChange={(e) =>
                setBlocks((all) =>
                  all.map((x, j) => (j === i ? { ...x, note: e.target.value } : x)),
                )
              }
            />
            <Button
              variant="secondary"
              aria-label={`Remove blocked date ${i + 1}`}
              onClick={() => setBlocks((all) => all.filter((_, j) => j !== i))}
            >
              Remove
            </Button>
          </div>
        ))}
        <div>
          <Button
            variant="secondary"
            onClick={() => setBlocks((all) => [...all, { date: '', note: '' }])}
          >
            Block a date
          </Button>
        </div>
      </fieldset>

      <FormError error={save.error} />
      <div className="flex items-center gap-3">
        <Button type="submit" disabled={save.isPending}>
          {save.isPending ? 'Saving…' : 'Save availability'}
        </Button>
        {save.isSuccess ? (
          <span role="status" className="text-sm font-semibold">
            ✓ Saved
          </span>
        ) : null}
      </div>
    </form>
  );
}

function PersonAvailability({ profile }: { profile: ProfileOut }) {
  const availability = useAvailability(profile.id);
  const person = personOf(profile.slot);
  const name = profileName(profile);
  return (
    <div
      data-person={person}
      className={`flex flex-col gap-3 rounded-2xl border-2 p-3 ${PERSON_CARD[person]}`}
    >
      <h3 className="text-lg font-semibold">
        {profile.is_me ? 'Your availability' : `${name}'s availability`}
      </h3>
      {availability.isPending ? <Skeleton label={`Loading ${name}'s availability`} /> : null}
      {availability.isError ? (
        <LoadError error={availability.error} onRetry={() => void availability.refetch()} />
      ) : null}
      {availability.data ? (
        <AvailabilityForm profile={profile} initial={availability.data} />
      ) : null}
    </div>
  );
}

export function AvailabilitySection() {
  const profiles = useProfiles();
  return (
    <Card title="Availability">
      <div className="flex flex-col gap-4 pt-2">
        <p className="px-2 text-muted">
          When can each of you go out? Plans only suggest times that work for you both.
        </p>
        {profiles.isPending ? <Skeleton label="Loading availability" /> : null}
        {profiles.isError ? (
          <LoadError error={profiles.error} onRetry={() => void profiles.refetch()} />
        ) : null}
        {profiles.data?.map((p) => (
          <PersonAvailability key={p.id} profile={p} />
        ))}
      </div>
    </Card>
  );
}
