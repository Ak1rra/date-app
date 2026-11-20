# Milestone 5 — Partner & match mode: design

Binding design for REQUIREMENTS §12.5 (FR-PAIR-1..3, FR-PROF-7 hand-over, FR-IDEA-*,
FR-VOTE-*, AI-GEN, realtime). Tables: `v1-schema.md` (`invites`, `votes`, `ideas`,
`profile_suggestions`).

## Pairing (email-login mode only; 404 while `NO_LOGIN=1`)

With `NO_LOGIN=1` the household is already paired the moment someone picks Wife, so
invites/join/leave use the `require_email_login` dependency and do not exist there.

| Method + path | Body → response | Rules |
|---|---|---|
| `POST /couple/invites` | → `{link, short_code, expires_at}` | owner only; revokes any active invite; token 32 bytes url-safe in the link fragment (`/join#token=`), `short_code` 8 chars from an unambiguous alphabet; only sha256 hashes stored; 7-day expiry (FR-PAIR-1) |
| `GET /couple/invites/current` / `DELETE` | → `{short_code, expires_at}` / 204 | owner |
| `POST /couple/join` | `{token} or {short_code}` → `MeOut` | signed-in user; invite active (unused, unrevoked, unexpired) else 404 `invite_invalid`; target couple has no partner (409 `couple_full`); the joiner's own solo couple is archived only if it has no partner (else 409 `already_paired`); the joiner becomes the `partner` member and the subject of the partner profile (`subject_user_id`); mark invite used; per-IP limiter like verify. All in one transaction with `pg_advisory_xact_lock` on the couple id |
| `POST /couple/leave` | → 204 | either member; `left_at` = now, couple `archived`; job `couple.purge` at +30 days deletes the couple's shared data unless re-paired (FR-PAIR-3); each member keeps a fresh solo couple with their own profile copied |

## Profile hand-over (FR-PROF-7)

- `ProfileOut` already has `confirmed_by_subject_at`. `POST /profiles/{id}/confirm`
  (subject only) sets it. The UI shows "Here's what {owner} told us you like" with
  Confirm / Edit until confirmed.
- After the subject joined, the other member cannot PUT (403, M1) but can
  `POST /profiles/{id}/suggestions {changes: partial ProfileIn, note?}` → pending
  suggestion; the subject lists `GET /profiles/{id}/suggestions?status=pending` and
  `POST /profiles/{id}/suggestions/{sid}/accept|decline` (accept applies the changes
  as a PUT by the subject). Authors see their own suggestions' status. (§14 Q4 decided:
  suggestions only, as the spec says.)

## Deck, votes, lists (FR-IDEA-0..4, FR-VOTE-1..6)

All through an `ideas_access` helper: shared ideas of my couple, `status='active'`,
excluding ideas with `is_surprise` whose surprise I'm the unrevealed surprisee of (M4).

| Method + path | Body → response | Rules |
|---|---|---|
| `GET /deck` | → `[IdeaOut]` (≤ 20) | shared ideas I haven't voted on, oldest first. Unpaired → `[]` plus `partner_joined: false` in the envelope `{cards, partner_joined}` |
| `PUT /ideas/{id}/vote` | `{value: yes|no|maybe}` → `IdeaOut` | shared idea only; 409 `vote_locked` once an itinerary made from it is scheduled (FR-VOTE-5) |
| `POST /deck/undo` | → `IdeaOut` or 204 | deletes my most recent vote (within 10 min, not locked) (FR-VOTE-6) |
| `GET /ideas/lists` | → `{matches, maybes, pending_partner, passed}` | after both voted: matches = yes+yes, maybes = no "no" and ≥1 maybe, passed = any no; `pending_partner` = I voted, partner hasn't. `IdeaOut.votes` = `{mine, partner}` where `partner` is null unless both voted (FR-VOTE-2) |
| `POST /ideas/generate` | `{mode: match|plan, count (5-10, default 8), window_start?, window_end?, budget_max?, vibe?, indoor_outdoor?, category?, surprise_me?: bool}` → `{ideas, degraded}` | AI-GEN, cap group ideas; match → shared (unvoted), plan → private. Grounded ideas keep provider data only; a model-claimed place/event not in `seen_*` is converted to an evergreen idea (AI-VAL-2). Diversity per AI-7 |
| `PATCH/DELETE /ideas/{id}` | | shared: either member (FR-IDEA-3); AI ideas both passed stay as negative signal (status active, in `passed`) |

`IdeaOut`: id, title, pitch, tags, category, est_cost, currency, duration_minutes,
best_time, indoor_outdoor, grounded, venue (name, rating, price_level, address,
distance_km from home), event (name, local date/time, url), valid_until, status,
may_have_changed (venue snapshot older than 30 d), visibility, source, created_by_me,
votes, attributions.

Job `ideas.expire` (daily, dedupe per day): grounded ideas with `valid_until < now()`
→ `expired` (FR-IDEA-4).

## Realtime (`/api/v1/ws`)

- WebSocket authenticated by the session cookie at handshake; the `Origin` header must
  equal `APP_ORIGIN` (else close 4403); no messages from the client except `ping`.
- In-process hub `app/realtime.py`: `couple_id → {(user_id, websocket)}`;
  `publish(couple_id, event, payload, *, only_user=None, except_user=None)`.
  Single process (systemd runs one uvicorn worker), so in-memory is enough.
- Events: `idea.created {id}`, `vote.updated {idea_id}` (never values),
  `match.created {idea_id, title}`, `itinerary.updated {id}` / `date.scheduled` /
  `date.cancelled` (sent per recipient through the access helper: a surprisee gets only
  `calendar.changed {}` with no ids for an unrevealed surprise), `hint.released {}`,
  `surprise.revealed {id}`, `rating.revealed {itinerary_id}`.
- Match → `match.created` to both, plus the M7 push hook to the partner who did not vote
  last (FR-VOTE-3).
- Client: one socket per app session, auto-reconnect with backoff, invalidates the
  matching TanStack queries; `match.created` shows the celebration overlay.

## Frontend

- Ideas page tabs: **Deck** (card stack: drag/swipe right yes, left no, up maybe with
  pointer events, plus buttons and ←/→/↑ keys, undo; empty → "Generate ideas" CTA;
  unpaired → invite CTA), **Matches**, **Maybes**, **Waiting on {name}**, **Passed**
  (collapsed), **My ideas** (M2). "Generate ideas" sheet with filters and match/plan
  mode. "Make it a date" on matches (M3 flow).
- Match celebration: warm, playful, motion-safe overlay; reduced motion → a static card.
- Invite (email-login mode): share link + short code, regenerate/revoke; `/join` page.
- Us page: hand-over banner and suggestion review for the subject; "Suggest a change"
  for the other member on a joined, read-only profile.
