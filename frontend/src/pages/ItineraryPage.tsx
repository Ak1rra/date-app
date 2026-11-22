import { useState } from 'react';
import { useParams } from 'react-router';
import { ApiError } from '../api/http';
import { Button } from '../components/Button';
import { Card, EmptyState } from '../components/EmptyState';
import { FormError, LoadError, Skeleton } from '../components/forms';
import { usePersonTheme } from '../hooks/usePersonTheme';
import { useSession } from '../hooks/useSession';
import {
  useCancelItinerary,
  useIsRegenerating,
  useItinerary,
  type ItineraryOut,
} from '../hooks/useItineraries';
import { ChecklistSection } from './itinerary/ChecklistSection';
import { DetailsEditor } from './itinerary/DetailsEditor';
import { FeedbackSection } from './itinerary/FeedbackSection';
import { Money } from './itinerary/Money';
import { ReactionBar } from './itinerary/ReactionBar';
import { SaveTemplateSheet } from './itinerary/SaveTemplateSheet';
import { ScheduleSheet } from './itinerary/ScheduleSheet';
import { StopCard } from './itinerary/StopCard';
import { StopEditor } from './itinerary/StopEditor';
import { SurpriseSection } from './itinerary/SurpriseSection';
import { formatDateWindow } from './itinerary/dateWindow';

function warningText(value: Record<string, unknown>) {
  if (typeof value.message === 'string') return value.message;
  if (typeof value.text === 'string') return value.text;
  if (typeof value.code === 'string') return value.code;
  return (
    Object.values(value)
      .filter((part) => typeof part === 'string')
      .join(' · ') || 'A note for your date.'
  );
}

function weatherSummary(weather: ItineraryOut['weather']) {
  if (!weather) return null;
  const summary = typeof weather.summary === 'string' ? weather.summary : null;
  const fahrenheitSummary =
    summary?.replace(/(-?\d+) to (-?\d+) C\b/, (_, low, high) => {
      const fahrenheit = (celsius: string) => Math.round((Number(celsius) * 9) / 5 + 32);
      return `${fahrenheit(low)} to ${fahrenheit(high)}°F`;
    }) ?? summary;
  if (fahrenheitSummary !== summary) return fahrenheitSummary;

  const hourly = Array.isArray(weather.hourly) ? weather.hourly : [];
  const temperatures = hourly
    .filter((hour): hour is Record<string, unknown> => typeof hour === 'object' && hour !== null)
    .map((hour) => hour.temp_c)
    .filter((temp): temp is number => typeof temp === 'number' && Number.isFinite(temp));
  if (temperatures.length === 0) return summary;
  const low = Math.round((Math.min(...temperatures) * 9) / 5 + 32);
  const high = Math.round((Math.max(...temperatures) * 9) / 5 + 32);
  return `${summary ? `${summary} · ` : ''}${low}–${high}°F`;
}

export function ItineraryPage() {
  const { id } = useParams<{ id: string }>();
  const session = useSession();
  usePersonTheme(session.data?.user?.role);
  const itinerary = useItinerary(id);
  const cancel = useCancelItinerary(id ?? '');
  // A stop replacement rewrites the whole itinerary: hold every other change until it lands.
  const regenerating = useIsRegenerating(id ?? '');
  const [scheduleOpen, setScheduleOpen] = useState(false);
  const [editingStops, setEditingStops] = useState(false);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [templateOpen, setTemplateOpen] = useState(false);

  if (itinerary.isPending) return <Skeleton label="Loading your itinerary" />;
  if (itinerary.isError) {
    if (itinerary.error instanceof ApiError && itinerary.error.status === 404) {
      return (
        <Card>
          <EmptyState title="That date isn't here">
            It may have been swept away, but we can make you another lovely plan.
          </EmptyState>
        </Card>
      );
    }
    return <LoadError error={itinerary.error} onRetry={() => void itinerary.refetch()} />;
  }
  if (!itinerary.data) return null;
  const data = itinerary.data;
  const weather = weatherSummary(data.weather);
  const canChange = data.status === 'draft' || data.status === 'scheduled';

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2 text-sm font-semibold text-muted">
          <span className="rounded-full bg-surface-2 px-3 py-1 uppercase">{data.status}</span>
          {data.is_surprise ? (
            <span className="rounded-full border-2 border-accent bg-accent-soft px-3 py-1">
              Surprise
            </span>
          ) : null}
        </div>
        <h1 className="text-3xl font-bold">{data.title}</h1>
        <p className="text-muted">
          {formatDateWindow(data.starts_at, data.ends_at, 'full') ?? 'Time to be chosen'}
        </p>
      </header>

      {data.is_planner ? (
        <div className="flex flex-wrap gap-2">
          {canChange ? (
            <Button disabled={regenerating} onClick={() => setScheduleOpen(true)}>
              Schedule
            </Button>
          ) : null}
          {canChange && !editingStops ? (
            <Button
              variant="secondary"
              disabled={regenerating}
              onClick={() => setEditingStops(true)}
            >
              Edit stops
            </Button>
          ) : null}
          {canChange ? (
            <Button
              variant="secondary"
              disabled={regenerating}
              onClick={() => setDetailsOpen(true)}
            >
              Edit details
            </Button>
          ) : null}
          <Button variant="secondary" onClick={() => setTemplateOpen(true)}>
            Save as template
          </Button>
          {canChange ? (
            <Button
              variant="secondary"
              disabled={cancel.isPending || regenerating}
              onClick={() => {
                if (window.confirm('Cancel this date? You can always plan another one.'))
                  cancel.mutate();
              }}
            >
              Cancel
            </Button>
          ) : null}
          <FormError error={cancel.error} />
        </div>
      ) : (
        <ReactionBar itineraryId={data.id} />
      )}

      {editingStops && data.is_planner && canChange ? (
        <StopEditor itinerary={data} onClose={() => setEditingStops(false)} />
      ) : (
        <section className="flex flex-col gap-4" aria-label="Stops">
          <h2 className="text-xl font-semibold">The plan</h2>
          {data.stops.map((stop) => (
            <StopCard
              key={stop.id}
              stop={stop}
              currency={data.currency}
              itineraryId={data.is_planner && canChange ? data.id : undefined}
            />
          ))}
        </section>
      )}

      <Card title="The little details">
        <div className="grid gap-4 pt-3 sm:grid-cols-2">
          <p>
            <span className="font-semibold">Estimated total:</span>{' '}
            {data.est_total === null ? (
              <span>Not estimated</span>
            ) : (
              <Money value={data.est_total} currency={data.currency} />
            )}
            {data.est_total !== null && data.budget !== null ? (
              <>
                {' '}
                <span className="text-muted">
                  of <Money value={data.budget} currency={data.currency} /> budget
                </span>
              </>
            ) : null}
          </p>
          {weather ? (
            <p>
              <span className="font-semibold">Weather:</span> {weather}
            </p>
          ) : null}
          {data.backup_plan ? (
            <p>
              <span className="font-semibold">Backup plan:</span> {data.backup_plan}
            </p>
          ) : null}
          {data.bring_wear ? (
            <p>
              <span className="font-semibold">Bring / wear:</span> {data.bring_wear}
            </p>
          ) : null}
        </div>
      </Card>

      {data.warnings.length > 0 ? (
        <Card title="A heads-up">
          <ul className="flex flex-col gap-2 pt-3">
            {data.warnings.map((warning, index) => (
              <li key={index}>⚠ {warningText(warning)}</li>
            ))}
          </ul>
        </Card>
      ) : null}

      {data.is_planner &&
      data.is_surprise &&
      (data.status === 'draft' || data.status === 'scheduled') ? (
        <SurpriseSection itinerary={data} />
      ) : null}

      {data.status === 'completed' ? <FeedbackSection key={data.id} itinerary={data} /> : null}

      <ChecklistSection itinerary={data} canEdit={data.is_planner && canChange} />

      {data.attributions.length > 0 ? (
        <footer className="flex flex-col gap-1 border-t border-line pt-4 text-xs text-muted">
          {data.attributions.map((attribution, index) => (
            <p key={`${attribution}-${index}`}>{attribution}</p>
          ))}
        </footer>
      ) : null}

      {detailsOpen && data.is_planner && canChange ? (
        <DetailsEditor itinerary={data} onClose={() => setDetailsOpen(false)} />
      ) : null}

      {templateOpen && data.is_planner ? (
        <SaveTemplateSheet
          itineraryId={data.id}
          title={data.title}
          onClose={() => setTemplateOpen(false)}
        />
      ) : null}

      {scheduleOpen ? (
        <ScheduleSheet
          itineraryId={data.id}
          startsAt={data.starts_at}
          endsAt={data.ends_at}
          initialMode={data.is_surprise ? 'surprise' : 'announce'}
          onClose={() => setScheduleOpen(false)}
        />
      ) : null}
    </div>
  );
}
