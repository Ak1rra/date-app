# Milestone 4 — Surprise mode: design

Covers FR-SURP-1..8 and FR-NOTIF-5. The schema already exists (v1-schema): `itineraries.is_surprise`,
`reveal_at`, `revealed_at`; `stops.reveal_policy`; `surprise_hints`; `ideas.is_surprise`. M4 adds
the rules, the routes and the scheduler work. No migration.

Words: the **planner** is `itineraries.planner_user_id`; the **surprisee** is the other couple
member (there may be none yet).

## Visibility (one place: `services/itinerary_access.py`)

An itinerary is visible to a viewer when any of:

1. the viewer is the planner;
2. it is announced (`announced_at` set) and not a surprise;
3. it is a surprise and has been revealed (`revealed_at` set).

Anything else is a 404 for the viewer, exactly as before. Before reveal, the surprisee reaches a
surprise **only** through the redacted surprise view below and the calendar's redacted entry.

`upcoming_surprises(session, viewer, couple)` (same module) selects the surprises the viewer is the
unrevealed surprisee of: same couple, planner ≠ viewer, `is_surprise`, `revealed_at IS NULL`,
`status = 'scheduled'`, `starts_at IS NOT NULL`.

## Redacted surprise view (FR-SURP-3)

- `GET /api/v1/surprises` → `list[SurpriseOut]`, ordered by `starts_at`.
- `GET /api/v1/surprises/{itinerary_id}` → `SurpriseOut`, 404 unless it is in `upcoming_surprises`.

```
SurpriseOut   { id, starts_at, ends_at, reveal_at, hints: list[ReleasedHintOut] }
ReleasedHintOut { id, kind, text, released_at }      # released_at IS NOT NULL only, by released_at
```

No title, stops, places, costs, invitation, checklist or weather: nothing else leaves the server.

Calendar (`services/calendar.list_entries`): for each `upcoming_surprises` row starting in range,
the surprisee gets `kind="date"`, `title="Surprise date ✨"`, `starts_at`, `ends_at`,
`itinerary_id=<id>`, `status="scheduled"`, `is_surprise=True`, `announced=False`. The frontend
links it to `/surprise/<id>`.

## Per-stop reveal (FR-SURP-7)

After reveal, in `itinerary_out` for a viewer who is not the planner, a stop with
`reveal_policy = 'at_stop_time'` whose `starts_at` is in the future is redacted:
`title = "Surprise stop ✨"`; `description`, `notes`, `place_id`, `event`, `venue`,
`booking_url`, `est_cost` = null; `warnings = []`; `maps` with no links; `travel_to_next` = null.
The leg into it (the previous stop's `travel_to_next`) is null, and the next stop's map
directions do not use it as their origin. The planner always sees everything.

## Hints (planner; FR-SURP-4)

Routes under `/api/v1/itineraries/{itinerary_id}`; access as `get_editable` (404 hidden, 403
partner); 409 `not_editable` unless the status is draft or scheduled; 409 `not_a_surprise` unless
`is_surprise`.

| Route | Body | Result |
|---|---|---|
| `GET /hints` | — | `list[HintOut]` by `release_at` |
| `POST /hints` | `HintIn { kind, text (1–300, stripped), release_at: datetime \| null }` | 201 `HintOut`; null = now; at most 20 per itinerary (409 `too_many_hints`) |
| `PATCH /hints/{hint_id}` | `HintPatch` (all optional) | `HintOut`; 409 `already_released` once released |
| `DELETE /hints/{hint_id}` | — | 204, any time |
| `PUT /reveal-at` | `{ reveal_at: datetime \| null }` (null = `starts_at`) | `ItineraryOut`; 409 `already_revealed`; 422 if after `coalesce(ends_at, starts_at)` |
| `POST /reveal` | — | `ItineraryOut`; reveal now; status must be scheduled |

`HintOut { id, kind, text, release_at, released_at }`. `kind` is the DB check list: `dress_code,
bring, ready_by, eat, energy, indoor_outdoor, weather, travel, custom`.

AI "suggest hints" (FR-SURP-5) is a separate, later route: `POST /hints/suggest`.

## Release and reveal (FR-SURP-6, FR-NOTIF-2/5)

`services/surprise.process_due(session, itinerary, settings, now)` is the only place that
releases and reveals. It does nothing unless the itinerary is a surprise with status
`scheduled`. Then:

- every hint with `release_at <= now` and no `released_at` gets `released_at = now`, and the
  surprisee is notified: kind `hint_released`, title "A new hint for your surprise ✨", body = the
  hint text, `data.url = /surprise/<id>`, dedupe `hint:<hint id>`;
- if `revealed_at` is null and `reveal_at <= now`, then `revealed_at = now`, and the surprisee is
  notified: kind `surprise_revealed`, title "Your surprise is revealed ✨", body "Tap to see what's
  planned.", `data.url = /itineraries/<id>`, dedupe `reveal:<id>`.

With no surprisee (unpaired), hints are still marked released so the planner sees them as due and
can copy them (FR-SURP-1's share-ready message is built in the UI from the hint text).

Notifications never name a venue, stop or title (FR-NOTIF-5): only the planner's own hint text.

Scheduling: worker job kind `surprise.due`, payload `{itinerary_id}`, `run_at` = the time,
dedupe `surprise:<id>:<iso time>`. `schedule_due_jobs(session, itinerary)` enqueues one per
distinct future time (each unreleased hint's `release_at`, and `reveal_at`). It runs after a hint
create or patch, a reveal-at change, and a surprise-mode `schedule()`. The handler loads the
itinerary and calls `process_due`; stale jobs are harmless because `process_due` re-checks state.
Times at or before now are processed in the request itself (call `process_due` before
returning), so "release immediately" is immediate and never depends on a job whose dedupe key
already ran.

`reveal-now` sets `reveal_at = revealed_at = now` and notifies as above.

Rescheduling a surprise (`schedule` with mode surprise) resets `reveal_at` to the new
`starts_at` (existing behaviour, kept).

## Other surfaces (FR-SURP-2, FR-SURP-8)

- Ideas (`services/ideas_access.py`): an idea with `is_surprise` is visible only to its creator,
  in the deck, lists, lookups and edits.
- AI context (`ai/context.build_context`): the requester's idea list excludes other members'
  `is_surprise` ideas, always. (`exclude_surprises_for` stays accepted; the rule no longer
  needs it.)
- `check_calendar` already returns times only; suggest-times busy windows are times only.
- `GET /itineraries`, `GET /itineraries/{id}`, react, checklist: all through `visible_itineraries`.
- Chat (M6) must apply the same visibility when it lands.

## Frontend (later wave)

- Planner, on the itinerary page of a surprise: a Hints section (presets + free text, release
  time: now / N hours or days before the start / a date-time), reveal time, "Reveal now", a copy
  button per released hint when unpaired.
- Surprisee: `/surprise/:id` (start, approximate end, released hints, countdown) linked from the
  calendar entry and a Home card from `GET /surprises`.
