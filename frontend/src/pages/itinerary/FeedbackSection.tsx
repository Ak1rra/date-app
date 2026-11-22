import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router';
import { Button } from '../../components/Button';
import { Card } from '../../components/EmptyState';
import { LoadError, Skeleton } from '../../components/forms';
import { useRatings, type RatingOut } from '../../hooks/useFeedback';
import type { ItineraryOut } from '../../hooks/useItineraries';
import { PlannerReadForm } from './PlannerReadForm';
import { RatingSheet, Stars } from './RatingSheet';

function RatingSummary({ rating }: { rating: RatingOut }) {
  return (
    <div className="flex flex-col gap-1">
      <p>
        <Stars value={rating.stars} />
      </p>
      <p>{rating.would_again ? 'Would do it again' : 'Not one to repeat'}</p>
      {rating.comment ? <p className="text-muted">&ldquo;{rating.comment}&rdquo;</p> : null}
    </div>
  );
}

export function FeedbackSection({ itinerary }: { itinerary: ItineraryOut }) {
  const ratings = useRatings(itinerary.id);
  const [params, setParams] = useSearchParams();
  const [sheetOpen, setSheetOpen] = useState(false);
  const wantsRate = params.get('rate') === '1';
  const loaded = ratings.data !== undefined;
  const hasMine = ratings.data?.mine != null;

  // A rate prompt opens the sheet once (if I haven't rated yet), then drops the param.
  const [promptedFor, setPromptedFor] = useState<string | null>(null);
  if (wantsRate && loaded && promptedFor !== itinerary.id) {
    setPromptedFor(itinerary.id);
    if (!hasMine) setSheetOpen(true);
  }
  useEffect(() => {
    if (!wantsRate || !loaded) return;
    setParams(
      (current) => {
        const next = new URLSearchParams(current);
        next.delete('rate');
        return next;
      },
      { replace: true },
    );
  }, [wantsRate, loaded, setParams]);

  if (ratings.isPending) return <Skeleton label="Loading ratings" />;
  if (ratings.isError) {
    return <LoadError error={ratings.error} onRetry={() => void ratings.refetch()} />;
  }
  const { mine, partner, partner_revealed, planner_read } = ratings.data;

  return (
    <section className="flex flex-col gap-4" aria-label="Feedback">
      <h2 className="text-xl font-semibold">How it went</h2>
      <Card title="Your rating">
        <div className="flex flex-col items-start gap-3 pt-3">
          {mine ? (
            <>
              <RatingSummary rating={mine} />
              <Button variant="secondary" onClick={() => setSheetOpen(true)}>
                Edit
              </Button>
            </>
          ) : (
            <Button onClick={() => setSheetOpen(true)}>Rate this date</Button>
          )}
        </div>
      </Card>
      <Card title="Partner's rating">
        <div className="pt-3">
          {partner_revealed && partner ? (
            <RatingSummary rating={partner} />
          ) : (
            <p className="text-muted">
              Hidden until you&apos;ve both rated (or a week after the date)
            </p>
          )}
        </div>
      </Card>
      {itinerary.is_planner ? (
        <PlannerReadForm
          key={`${itinerary.id}:${planner_read?.updated_at ?? 'none'}`}
          itineraryId={itinerary.id}
          saved={planner_read}
        />
      ) : null}
      {sheetOpen ? (
        <RatingSheet
          itineraryId={itinerary.id}
          stops={itinerary.stops.map((stop) => ({ id: stop.id, title: stop.title }))}
          initial={mine}
          onClose={() => setSheetOpen(false)}
        />
      ) : null}
    </section>
  );
}
