import { useEffect, useState } from 'react';
import { ApiError } from '../../api/http';
import { Button } from '../../components/Button';
import { TextField } from '../../components/forms';
import { usePlaceSearch, type PlaceOut } from '../../hooks/useItineraries';
import { Sheet } from '../../components/Sheet';

export interface NewStop {
  title: string;
  place_id: string | null;
}

function searchError(error: unknown) {
  if (error instanceof ApiError && error.status === 503) {
    return 'Venue search is unavailable right now';
  }
  return "We couldn't search venues just now. Try again in a moment.";
}

function placeDetails(place: PlaceOut) {
  return [place.address, place.rating !== null ? `${place.rating} ★` : null]
    .filter(Boolean)
    .join(' · ');
}

export function AddStopSheet({
  onAdd,
  onClose,
}: {
  onAdd: (stop: NewStop) => void;
  onClose: () => void;
}) {
  const [query, setQuery] = useState('');
  const [debounced, setDebounced] = useState('');
  const [customTitle, setCustomTitle] = useState('');
  const search = usePlaceSearch(debounced);

  useEffect(() => {
    const timer = setTimeout(() => setDebounced(query), 300);
    return () => clearTimeout(timer);
  }, [query]);

  const places = search.data ?? [];

  return (
    <Sheet labelledBy="add-stop-title" onClose={onClose}>
      <div className="flex items-start justify-between gap-4">
        <h2 id="add-stop-title" className="text-xl font-semibold">
          Add a stop
        </h2>
        <Button variant="secondary" onClick={onClose} aria-label="Close add a stop">
          Close
        </Button>
      </div>
      <div className="mt-5 flex flex-col gap-4">
        <TextField
          label="Search for a venue"
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Tacos, a rooftop bar, a mini golf course…"
        />
        {search.isFetching ? (
          <p role="status" className="text-sm text-muted">
            Searching…
          </p>
        ) : null}
        {search.isError ? (
          <p role="alert" className="rounded-xl border border-coral p-3 text-sm">
            {searchError(search.error)}
          </p>
        ) : null}
        {search.isSuccess && places.length === 0 ? (
          <p className="text-sm text-muted">No venues found. Try another search.</p>
        ) : null}
        {places.length > 0 ? (
          <ul className="flex flex-col gap-2" aria-label="Search results">
            {places.map((place) => (
              <li key={place.place_id}>
                <button
                  type="button"
                  className="flex min-h-11 w-full flex-col items-start rounded-2xl border border-line p-3 text-left hover:bg-surface-2"
                  onClick={() => onAdd({ title: place.name, place_id: place.place_id })}
                >
                  <span className="font-semibold">{place.name}</span>
                  <span className="text-sm text-muted">{placeDetails(place)}</span>
                </button>
              </li>
            ))}
          </ul>
        ) : null}
        <form
          className="flex flex-col gap-3 border-t border-line pt-4"
          onSubmit={(event) => {
            event.preventDefault();
            const title = customTitle.trim();
            if (title) onAdd({ title, place_id: null });
          }}
        >
          <TextField
            label="Or add a custom stop"
            value={customTitle}
            onChange={(event) => setCustomTitle(event.target.value)}
            placeholder="Sunset walk home"
          />
          <Button type="submit" variant="secondary" disabled={!customTitle.trim()}>
            Add custom stop
          </Button>
        </form>
      </div>
    </Sheet>
  );
}
