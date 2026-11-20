# Milestone 4 — Surprise mode: design

Binding design for REQUIREMENTS §12.4 (FR-SURP-1..8, FR-NOTIF-5, AI-SAFE-3,
NFR-SEC-2). Extends `services/itinerary_access.py` from M3: that module is the only
place itinerary visibility is decided.

## Roles

For an itinerary with `is_surprise = true`: the **planner** is `planner_user_id`; the
**surprisee** is the other active member of the couple. If the couple has no other
member (unpaired), there is no surprisee, and hints/reveal are shown to the planner as
share-ready text (FR-SURP-1).

## What the surprisee sees

`revealed` = `revealed_at IS NOT NULL`. Until revealed, the surprisee gets a
`SurpriseView` and nothing else:

```
SurpriseView { id, view: "surprise", title: "Surprise date ✨", starts_at,
               approx_ends_at (ends_at rounded UP to the next half hour, or starts_at + 3 h),
               hints: [{kind, text, released_at}]  -- released only, newest last,
               revealed: false }
```

- `GET /itineraries/{id}` → `SurpriseView` (200) for an unrevealed surprise whose status
  is `scheduled` (drafts and cancelled unrevealed surprises stay 404 for the surprisee;
  a cancelled one is simply gone from their calendar).
- `GET /itineraries` and `GET /calendar` → the entry with `kind: "surprise"`, the same
  redacted fields, no title/stops/venue/idea/plan session/checklist.
- After reveal: the normal `ItineraryOut`, except stops with `reveal_policy =
  "at_stop_time"` whose `starts_at > now()` are replaced by `{position, starts_at,
  redacted: true, title: "Revealed at <local time>"}` and their travel legs are dropped
  (FR-SURP-7).
- Checklist items, plan sessions, the source idea's link and `warnings` are never sent to
  the surprisee (checklist is planner-only for every itinerary anyway).
- `POST /itineraries/{id}/react` stays available to the surprisee only after reveal.

## Hints (FR-SURP-4/5)

| Method + path | Body → response | Rules |
|---|---|---|
| `GET /itineraries/{id}/hints` | → `[HintOut {id, kind, text, release_at, released_at, share_text}]` | planner only (others 404) |
| `POST /itineraries/{id}/hints` | `{kind, text (1-300), release: {at?: datetime} or {hours_before?: int 0-336}}` → `HintOut` | planner; itinerary is_surprise; `release_at` = `at`, or `starts_at - hours_before`, or now (immediately); must be ≤ reveal time; enqueues job `surprise.hint` (dedupe `hint:<id>:<release_at>`) |
| `PATCH /itineraries/{id}/hints/{hint_id}` | same fields, only while unreleased | re-enqueues |
| `DELETE /itineraries/{id}/hints/{hint_id}` | 204, only while unreleased | |
| `POST /itineraries/{id}/hints/suggest` | `{}` → `{suggestions: [{kind, text}]}` (3-5) | fast tier, AI-HINT; teasing but non-spoiling (never names a venue, street, event or exact activity); not saved |

Presets for `kind` text in the UI: dress code, what to bring, be ready by, eat beforehand
/ don't eat, energy level, indoor/outdoor, weather gear, rough travel. `share_text`
(unpaired, or always as a convenience): "Hint for our date on Saturday: wear comfy shoes ✨".

Job `surprise.hint`: load the hint; if released, deleted, the itinerary is no longer a
surprise or is cancelled, or `release_at > now()` (stale job) → do nothing; else set
`released_at = now()` and call the M7 notification hook
`notify(surprisee, kind="hint_released", title="A new hint ✨", body="There's a new hint for your surprise date.")`
— generic text, never details (FR-NOTIF-5).

## Reveal (FR-SURP-6)

- `reveal_at` default `starts_at`, settable through `PATCH /itineraries/{id}` (planner;
  must be ≤ `ends_at`). Scheduling a surprise or changing `reveal_at`/`starts_at` enqueues
  `surprise.reveal` with dedupe `reveal:<id>:<reveal_at iso>`.
- `POST /itineraries/{id}/reveal` (planner) → reveal now.
- Job `surprise.reveal`: if still a surprise, scheduled, unrevealed and `reveal_at <=
  now()` → set `revealed_at`, notify the surprisee "Your surprise is revealed! 🎉" (no
  details). Overdue jobs run on recovery (NFR-REL-1); a stale job (reveal_at moved later)
  does nothing.
- Per-stop timed reveal needs no job: it is evaluated at read time.
- Turning a surprise back into an announced date (`schedule` with mode announce) is
  allowed any time and acts as a reveal.

## Isolation everywhere (FR-SURP-2/8, AI-SAFE-3, NFR-SEC-2)

- `build_context(..., exclude_surprises_for=requester)` drops every unrevealed surprise
  the requester is the surprisee of, plus its idea and plan session; `check_calendar`
  shows those windows to the surprisee as `{"busy": true, "label": "Busy"}` only.
- Ideas: an idea used by an unrevealed surprise itinerary is hidden from the surprisee in
  every idea list, the deck and matches when it is private (it already is) — and
  `POST /ideas/{id}/itinerary` / scheduling as surprise on a **shared** idea sets
  `ideas.is_surprise = true` so the deck/matches (M5) hide it from the surprisee until
  reveal. M5's queries must honour `ideas.is_surprise` via the access helper.
- Notifications and realtime (M5/M7): any event about a surprise goes to the planner with
  details, and to the surprisee only as the generic hint/reveal texts above.
- Chat (M6): the surprisee's AI context never holds surprise data; assistant messages in
  the shared thread produced from the planner's context are not used for surprise info.

## Tests (high-risk, xhigh)

A parametrized sweep as the surprisee over every endpoint that can return itinerary or
idea data (`/itineraries`, `/itineraries/{id}`, `/itineraries/{id}/hints`,
`/calendar`, `/ideas`, `/ideas/{id}`, `/plan/sessions*`, and later `/deck`,
`/matches`, `/chat/*`, `/notifications`, the WebSocket), asserting that no title, stop,
venue, place id, description, checklist text, unreleased hint or plan text of the
surprise appears anywhere in the response body (search the raw JSON for marker
strings). Plus: hint and reveal jobs idempotent, stale jobs no-op, missed reveal fires
on recovery, per-stop reveal by time, unpaired share text, announced → surprise blocked
after `seen_by_partner_at`.

## Frontend

- Planner: the itinerary page shows the surprise state unmistakably (UX-7: a distinct
  badge and tint, "Only you can see this"), a hints manager (presets + free text,
  release time: now / N hours before / at a time), "Suggest hints" (approve/edit each),
  reveal time and "Reveal now", per-stop "reveal at stop time" toggle, and copy buttons
  for share-ready text when unpaired.
- Surprisee: calendar and home show "Surprise date ✨" with the time, a countdown and the
  released hints; after reveal, the full itinerary with a small celebration (motion-safe).
