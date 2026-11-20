import { useState } from 'react';
import { Button } from '../../components/Button';
import { Card } from '../../components/EmptyState';
import { FormError, LoadError, Skeleton, TextField } from '../../components/forms';
import {
  isProviderUnavailable,
  useCouple,
  usePatchCouple,
  useSetHomeArea,
  type CoupleOut,
} from '../../hooks/useCouple';

export const LOOKUP_UNAVAILABLE =
  "Location lookup isn't available right now, you can add it later.";

function HomeAreaForm({ couple }: { couple: CoupleOut }) {
  const [query, setQuery] = useState('');
  const [timezone, setTimezone] = useState(couple.timezone);
  const setHome = useSetHomeArea();
  const patch = usePatchCouple();

  return (
    <div className="flex flex-col gap-6 pt-2">
      <form
        className="flex flex-col gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          if (query.trim()) setHome.mutate(query.trim(), { onSuccess: () => setQuery('') });
        }}
      >
        {couple.home_area ? (
          <p className="px-2">
            Home area: <strong className="font-semibold">{couple.home_area.label}</strong>
          </p>
        ) : (
          <p className="px-2 text-muted">
            No home area yet. Tell us a neighbourhood or town and we will look for dates nearby.
          </p>
        )}
        <TextField
          label={couple.home_area ? 'Change home area' : 'Home area'}
          placeholder="e.g. Downtown Austin"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        {isProviderUnavailable(setHome.error) ? (
          <p role="alert" className="rounded-xl border border-coral p-3 text-sm">
            {LOOKUP_UNAVAILABLE}
          </p>
        ) : (
          <FormError error={setHome.error} />
        )}
        <div>
          <Button type="submit" disabled={setHome.isPending || !query.trim()}>
            {setHome.isPending ? 'Looking up…' : 'Save home area'}
          </Button>
        </div>
      </form>

      <form
        className="flex flex-col gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          if (timezone.trim()) patch.mutate({ timezone: timezone.trim() });
        }}
      >
        <TextField
          label="Time zone"
          hint="Used to show dates and times in your local time, like America/Chicago."
          value={timezone}
          onChange={(e) => setTimezone(e.target.value)}
        />
        <FormError error={patch.error} />
        <div className="flex items-center gap-3">
          <Button type="submit" disabled={patch.isPending}>
            {patch.isPending ? 'Saving…' : 'Save time zone'}
          </Button>
          {patch.isSuccess ? (
            <span role="status" className="text-sm font-semibold">
              ✓ Saved
            </span>
          ) : null}
        </div>
      </form>
    </div>
  );
}

export function HomeAreaSection() {
  const couple = useCouple();
  return (
    <Card title="Home area and time zone">
      {couple.isPending ? <Skeleton label="Loading home area" /> : null}
      {couple.isError ? (
        <LoadError error={couple.error} onRetry={() => void couple.refetch()} />
      ) : null}
      {couple.data ? <HomeAreaForm couple={couple.data} /> : null}
    </Card>
  );
}
