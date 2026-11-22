import { useState } from 'react';
import { Button } from '../../components/Button';
import { FormError, TextAreaField } from '../../components/forms';
import { usePutRating, type RatingIn, type RatingOut } from '../../hooks/useFeedback';

export const COMMENT_LIMIT = 280;

type Thumb = NonNullable<RatingIn['stop_thumbs']>[string];

export function Stars({ value }: { value: number }) {
  return (
    <span role="img" aria-label={`${value} out of 5 stars`}>
      {'★'.repeat(value)}
      <span className="text-muted">{'★'.repeat(5 - value)}</span>
    </span>
  );
}

export function RatingSheet({
  itineraryId,
  stops,
  initial,
  onClose,
}: {
  itineraryId: string;
  stops: { id: string; title: string }[];
  initial: RatingOut | null;
  onClose: () => void;
}) {
  const put = usePutRating(itineraryId);
  const [stars, setStars] = useState(initial?.stars ?? 0);
  const [wouldAgain, setWouldAgain] = useState<boolean | null>(initial?.would_again ?? null);
  const [comment, setComment] = useState(initial?.comment ?? '');
  const [thumbs, setThumbs] = useState<Record<string, Thumb>>(initial?.stop_thumbs ?? {});

  const setThumb = (stopId: string, thumb: Thumb) =>
    setThumbs((current) => {
      const next = { ...current };
      if (next[stopId] === thumb) delete next[stopId];
      else next[stopId] = thumb;
      return next;
    });

  return (
    <div
      className="fixed inset-0 z-10 flex items-end bg-ink/40 p-3 sm:items-center sm:justify-center"
      role="presentation"
    >
      <section
        className="max-h-full w-full max-w-lg overflow-y-auto rounded-3xl bg-surface p-5 shadow-xl"
        role="dialog"
        aria-modal="true"
        aria-labelledby="rating-title"
      >
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 id="rating-title" className="text-xl font-semibold">
              How was your date?
            </h2>
            <p className="mt-1 text-sm text-muted">
              Your rating stays private until you&apos;ve both rated.
            </p>
          </div>
          <Button variant="secondary" onClick={onClose} aria-label="Close rating">
            Close
          </Button>
        </div>
        <form
          className="mt-5 flex flex-col gap-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (stars < 1 || wouldAgain === null || put.isPending) return;
            const body: RatingIn = {
              stars,
              would_again: wouldAgain,
              comment: comment.trim().slice(0, COMMENT_LIMIT) || null,
              stop_thumbs: thumbs,
            };
            put.mutate(body, { onSuccess: onClose });
          }}
        >
          <fieldset className="flex flex-col gap-2">
            <legend className="text-sm font-semibold">Overall</legend>
            <div className="flex flex-wrap gap-2">
              {[1, 2, 3, 4, 5].map((n) => (
                <label
                  key={n}
                  className={`inline-flex min-h-11 min-w-11 cursor-pointer items-center justify-center rounded-full border px-3 text-lg font-semibold has-[:focus-visible]:ring-2 ${
                    n <= stars
                      ? 'border-2 border-accent bg-accent-soft text-accent'
                      : 'border-line bg-surface text-ink'
                  }`}
                >
                  <input
                    type="radio"
                    name="stars"
                    className="sr-only"
                    aria-label={`${n} ${n === 1 ? 'star' : 'stars'}`}
                    checked={stars === n}
                    onChange={() => setStars(n)}
                  />
                  <span aria-hidden="true">{n}★</span>
                </label>
              ))}
            </div>
          </fieldset>
          <div
            className="flex flex-col gap-2"
            role="group"
            aria-label="Would you do this date again?"
          >
            <span className="text-sm font-semibold">Would you do this date again?</span>
            <div className="flex gap-2">
              {([true, false] as const).map((option) => (
                <Button
                  key={String(option)}
                  type="button"
                  variant={wouldAgain === option ? 'primary' : 'secondary'}
                  aria-pressed={wouldAgain === option}
                  onClick={() => setWouldAgain(option)}
                >
                  {option ? 'Yes, again' : 'Not again'}
                </Button>
              ))}
            </div>
          </div>
          <TextAreaField
            label="A few words (optional)"
            value={comment}
            maxLength={COMMENT_LIMIT}
            onChange={(event) => setComment(event.target.value)}
            hint={`${comment.length}/${COMMENT_LIMIT}`}
            placeholder="What made it special?"
          />
          {stops.length > 0 ? (
            <fieldset className="flex flex-col gap-2">
              <legend className="text-sm font-semibold">How were the stops?</legend>
              <ul className="flex flex-col gap-2">
                {stops.map((stop) => (
                  <li key={stop.id} className="flex items-center justify-between gap-3">
                    <span className="min-w-0 truncate">{stop.title}</span>
                    <span className="flex shrink-0 gap-2">
                      {(['up', 'down'] as const).map((thumb) => (
                        <Button
                          key={thumb}
                          type="button"
                          variant={thumbs[stop.id] === thumb ? 'primary' : 'secondary'}
                          aria-pressed={thumbs[stop.id] === thumb}
                          aria-label={`Thumbs ${thumb} for ${stop.title}`}
                          onClick={() => setThumb(stop.id, thumb)}
                        >
                          {thumb === 'up' ? '👍' : '👎'}
                        </Button>
                      ))}
                    </span>
                  </li>
                ))}
              </ul>
            </fieldset>
          ) : null}
          <FormError error={put.error} />
          <Button type="submit" disabled={stars < 1 || wouldAgain === null || put.isPending}>
            {put.isPending ? 'Saving…' : 'Save rating'}
          </Button>
        </form>
      </section>
    </div>
  );
}
