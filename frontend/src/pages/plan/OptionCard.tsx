import type { PlanOptionOut } from '../../hooks/usePlan';
import { Money } from '../itinerary/Money';

const KIND_LABEL: Record<PlanOptionOut['stops'][number]['kind'], string> = {
  food: 'Food',
  drink: 'Drinks',
  activity: 'Activity',
  event: 'Event',
  outdoors: 'Outdoors',
  other: 'Something else',
};

interface OptionCardProps {
  option: PlanOptionOut;
  selected: boolean;
  /** True when three others are already chosen, so this one cannot be added. */
  locked: boolean;
  onToggle: () => void;
}

/** One idea for the night: pitch, why it fits, a stop sketch, a rough cost, and a select box. */
export function OptionCard({ option, selected, locked, onToggle }: OptionCardProps) {
  return (
    <li
      className={`flex flex-col gap-3 rounded-3xl border bg-surface p-4 shadow-sm ${
        selected ? 'border-2 border-accent' : 'border-line'
      }`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex flex-col gap-1">
          <h3 className="text-lg font-semibold">{option.title}</h3>
          {option.occasion ? <p className="text-sm text-muted">{option.occasion}</p> : null}
        </div>
        <label className="flex min-h-11 shrink-0 items-center gap-2 text-sm font-semibold">
          <input
            type="checkbox"
            className="size-5 accent-[var(--accent)]"
            checked={selected}
            disabled={!selected && locked}
            aria-label={`Select ${option.title}`}
            onChange={onToggle}
          />
          Pick
        </label>
      </div>
      <div className="flex flex-wrap gap-2">
        <span className="rounded-full border border-line bg-surface-2 px-3 py-1 text-sm font-semibold">
          {option.grounded ? 'Grounded in real places' : 'Evergreen idea'}
        </span>
        {option.est_cost != null ? (
          <span className="rounded-full border border-line bg-surface-2 px-3 py-1 text-sm font-semibold">
            About <Money value={option.est_cost} />
          </span>
        ) : null}
      </div>
      <p>{option.pitch}</p>
      <p className="text-sm">
        <strong className="font-semibold">Why it fits them: </strong>
        {option.why_for_them}
      </p>
      {option.stops.length > 0 ? (
        <ol aria-label={`Stops for ${option.title}`} className="flex flex-col gap-1 text-sm">
          {option.stops.map((stop, index) => (
            <li key={`${index}-${stop.title}`} className="flex flex-wrap gap-x-2">
              <span className="font-semibold text-muted">{KIND_LABEL[stop.kind]}</span>
              <span>{stop.title}</span>
              {stop.est_cost != null ? (
                <span className="text-muted">
                  <Money value={stop.est_cost} />
                </span>
              ) : null}
            </li>
          ))}
        </ol>
      ) : null}
    </li>
  );
}
