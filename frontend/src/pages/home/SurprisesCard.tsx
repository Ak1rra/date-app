import { ButtonLink } from '../../components/Button';
import { Card } from '../../components/EmptyState';
import { countdownLabel } from '../../hooks/useHome';
import { useSurprises } from '../../hooks/useSurpriseView';

const dateTimeFormatter = new Intl.DateTimeFormat(undefined, {
  dateStyle: 'full',
  timeStyle: 'short',
});

/** The surprises the viewer is on the receiving end of. Absent unless there is one. */
export function SurprisesCard() {
  const surprises = useSurprises();
  const list = Array.isArray(surprises.data) ? surprises.data : [];
  if (surprises.isPending || surprises.isError || list.length === 0) return null;

  return (
    <Card title="A surprise for you ✨" className="md:col-span-2">
      <div className="flex flex-col gap-4 p-2">
        {list.map((surprise) => (
          <ButtonLink
            key={surprise.id}
            to={`/surprise/${surprise.id}`}
            variant="secondary"
            className="flex-col items-start rounded-2xl p-4 text-left"
          >
            <span className="flex w-full items-start justify-between gap-3">
              <span className="text-lg font-semibold">A surprise is coming</span>
              <span className="shrink-0 text-xs font-semibold uppercase tracking-wide text-accent">
                {countdownLabel(new Date(surprise.starts_at))}
              </span>
            </span>
            <time className="text-sm font-normal text-muted" dateTime={surprise.starts_at}>
              {dateTimeFormatter.format(new Date(surprise.starts_at))}
            </time>
            <span className="text-sm font-normal text-muted">
              {surprise.hints.length} {surprise.hints.length === 1 ? 'hint' : 'hints'} so far
            </span>
          </ButtonLink>
        ))}
      </div>
    </Card>
  );
}
