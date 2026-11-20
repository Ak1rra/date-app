# Milestone 3 — Itineraries & calendar: design

Binding design for REQUIREMENTS §12.3. Builds on `m2-ai-plan.md` (the
`build_itinerary` pipeline, providers, caps) and `v1-schema.md`.

## Visibility (until M4 adds surprise redaction)

An itinerary is visible to its planner always. It is visible to the other member only
when `announced_at IS NOT NULL AND NOT is_surprise` (status `scheduled`, `completed` or
`cancelled`). Everything else is 404 for the other member. Only the planner edits;
the other member may only react. One helper, `services/itinerary_access.py`:
`visible_itineraries(session, viewer) -> Select` and `get_visible(session, viewer, id)`,
`get_editable(session, viewer, id)` (planner only, else 404 if not visible / 403 if
visible but not planner). Every itinerary, stop, checklist and calendar query goes
through it (M4 extends it in one place).

## API (`/api/v1`)

| Method + path | Body → response | Rules |
|---|---|---|
| `POST /ideas/{id}/itinerary` | `{starts_at?, ends_at?, stop_count? (1-5), budget_max?}` → `ItineraryOut` | AI-ITIN, FR-ITIN-1: idea must be visible to the requester; no time → first `suggest-times` slot; cap group builds |
| `PATCH /itineraries/{id}` | `{title?, starts_at?, ends_at?, budget?, backup_plan?, bring_wear?, invitation_message?}` → `ItineraryOut` | planner only. A time change on a scheduled date re-validates hours/weather/events (no AI) and replaces `warnings` (FR-CAL-3) |
| `PUT /itineraries/{id}/stops` | `{stops: [{id? (existing), title, place_id?, starts_at, ends_at, description?, est_cost?, notes?, reveal_policy?}]}` → `ItineraryOut` | FR-ITIN-5 replace-all in the given order: reorder, remove, add, change times. Venue snapshots refreshed by place id, travel legs recomputed, validation re-run (warnings, no AI) |
| `POST /itineraries/{id}/stops/{stop_id}/regenerate` | `{note? (≤300)}` → `ItineraryOut` | AI alternative for one stop only, given the neighbouring stops and times; cap group builds |
| `GET /places/search?q=&open_at=` | → `[PlaceOut]` (≤ 8, near the home area) | venue search for "add a stop"; 503 provider_unavailable |
| `POST /itineraries/{id}/checklist`, `PATCH/DELETE /itineraries/{id}/checklist/{item_id}` | `{text, kind, due_at?, link?}`, `{text?, kind?, due_at?, link?, done?: bool}` | planner only (FR-PLAN-6) |
| `POST /itineraries/{id}/schedule` | `{starts_at, ends_at?, mode: "announce"|"surprise", invitation_message?}` → `ItineraryOut` | FR-CAL-3/5, FR-PLAN-7: status → `scheduled`; announce sets `announced_at`; surprise sets `is_surprise=true`, `reveal_at` default `starts_at`. Allowed from draft or scheduled. Announced → surprise only if `seen_by_partner_at IS NULL` (FR-SURP-1) else 409 `already_seen`. Re-validates. Enqueues the M7 reminder jobs via a no-op hook `on_scheduled(itinerary)` |
| `POST /itineraries/{id}/cancel` | → `ItineraryOut` | status cancelled |
| `POST /itineraries/{id}/polish-invitation` | `{draft (≤1000)}` → `{text}` | fast tier, recorded as feature `polish` (FR-PLAN-7 "optionally AI-polished") |
| `POST /itineraries/{id}/react` | `{kind: "heart"|"move", suggested_start?, note? (≤300)}` → `ItineraryOut` | the non-planner only, on an itinerary visible to them; sets `partner_reaction` |
| `GET /itineraries?status=&from=&to=` | → `[ItinerarySummary]` | visible ones; GET of a visible announced itinerary by the non-planner sets `seen_by_partner_at` |
| `POST /itineraries/{id}/template` | `{name}` → `TemplateOut` | FR-PLAN-8 (SHOULD); snapshot = title, stops without times, budget, notes |
| `GET /templates`, `DELETE /templates/{id}`, `POST /templates/{id}/replan` | `{starts_at}` → `ItineraryOut` | planner-private; re-plan runs the build pipeline with the template as the steer (AI re-validates hours/events/weather) |
| `GET /calendar?from=&to=` | → `[CalendarEntry {kind: "date"|"occasion", id, title, starts_at, ends_at, all_day, itinerary_id?, status?, is_surprise?, announced?}]` | FR-CAL-1/2/6; ≤ 120-day range; visible itineraries only; occasions expanded (recurring yearly) in the couple timezone |
| `POST /calendar/suggest-times` | `{duration_minutes (30-720), weeks (1-6, default 3), idea_id?, best_time?}` → `{slots: [{starts_at, ends_at, reason}]}` (≤ 3) | FR-CAL-4, deterministic (no AI) |

`ItineraryOut` gains per-stop `maps: {apple, google, directions_from_previous?}` (FR-ITIN-6;
URL formats from `.claude/research/google-maps-platform.md`) and `booking_url`
(FR-ITIN-7, provider data only).

## Suggest times (FR-CAL-4)

Candidate days = the next `weeks` × 7 days (couple timezone), from tomorrow. For each
day, free windows = the intersection of both profiles' recurring windows for that weekday
(a profile with no windows at all counts as free 09:00–23:00), minus both profiles'
one-off blocked dates, minus visible scheduled itineraries (±60 min buffer). A slot fits
when a window holds `duration_minutes`. `best_time` (from the idea, or the request):
`evening` → start 17:00–20:00, `day` → 09:00–15:00, `weekend` → Sat/Sun only. An idea
with an event → only the event's start. Rank: weekend evenings first for `any`, then
earliest; at most one slot per day; return the top 3 with a short `reason` ("You're both
free Saturday evening").

## Offline (FR-ITIN-8)

`vite-plugin-pwa` `runtimeCaching`: `NetworkFirst` (3 s timeout) for
`/api/v1/calendar*`, `/api/v1/itineraries*`, `/api/v1/auth/session` in a cache named
`api-offline`; nothing else under `/api/` is cached. On profile switch or logout the app
deletes `api-offline` (shared-device safety: one profile's cached dates never show for
the other). The calendar page prefetches upcoming itineraries (next 30 days) so they are
available offline.

## Frontend

- `/itineraries/:id` becomes the editor (planner) / viewer (other member): stop list
  with drag-free reorder buttons (up/down, keyboard accessible), edit times, remove, "add
  a stop" (venue search), "try another" per stop, travel legs, cost vs budget, weather +
  backup, bring/wear, warnings on stops, map links (Apple / Google / directions),
  booking links, attributions; finishing-touches checklist with tick-off and due dates;
  "Schedule" sheet (date/time, Announce vs Surprise with the UX-7 badge, invitation
  message + "Polish it"); cancel; save as template. The partner view shows ❤️ / "Can we
  move it?" (suggested time + note).
- `/calendar`: month grid + agenda list (week view optional), entries colour-coded:
  occasions, announced dates, the planner's own surprises with a distinct badge "Only you
  can see this". Tapping a date opens the itinerary. "Suggest a time" from an idea or the
  plan page.
- Home: next date card, finishing touches due (unticked items with due dates in the next
  7 days), upcoming occasions (next 60 days).
- Ideas: "Make it a date" on an idea → time (or suggest) → AI itinerary.
