import { useState } from 'react';
import { useNavigate } from 'react-router';
import { Button, ButtonLink } from '../../components/Button';
import { Card } from '../../components/EmptyState';
import { FormError, SelectField } from '../../components/forms';
import { useSuggestTimes, type SuggestTimesIn, type TimeSlot } from '../../hooks/useCalendar';
import { useMakeItADate } from '../../hooks/useIdeas';
import { plannerError } from '../ideas/MakeItADateSheet';
import { dayKey, formatTime, timeValue } from './helpers';

const DURATIONS = [
  { value: '60', label: '1 hour' },
  { value: '120', label: '2 hours' },
  { value: '180', label: '3 hours' },
  { value: '240', label: '4 hours' },
];

const WHENS: { value: NonNullable<SuggestTimesIn['best_time']>; label: string }[] = [
  { value: 'any', label: 'Any' },
  { value: 'evening', label: 'Evening' },
  { value: 'day', label: 'Daytime' },
  { value: 'weekend', label: 'Weekend' },
];

const WEEKS = [1, 2, 3, 4, 5, 6];

const slotDay = new Intl.DateTimeFormat(undefined, { dateStyle: 'full' });

/** The plan page's prefill for a slot, in the browser's timezone; the end never crosses midnight. */
function planHref(slot: TimeSlot) {
  const start = new Date(slot.starts_at);
  const end = new Date(slot.ends_at);
  const endTime = dayKey(end) === dayKey(start) ? timeValue(end) : '23:59';
  return `/plan?date=${dayKey(start)}&start=${timeValue(start)}&end=${endTime}`;
}

/** Finds free evenings for both of you; `ideaId` narrows it to one idea's best time. */
export function SuggestTimes({ ideaId }: { ideaId?: string | null }) {
  const [duration, setDuration] = useState('180');
  const [when, setWhen] = useState<NonNullable<SuggestTimesIn['best_time']>>('any');
  const [weeks, setWeeks] = useState('3');
  const suggest = useSuggestTimes();
  const navigate = useNavigate();
  const makeDate = useMakeItADate(ideaId ?? '');

  return (
    <Card title="Suggest a time">
      <form
        className="flex flex-col gap-3 pt-2"
        onSubmit={(e) => {
          e.preventDefault();
          suggest.mutate({
            duration_minutes: Number(duration),
            weeks: Number(weeks),
            best_time: when,
            ...(ideaId ? { idea_id: ideaId } : {}),
          });
        }}
      >
        <SelectField
          label="How long"
          value={duration}
          onChange={(e) => setDuration(e.target.value)}
        >
          {DURATIONS.map((d) => (
            <option key={d.value} value={d.value}>
              {d.label}
            </option>
          ))}
        </SelectField>
        <SelectField
          label="When"
          value={when}
          onChange={(e) => setWhen(e.target.value as typeof when)}
        >
          {WHENS.map((w) => (
            <option key={w.value} value={w.value}>
              {w.label}
            </option>
          ))}
        </SelectField>
        <SelectField
          label="Look ahead (weeks)"
          value={weeks}
          onChange={(e) => setWeeks(e.target.value)}
        >
          {WEEKS.map((w) => (
            <option key={w} value={w}>
              {w}
            </option>
          ))}
        </SelectField>
        <Button type="submit" disabled={suggest.isPending}>
          {suggest.isPending ? 'Looking…' : 'Find free time'}
        </Button>
        <FormError error={suggest.error} />
      </form>
      {suggest.data?.slots.length === 0 ? (
        <p className="p-2 text-muted">No free time found. Try more weeks or a shorter date.</p>
      ) : null}
      {suggest.data && suggest.data.slots.length > 0 ? (
        <ul className="flex flex-col gap-3 pt-3">
          {suggest.data.slots.map((slot) => (
            <li
              key={slot.starts_at}
              className="flex flex-col gap-2 rounded-2xl border border-line bg-surface-2 p-3"
            >
              <span className="font-semibold">{slotDay.format(new Date(slot.starts_at))}</span>
              <span>
                {formatTime(slot.starts_at)} – {formatTime(slot.ends_at)}
              </span>
              <span className="text-sm text-muted">{slot.reason}</span>
              {ideaId ? (
                <Button
                  variant="secondary"
                  disabled={makeDate.isPending}
                  aria-label={`Make it a date on ${slotDay.format(new Date(slot.starts_at))}, ${formatTime(slot.starts_at)}`}
                  onClick={() =>
                    makeDate.mutate(
                      { starts_at: slot.starts_at, ends_at: slot.ends_at },
                      { onSuccess: (itinerary) => navigate(`/itineraries/${itinerary.id}`) },
                    )
                  }
                >
                  {makeDate.isPending ? 'Planning…' : 'Make it a date'}
                </Button>
              ) : (
                <ButtonLink to={planHref(slot)} variant="secondary">
                  Plan this
                </ButtonLink>
              )}
            </li>
          ))}
        </ul>
      ) : null}
      {ideaId ? <FormError error={plannerError(makeDate.error)} /> : null}
    </Card>
  );
}
