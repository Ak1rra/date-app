# Date App — Requirements

> Status: v1 requirements, agreed 2026-11-18 in a planning interview with the owner.
> Audience: the development session that will build the app. Requirements use
> IDs (`FR-…`, `AI-…`, `NFR-…`) so work, tests and reviews can cite them.
> **MUST** = required for v1. **SHOULD** = v1 if feasible. **v2** = explicitly deferred.
> **Implementation status** (audited 2026-11-22 at commit 7625e66): ✅ Complete · 🚧 Partial · ⚠️ Built, target not measured · ⬜ Not built · ⏭️ Deferred (v2).

---

## 1. Product summary

An AI-powered web app for **one partner to plan great dates for the other**, with
an optional shared side for deciding together.

**Primary persona: the planner.** The owner (a husband planning dates for his
wife) is the main user. The app must let him **fully plan a date on his own**,
start to finish: get AI ideas tailored to his wife's tastes, build a grounded
itinerary (real venues, events, weather, travel time), schedule it, and either
**announce** it to her or keep it a **surprise** that she sees only as hints
until a timed reveal. He can maintain her preference profile himself; she never
has to fill anything in for the app to work.

**Secondary persona: the partner.** The wife can join the couple space (optional).
When she does, the couple can also **swipe on ideas and match** (both say yes),
she sees announced dates and surprise hints, and she rates dates afterwards. Her
ratings, and the planner's own post-date notes about how she liked it, feed AI
personalization.

So there are two equal-weight ways to get to a date:
1. **Plan mode (primary):** the planner decides alone, privately, and presents the result.
2. **Match mode:** ideas go into a shared swipe deck and become dates when both say yes.

The rest of the experience (itineraries on a shared **in-app calendar**,
reminders, post-date ratings) is common to both.

**Scope stance:** built for personal use (the owner and their partner) first, but
**architected to be public-ready**: real auth, multi-tenant data isolation,
per-couple cost controls, privacy hygiene. No monetization in v1.

### Goals
1. Let the planner go from "I want to take her out Saturday" to a complete, bookable plan in a few minutes.
2. Suggestions that are actually doable: real places, open at that time, in budget, weather-appropriate.
3. Tailored to **her**: the AI plans around the partner's tastes and the planner's knowledge of them, not generic "couple" ideas.
4. When deciding together, both get an equal, private say (swipe + match); surprises stay surprises.
5. The app gets better at suggesting the more dates are rated.

### Non-goals (v1)
- Native iOS/Android apps. It is an installable PWA.
- Two-way sync with iPhone/iCloud/Google calendars (in-app calendar only, see §6.5).
- Booking or payment (reservations and tickets are deep links out).
- Photo journal / memories timeline (only the quick post-date rating is in v1).
- Budget tracking of actual spend.
- Dating-for-singles, multi-partner, or group/friends planning.
- Monetization, subscriptions, ads.

---

## 2. Users and roles

| Role | Description |
|---|---|
| **Owner / primary planner** | The user who created the couple space; the app is designed around his workflow. Can use **every planning feature with no partner account**. |
| **Partner** | The other member (optional account). Once joined, has equal permissions on shared data (shared ideas, calendar, ratings, shared chat) and can also plan dates herself, including surprises for the owner. |
| **Planner** | Whoever created a given date (per-date role). Usually the owner. |
| **Recipient / surprisee** | The other member, for a given planned or surprise date (per-date role). |

**Unpaired operation (MUST):** before the partner joins, the owner can do everything
except match-mode voting and partner-facing notifications: maintain both profiles,
generate ideas, build itineraries, schedule dates (announced or surprise) on the
calendar, and record his own post-date notes. When the partner joins, all existing
dates and the profile he wrote for her carry over (see FR-PROF-7).
| **Admin** (ops) | Owner-level operator: can view usage/cost metrics, adjust caps. No in-app admin UI required in v1 (a CLI/script or env config is fine). |

A user belongs to **at most one couple** in v1.

---

## 3. Tech stack (decided)

| Layer | Choice |
|---|---|
| Backend | **Python 3.12+, FastAPI** (async), Pydantic v2 models, Uvicorn |
| Database | **PostgreSQL 17** (Debian's packaged server; was 16, see §14 decision 6), SQLAlchemy 2.x (async) + **Alembic** migrations |
| Background jobs | A worker for scheduled work (reminders, reveals, rating prompts, cache refresh). Use a Postgres-backed queue/scheduler or Redis + a task runner (e.g. ARQ/Celery). Dev session decides; it must survive restarts. |
| Realtime | **WebSockets** (or SSE) from FastAPI so partner actions (votes, matches, schedule changes) appear live |
| Frontend | **React + TypeScript + Vite**, **Tailwind CSS**, **TanStack Query**, React Router; typed API client generated from FastAPI's OpenAPI schema |
| PWA | Installable (manifest + service worker), offline read of the calendar and upcoming itineraries, **Web Push** notifications |
| AI | **OpenAI API** (official `openai` Python SDK, Responses API) with **tool use** |
| Deploy | **No Docker** (superseded, see §14 decision 5): one **systemd service** on the owner's Linux box, managed by the app's own `dateapp` CLI in the style of brothereye (`build`, `serve`, `daemon install/update/...`). FastAPI serves the built frontend; the job runner runs in-process. Served over HTTPS by the owner's Caddy at `https://dates.lan`. |
| Testing | pytest (+ httpx AsyncClient) for backend; Vitest + React Testing Library for frontend; Playwright for a small e2e smoke suite |

All secrets come from environment variables; a `.env.example` documents every one.

---

## 4. Core user flows

1. **Onboarding.** The owner signs up, creates the couple space, and fills in **his preferences and hers** (he knows her tastes), plus shared constraints and important dates. Inviting his partner is offered but optional and can happen any time.
2. **Plan a date (primary flow).** The owner opens **Plan a date**, gives a time window and any steer ("Saturday evening, under $150, something she's never done, she's been stressed"), or just a date. The AI proposes a few grounded options **privately**; he picks one (or mixes stops), the AI builds the full **itinerary**, he edits it, and then chooses:
   - **Announce** it: it appears on the shared calendar and she's notified (if joined), or
   - **Surprise** it: she sees only the time slot and hints until the reveal.
   (He can also say "plan the whole thing" and get a single complete itinerary straight away.)
3. **Match mode (optional).** Either partner generates or adds ideas to the **shared swipe deck**. Each swipes yes / maybe / no privately; a mutual yes is a **match**. Any match can then be turned into an itinerary and scheduled exactly as in flow 2. The planner can also move any of his private ideas into the shared deck to see if she's into it.
4. **Go.** Reminders fire beforehand with a weather heads-up; the itinerary is available offline with map links.
5. **Rate.** After the date, each member rates it: 1–5 stars, "would do again", optional one-liner. The planner can also record **how she liked it** (his observations), which is especially useful while she has no account. All of this feeds personalization.

---

## 5. Functional requirements

### 5.1 Accounts, auth & pairing

- **FR-AUTH-1 (MUST)** Sign-up/sign-in via **email magic link**. Session uses secure, HttpOnly cookies (or short-lived access + refresh tokens); CSRF-protected. — ✅ *Complete (switched off while NO_LOGIN=1, decision 7).*
- **FR-AUTH-3 (MUST)** Sign out, sign out all sessions, delete account (see NFR-PRIV). — ✅ *Complete.*
- **FR-PAIR-0 (MUST)** Signing up creates the user's couple space immediately; the app is fully usable for planning with only one member (see §2 "Unpaired operation"). — ✅ *Complete.*
- **FR-PAIR-1 (MUST)** The owner can generate a single-use **invite link + short code** at any time, valid 7 days, revocable, regenerable. — ✅ *Complete.*
- **FR-PAIR-2 (MUST)** Joining via invite links the second user to the couple. A couple has exactly 2 members max. — ✅ *Complete.*
- **FR-PAIR-3 (MUST)** Either partner can **leave/unpair**. On unpair: the couple space is archived; each partner keeps their own individual profile; shared data is retained for 30 days then deleted unless re-paired (state this in the UI). — ✅ *Complete.*
- **FR-PAIR-4 (MUST)** All couple-owned data is visible only to that couple's members (authorization enforced server-side on every query; see NFR-SEC-2). — ✅ *Complete.*

### 5.2 Preference profile

Individual (per partner) **and** shared (per couple) fields.

- **FR-PROF-1 (MUST) Interests & vibes (individual):** liked/disliked activity categories (food, outdoors, arts, music, sports, games, learning, nightlife, wellness, travel…), cuisines liked/disliked, energy level (chill ↔ adventurous slider), indoor/outdoor preference, social setting (quiet ↔ lively), free-text "things I love" / "hard no's". — ✅ *Complete.*
- **FR-PROF-2 (MUST) Constraints (shared):** default budget range per date, currency; **home area** (address/neighborhood → geocoded lat/lng, stored at neighborhood precision), max travel radius / max travel time; transport modes (car, transit, walk, rideshare); dietary restrictions and allergies (per partner); accessibility needs; alcohol preference; kids/pets/childcare considerations. — ✅ *Complete.*
- **FR-PROF-3 (MUST) Important dates (shared):** anniversary, each partner's birthday, plus custom occasions (name, date, recurring yearly y/n). The AI uses these for occasion-aware suggestions (e.g. when an idea request falls near one). — ✅ *Complete.*
- **FR-PROF-4 (MUST) Availability (individual):** each partner marks recurring usual free windows (e.g. "weeknights after 7pm", "Saturdays all day") and can add one-off blocked dates. Used to suggest times (FR-CAL-4). — ✅ *Complete.*
- **FR-PROF-5 (MUST)** Profiles are editable any time; changes apply to subsequent AI requests. — ✅ *Complete.*
- **FR-PROF-6 (SHOULD)** Onboarding flow presents the profile as a short, playful multi-step quiz rather than a long form; all fields except home area are skippable. — 🚧 *Partial: Setup is three steps, not a playful quiz; required names are not skippable.*
- **FR-PROF-7 (MUST) Planner-maintained partner profile:** the owner can fill in and edit the partner's individual profile (FR-PROF-1, dietary/accessibility, availability) on her behalf, whether or not she has joined. When she joins, she is shown that profile ("Here's what {owner} told us you like"), can confirm or edit it, and from then on she owns it (the owner can still view it and suggest changes she must accept). — ✅ *Complete.*
- **FR-PROF-8 (MUST) Planner's notes about her:** a private, free-text notes area visible only to the owner ("loves little bookstores", "wants to try pottery", "hates loud bars", sizes, favorite flowers), and an **"ideas she's mentioned"** quick-capture list. Both are included in the AI's context for his planning requests only (never shown to her, never used in her AI requests). — ✅ *Complete.*

### 5.3 Plan mode (primary planner flow)

- **FR-PLAN-1 (MUST)** A **Plan a date** entry point that is the most prominent action on the home screen. — ✅ *Complete.*
- **FR-PLAN-2 (MUST)** Inputs: date or window (or "suggest a time", FR-CAL-4), optional budget override, optional free-text steer (mood, occasion, "something new", "low-key because she's tired"), optional must-include (e.g. a specific restaurant) and the option of surprise vs announced (changeable later). — ✅ *Complete.*
- **FR-PLAN-3 (MUST)** Two speeds: — ✅ *Complete.*
  - **Options:** the AI returns 3 grounded date concepts (each a short pitch + sketch of stops + est. cost); the planner picks one, asks for more, or says "combine the dinner from #1 with the activity from #3". Then a full itinerary is built (§5.5).
  - **Plan it all:** one complete validated itinerary in a single step.
- **FR-PLAN-4 (MUST)** Everything in plan mode is **private to the planner** until he announces it or schedules it as a surprise. Unused options are saved to his **private idea list** (not the shared deck) for later. — ✅ *Complete.*
- **FR-PLAN-5 (MUST)** Plans are tailored primarily to the **recipient's** profile and the planner's notes about her (FR-PROF-8), while still respecting both partners' hard constraints (dietary, accessibility, budget, hard no's). — ✅ *Complete.*
- **FR-PLAN-6 (MUST)** "Finishing touches" checklist on a planned date, AI-suggested and editable: reservations to make (with links and a "book by" date), tickets to buy, things to bring/buy (flowers, a card, a blanket), childcare/pet arrangements, outfit guidance to pass on as a hint. Items can be ticked off; unticked items with a deadline trigger a reminder to the planner (planner-only notification). — ✅ *Complete.*
- **FR-PLAN-7 (MUST)** Announce flow: announcing puts the date on the shared calendar with a short planner-written (optionally AI-polished) invitation message; if the partner has joined she is notified and can see the full itinerary. Announced dates do not need her approval, but she can react (❤️ / "can we move it?" with a suggested time). — ✅ *Complete.*
- **FR-PLAN-8 (SHOULD)** Saved **plan templates**: the planner can save a date he liked as a reusable template ("our usual Friday") and re-plan it for a new date (AI re-validates hours, events and weather). — ✅ *Complete.*

### 5.4 Idea bank & swipe-to-match voting (match mode)

- **FR-IDEA-0 (MUST)** Two idea collections: the planner's **private ideas** (from plan mode, manual entry, "ideas she's mentioned"; visible only to that member) and the couple's **shared deck** (match mode). Either member can move an idea from their private list into the shared deck; that cannot be undone except by deleting the idea from the deck. — ✅ *Complete.*
- **FR-IDEA-1 (MUST)** The shared **idea bank / deck**: ideas come from AI generation in match mode (§6), the chat assistant, manual entry by either partner (title, description, optional venue/link, est. cost, tags), or a private idea shared into it. — ✅ *Complete.*
- **FR-IDEA-2 (MUST)** Each idea card shows: title, one-paragraph pitch, category/vibe tags, estimated cost per couple, estimated duration, best time (day/evening/weekend), indoor/outdoor, and if grounded: venue name, rating, price level, distance/travel time, open-hours note, event date/time, weather note. Source attribution required where provider ToS demand it (see §7). — 🚧 *Partial: Missing grounded travel-time, open-hours, and weather-note fields.*
- **FR-VOTE-1 (MUST)** **Swipe UI**: a card stack for ideas the current partner hasn't voted on. Swipe right = **yes**, left = **no**, up (or button) = **maybe**. Buttons for every action, too (accessibility, desktop). — ✅ *Complete.*
- **FR-VOTE-2 (MUST)** Votes are **private**: a partner never sees the other's vote on an idea until both have voted. After both vote, outcome is visible to both. — ✅ *Complete.*
- **FR-VOTE-3 (MUST)** **Match** = both yes. A match triggers a realtime in-app celebration (warm, playful animation) and a push notification to the partner who didn't vote last. — ✅ *Complete.*
- **FR-VOTE-4 (MUST)** Lists: **Matches**, **Maybes** (at least one maybe, no no's: can be re-swiped later), **Pending partner** (I voted, they haven't; my vote stays hidden from them), **Passed** (any no, hidden by default, recoverable). — ✅ *Complete.*
- **FR-VOTE-5 (MUST)** A partner can change their vote until the idea is scheduled. — ✅ *Complete.*
- **FR-VOTE-6 (SHOULD)** Undo last swipe. — ✅ *Complete.*
- **FR-IDEA-3 (MUST)** Ideas can be archived/deleted by either partner; AI-generated ideas that are passed are kept (hidden) as negative signal for personalization. — ✅ *Complete.*
- **FR-IDEA-4 (SHOULD)** Idea freshness: grounded ideas tied to a specific event/date are auto-flagged "expired" after the event passes; venue ideas show "may have changed" if the cached venue data is older than its refresh window. — ✅ *Complete.*

### 5.5 Itineraries

- **FR-ITIN-1 (MUST)** From any idea (typically a match), a partner can **generate an itinerary** for a chosen date/time (or let the app suggest a time; FR-CAL-4). — ✅ *Complete.*
- **FR-ITIN-2 (MUST)** An itinerary is an ordered list of **stops**: start/end time, venue (name, address, lat/lng, provider place id), activity description, est. cost, notes (e.g. "reserve ahead", "dress code"), and the **travel leg** to the next stop (mode, duration, distance). — ✅ *Complete.*
- **FR-ITIN-3 (MUST)** Itinerary-level fields: title, date, total est. cost vs budget, total duration, weather forecast summary (when within the forecast window), **backup plan** (an alternative stop or indoor swap when weather risk is meaningful), "what to bring/wear". — 🚧 *Partial: No itinerary-level total-duration field or displayed total duration.*
- **FR-ITIN-4 (MUST)** Generated itineraries must be validated (§6.4): venues open during their stop window, travel legs feasible, total within budget (or flagged as over). — ✅ *Complete.*
- **FR-ITIN-5 (MUST)** Editable: reorder, remove, add a stop (search venues), change times; "regenerate this stop" asks the AI for an alternative for one stop only. — ✅ *Complete.*
- **FR-ITIN-6 (MUST)** Each stop has **map links**: Apple Maps and Google Maps deep links, plus a "directions from previous stop" link. An itinerary overview map (SHOULD) shows all stops. — ✅ *Complete.*
- **FR-ITIN-7 (MUST)** Reservation/ticket links where available (venue website, event ticket URL). No in-app booking. — ✅ *Complete.*
- **FR-ITIN-8 (MUST)** Itineraries for upcoming dates are available **offline** in the PWA. — ✅ *Complete.*

### 5.6 In-app calendar & scheduling

> Decision: **in-app calendar only** in v1. No iCloud/Google/CalDAV sync and no
> subscribed feed. (Apple exposes no web calendar API; owner chose to keep v1 simple.)

- **FR-CAL-1 (MUST)** Shared couple calendar with **month** and **agenda (upcoming list)** views; SHOULD add a week view. — ✅ *Complete.*
- **FR-CAL-2 (MUST)** Calendar entries: scheduled dates (linked to an itinerary), surprise dates (rendered per §5.7), and important dates/occasions (FR-PROF-3). — ✅ *Complete.*
- **FR-CAL-3 (MUST)** Schedule/reschedule/cancel a date. Rescheduling a dated itinerary re-runs validation (hours, weather, events) and flags anything that no longer works. — ✅ *Complete.*
- **FR-CAL-4 (MUST)** **Suggest a time:** using both partners' availability (FR-PROF-4), already-scheduled dates, and the idea's constraints (event times, venue hours, best-time-of-day), propose up to 3 candidate slots in the next N weeks (default 3). — ✅ *Complete.*
- **FR-CAL-5 (MUST)** No approval gate: a date the planner schedules is on the calendar immediately (announced or surprise). The partner, if joined, can react or request a different time (FR-PLAN-7); the planner decides. Unpaired, the calendar is the planner's alone. — ✅ *Complete.*
- **FR-CAL-6 (MUST)** All times stored in UTC with the couple's IANA time zone; displayed in local time. — ✅ *Complete.*
- **FR-CAL-7 (v2)** Optional export: per-date `.ics` download and/or a subscribed webcal feed; iCloud CalDAV free/busy. — ⏭️ *Deferred (v2).*

### 5.7 Surprise mode

- **FR-SURP-1 (MUST)** Any planned date (plan mode or a match) can be made a **surprise**, before or after it is scheduled, and an announced date can be turned into a surprise only if the partner hasn't seen it yet. Either member can plan one; the creator is the planner, the other the surprisee. If the partner hasn't joined, hints and reveal are just shown to the planner as a share-ready message ("text her this hint on Thursday") he can copy. — ✅ *Complete.*
- **FR-SURP-2 (MUST)** The planner builds the itinerary normally (AI assist included), privately: surprise ideas and itineraries **never** appear in the surprisee's idea bank, swipe deck, matches, chat history, or any API response for that user before reveal. Enforce on the server, not just in the UI. — ✅ *Complete.*
- **FR-SURP-3 (MUST)** The surprisee sees a calendar entry "Surprise date ✨" with **start time**, approximate **end time**, and the **hints** the planner chose to release. — ✅ *Complete.*
- **FR-SURP-4 (MUST) Hints:** the planner adds hints from presets plus free text: dress code, what to bring, "be ready at/by", "eat beforehand / don't eat", energy level, indoor/outdoor, weather gear, rough travel ("we'll be out ~30 min from home"). Each hint has a **release time** (immediately, or N hours/days before). — ✅ *Complete.*
- **FR-SURP-5 (SHOULD)** "Suggest hints": the AI proposes a few teasing but non-spoiling hints from the itinerary; the planner approves/edits each before release. — ✅ *Complete.*
- **FR-SURP-6 (MUST) Reveal:** the full itinerary unlocks for the surprisee at a planner-set **reveal time** (default: date start time) **or** when the planner taps **Reveal now**. The reveal is executed by the background scheduler and pushes a notification. — ✅ *Complete.*
- **FR-SURP-7 (MUST)** Partial reveal safety: the planner can mark individual stops as "revealed at stop time" so a multi-stop surprise unfolds step by step (SHOULD-level; MUST that the data model allows per-stop reveal). — 🚧 *Partial: data model and API support per-stop reveal (the MUST); no editor control yet.*
- **FR-SURP-8 (MUST)** Surprise data is excluded from the surprisee's personalization context and from the shared chat until revealed. — ✅ *Complete.*

### 5.8 Post-date rating (feedback for personalization)

- **FR-RATE-1 (MUST)** After a scheduled date's end time, each partner is prompted (in-app; push optional per notification settings) to rate it: **1–5 stars**, **would do again** (y/n), optional one-line comment, and optional per-stop thumbs up/down. — ✅ *Complete.*
- **FR-RATE-2 (MUST)** Ratings are private until both have rated (or 7 days pass), then visible to both. — ✅ *Complete.*
- **FR-RATE-2a (MUST) Planner's read on how it went:** the planner additionally records, privately, how he thinks **she** enjoyed it (1–5, highlights, "she lit up at…", "she wasn't into…"). This is the main feedback signal while she has no account, and it stays private to him. — ✅ *Complete.*
- **FR-RATE-3 (MUST)** Her ratings (when joined), the planner's read on her, his own rating, votes (incl. passes), profiles and planner notes form the personalization signal (AI-6). Her own ratings outweigh his read on her when both exist. — ✅ *Complete.*
- **FR-RATE-4 (MUST)** A "Past dates" list shows completed dates with both ratings. (Not a full journal: no photos in v1.) — ✅ *Complete.*

### 5.9 Notifications

Web Push via the PWA service worker, plus an in-app notification center. Each type is individually toggleable per partner.

- **FR-NOTIF-1 (MUST) Date reminders:** day-before (default 6pm local) and ~1 hour before start; day-before includes a **weather heads-up** and "what to bring", and flags any validation issue (venue now closed, rain forecast with backup available). — ✅ *Complete.*
- **FR-NOTIF-2 (MUST) Partner activity:** new match; partner added idea(s); date proposed/confirmed/rescheduled/cancelled; surprise hint released; surprise revealed. — ✅ *Complete.*
- **FR-NOTIF-3 (MUST)** Quiet hours (default 22:00–08:00 local), with partner-activity notifications batched/deferred during quiet hours; reminders are exempt only if the date itself starts within that window. — ✅ *Complete.*
- **FR-NOTIF-4 (MUST)** iOS caveat surfaced in onboarding: web push on iPhone requires installing the PWA to the Home Screen (iOS 16.4+). Show an install guide on iOS Safari. — ✅ *Complete.*
- **FR-NOTIF-5 (MUST)** Notification content must not leak surprise details (e.g. "Your surprise date is in 1 hour!" never names a venue). — ✅ *Complete.*
- **FR-NOTIF-6 (v2)** Occasion nudges ("anniversary in 3 weeks"), rating-prompt pushes beyond the in-app prompt, email notifications. — ⏭️ *Deferred (v2); rating-prompt push is built, occasion nudges and email are not.*

---

## 6. AI requirements

### 6.1 Model & integration

- **AI-1 (MUST)** Use the **OpenAI API** via the official Python SDK, server-side only (the key never reaches the browser). — ✅ *Complete.*
- **AI-2 (MUST)** Model choice is **config-driven**, with at least two tiers: — ✅ *Complete.*
  - **Fast tier** (default `gpt-6-luna`): hint suggestions, tagging/classifying ideas, short rewrites, chat intent routing.
  - **Smart tier** (default `gpt-6.2-sol`): idea generation, itinerary building, chat assistant.
  - Model IDs live in env/config, not code, so they can be upgraded without a deploy of logic changes.
- **AI-3 (MUST)** Use **prompt caching** for stable prompt prefixes (system prompt, tool definitions, couple profile) to cut cost/latency. — 🚧 *Partial: Couple profile and taste summary are not cached as prompt prefixes.*
- **AI-4 (MUST)** Use **structured outputs** (JSON schema / tool-result schemas validated by Pydantic) for ideas and itineraries; never parse free text for data the app stores. — ✅ *Complete.*
- **AI-5 (MUST)** **Stream** chat responses to the client. — ✅ *Complete.*

### 6.2 Tools the AI can call (tool use)

The AI grounds its suggestions by calling backend-implemented tools. Each tool wraps a provider behind an internal interface (so providers can be swapped) and goes through the cache (§7.2).

| Tool | Purpose | Default provider |
|---|---|---|
| `search_places(query, near, radius, type, price_levels, open_at)` | Find venues/activities | Google Places API (New) |
| `get_place_details(place_id)` | Hours, price level, rating, website, accessibility attrs | Google Places API (New) |
| `search_events(near, radius, start, end, keywords, categories)` | Concerts, shows, sports, festivals on specific dates | Ticketmaster Discovery API (provider-abstracted; add others later) |
| `get_weather(lat, lng, start, end)` | Hourly/daily forecast, precipitation probability | Open-Meteo (free, no key) or equivalent |
| `get_travel_time(origin, destination, mode, depart_at)` | Duration/distance between stops | Google Routes API |
| `get_couple_context()` | Profile, constraints, availability, important dates, recent ratings/votes summary | internal |
| `check_calendar(start, end)` | Busy/free slots for the couple | internal |

- **AI-6 (MUST) Personalization context:** every idea/itinerary/chat request includes a compact, server-built summary: both partners' preferences and hard no's, shared constraints, upcoming occasions, the **requester's** private notes about the other partner (FR-PROF-8, never anyone else's), and a **learned-taste summary** (kept per recipient, i.e. "what she enjoys" and "what he enjoys") derived from ratings, the planner's read (FR-RATE-2a), would-do-again, per-stop thumbs, votes (yes/no/maybe patterns by category/price/vibe) and passed ideas. Recompute the learned summary after new ratings/votes (debounced), store it, and cache it as a prompt prefix. — 🚧 *Partial: Context is not cached as a prompt prefix.*
- **AI-7 (MUST) Diversity & novelty:** idea batches avoid repeating ideas already in the bank, recently done, or passed; mix categories/price points unless the request narrows them. — ✅ *Complete (plan-option diversity is prompt-only).*
- **AI-8 (MUST) Hard constraints win:** dietary/allergy, accessibility, budget ceiling, travel radius and "hard no's" are never violated; if the AI can't satisfy them it says so rather than suggesting a violating idea. — 🚧 *Partial: Only budget, window and hours are enforced in code.*

### 6.3 AI features

- **AI-GEN (MUST) Generate ideas:** input = optional filters (date/time window, budget, vibe, indoor/outdoor, category, "surprise me"), output = 5–10 idea cards (default 8) that are a mix of grounded (real venue/event) and evergreen ideas (e.g. "cook a new cuisine together"), each with the FR-IDEA-2 fields. In match mode, ideas land in the shared deck as unvoted for both partners; in plan mode they go to the requester's private list. — ✅ *Complete.*
- **AI-PLAN (MUST) Plan a date:** implements FR-PLAN-2/3: "options" (3 grounded concepts with stop sketches) and "plan it all" (one validated itinerary), plus the finishing-touches checklist (FR-PLAN-6) and an optional polished invitation message (FR-PLAN-7). Optimizes for the recipient's taste. Smart tier. — ✅ *Complete.*
- **AI-ITIN (MUST) Build itinerary:** input = idea + date/time (+ optional stop count, budget override); output = validated itinerary (FR-ITIN-2/3). Uses places, travel time, weather and events tools. — ✅ *Complete.*
- **AI-CHAT (MUST) Chat assistant:** each member has a **private planning thread** (the main one for the owner: "help me plan something for her birthday"), plus a shared couple thread once the partner has joined (each message attributed to its author). Natural-language requests like "something cozy under $50 this Friday", "plan our anniversary", "swap the bar for somewhere quieter". The assistant can, via tools/actions, add ideas to the bank, create/edit an itinerary draft, and propose calendar slots; any action that writes data is shown as a confirmable card the user taps to apply (no silent writes). — ✅ *Complete.*
- **AI-OCC (SHOULD) Occasion-aware:** when a request's window is near an important date, the AI proactively offers occasion-appropriate options. — 🚧 *Partial: Occasion awareness is prompt-only; no output test, and window uses today.*
- **AI-HINT (SHOULD)** Surprise hint suggestions (FR-SURP-5), fast tier. — ✅ *Complete.*

### 6.4 Validation & safety

- **AI-VAL-1 (MUST)** Post-generation validation in code (not trusted to the model): every venue in an itinerary has a resolvable place id; stop windows fall within opening hours (when known); travel legs fit between stops with a buffer (default 10 min); total cost estimate checked against budget; events are on the requested date. Failures trigger one automatic repair pass by the AI with the specific errors; anything still failing is shown to the user as a warning on that stop. — ✅ *Complete.*
- **AI-VAL-2 (MUST)** The AI must never invent a venue, address, price, or event time presented as real. Evergreen ideas are labelled as such; grounded ones only use tool-returned data. — 🚧 *Partial: Stop prices are not checked against provider data.*
- **AI-SAFE-1 (MUST)** Content stays appropriate for a general date-planning app; refuse or redirect requests outside the app's purpose (no general-purpose chatbot use beyond date planning, to protect cost caps). — 🚧 *Partial: Off-topic refusal is prompt-only; no code guard or test.*
- **AI-SAFE-2 (MUST)** Prompt-injection hygiene: venue/event text from third-party APIs is passed as data, never as instructions; tools available to the model are only the read-only lookups above plus the confirm-gated write actions. — ✅ *Complete.*
- **AI-SAFE-3 (MUST)** Surprise isolation: a chat/AI call made by the surprisee never receives unrevealed surprise data in its context (FR-SURP-8). — ✅ *Complete.*

---

## 7. External integrations

### 7.1 Providers & keys

| Need | Provider (default) | Env var(s) |
|---|---|---|
| LLM | OpenAI API | `OPENAI_API_KEY`, `AI_MODEL_FAST`, `AI_MODEL_SMART` |
| Places | Google Places API (New) | `GOOGLE_MAPS_API_KEY` |
| Travel time | Google Routes API | `GOOGLE_MAPS_API_KEY` |
| Geocoding (home area) | Google Geocoding API | `GOOGLE_MAPS_API_KEY` |
| Events | Ticketmaster Discovery API | `TICKETMASTER_API_KEY` |
| Weather | Open-Meteo (no key) | `WEATHER_PROVIDER` |
| Email (magic links) | any transactional email provider (e.g. Resend/Postmark/SES) | `EMAIL_*` |
| Web Push | VAPID | `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT` |

- **INT-1 (MUST)** Each provider sits behind an internal interface with a fake/stub implementation used in tests and in a `DEMO_MODE` for local dev without keys. — ✅ *Complete.*
- **INT-2 (MUST)** Respect provider **Terms of Service**, notably Google Maps Platform caching rules (place IDs may be stored indefinitely; most other Places content may only be cached briefly, so re-fetch details by place id rather than storing them long-term) and required attribution display. The dev session must check current ToS and encode the allowed TTLs in config. — ✅ *Complete.*
- **INT-3 (MUST)** Graceful degradation: if any provider is down or over quota, AI falls back to evergreen/ungrounded ideas clearly labelled, and the UI shows a non-blocking notice. — 🚧 *Partial: Degraded notices cover plan options and idea generation only.*

### 7.2 Caching & cost controls

- **COST-1 (MUST)** Cache tool results in Postgres (or Redis) keyed by normalized request, with per-provider TTLs: weather ~1h, events ~6h, travel times ~24h for same origin/destination/mode/time-bucket, places search per ToS. — ✅ *Complete (Google content is never stored; decision 8).*
- **COST-2 (MUST)** **Per-couple AI caps**: daily limits on idea-generation runs, itinerary builds, and chat messages (configurable; defaults e.g. 20 generations, 20 itineraries, 100 chat messages per couple per day), plus a per-couple monthly token budget. Exceeding a cap returns a friendly message with the reset time. — ✅ *Complete.*
- **COST-3 (MUST)** Global kill-switch/budget: an env-configured monthly spend ceiling across all couples; when hit, AI features degrade to read-only with a notice. — ✅ *Complete.*
- **COST-4 (MUST)** Record per-request usage (model, input/output/cached tokens, tool calls, est. cost, couple id, feature) for an ops report (CLI script or simple admin endpoint). — ✅ *Complete.*
- **COST-5 (MUST)** Rate-limit all external provider calls per couple and globally. — 🚧 *Partial: Resend email and web push bypass the provider rate limiter.*

---

## 8. Data model (conceptual)

Dev session finalizes the schema; these entities and relationships are required.

- **User**: id, email, display name, avatar (optional), auth identities, notification settings, timezone, created/deleted timestamps.
- **Couple**: id, name, timezone, home area (geocoded, neighborhood precision), status (active/archived), created_at.
- **CoupleMember**: couple_id, user_id, joined_at (max 2 per couple; user in ≤1 active couple).
- **Invite**: couple_id, token hash, short code, expires_at, used_at, revoked_at.
- **PartnerProfile**: user_id, interests, cuisines, energy, indoor/outdoor, social setting, loves, hard no's, dietary, accessibility.
- **CoupleConstraints**: budget range, currency, radius/max travel time, transport modes, alcohol, kids/pets.
- **Availability**: user_id, recurring windows (weekday + time range), one-off blocks.
- **ImportantDate**: couple_id, name, date, recurring, kind (anniversary/birthday/custom).
- **PartnerProfile ownership**: `edited_by` / `confirmed_by_subject_at` so a planner-written profile (FR-PROF-7) can be handed over.
- **PlannerNote / MentionedIdea**: author_id, about_user_id (nullable before she joins), text, created_at. Author-only visibility.
- **PlanSession**: planner_id, inputs (window, budget, steer, must-include), generated options, chosen option, resulting itinerary_id.
- **ChecklistItem**: itinerary_id, text, kind (reserve/buy/bring/arrange), due_at, link, done_at. Planner-only on surprises.
- **PlannerRead**: itinerary_id, planner_id, her_enjoyment 1–5, highlights, misses (planner-only).
- **Idea**: couple_id, source (ai/chat/manual/plan), created_by, visibility (shared deck | private to creator), title, pitch, tags, est. cost, duration, best time, indoor/outdoor, grounded refs (place ids, event id, valid_until), status (active/archived/expired), generation metadata.
- **Vote**: idea_id, user_id, value (yes/no/maybe), updated_at. Unique (idea, user).
- **Itinerary**: idea_id (nullable), couple_id, title, date start/end, status (draft/proposed/confirmed/completed/cancelled), is_surprise, planner_id, reveal_at, revealed_at, budget, est. total, weather snapshot, backup plan, bring/wear.
- **Stop**: itinerary_id, order, start/end, place id, cached display fields (per ToS), description, est. cost, notes, reveal policy (with itinerary / at stop time).
- **TravelLeg**: from_stop, to_stop, mode, duration, distance.
- **SurpriseHint**: itinerary_id, text, kind, release_at, released_at.
- **Rating**: itinerary_id, user_id, stars, would_again, comment, per-stop thumbs, created_at.
- **TasteSummary**: couple_id, summary text/JSON, source counts, updated_at.
- **ChatThread / ChatMessage**: couple_id, scope (shared | private user_id), role, author user_id, content, tool calls/results, created_at.
- **Notification / PushSubscription**: per-user in-app notifications; Web Push endpoints and keys.
- **ProviderCache**: provider, key, payload, expires_at.
- **AIUsage**: couple_id, user_id, feature, model, token counts, cost estimate, created_at.

**Authorization invariant:** every couple-owned row is filtered by the requester's couple membership; surprise-private rows are additionally filtered by planner id until `revealed_at`.

---

## 9. API (outline)

REST + JSON under `/api/v1`, documented by FastAPI's OpenAPI; WebSocket at `/api/v1/ws` for realtime events. Indicative groups (dev session finalizes):

- `auth/*` (magic link request/verify, session, logout)
- `couple/*` (create, invite, join, leave, constraints, important dates)
- `me/*` (profile, availability, notification settings, push subscriptions, delete account, data export)
- `plan/*` (start plan session, options, choose/combine, plan-it-all, checklist, announce, templates)
- `notes/*` (planner notes, ideas she's mentioned)
- `ideas/*` (list w/ filters incl. private/shared, create, archive, share-to-deck, `POST ideas/generate`), `ideas/{id}/vote`, `deck` (next unvoted cards), `matches`
- `itineraries/*` (create from idea via AI, CRUD, stop regenerate, schedule/confirm/cancel, suggest-times, reveal, hints)
- `calendar?from&to` (entries rendered per viewer, with surprise redaction)
- `ratings/*`
- `chat/threads/*`, `chat/threads/{id}/messages` (streaming), `chat/actions/{id}/apply`
- `notifications/*`

Realtime events: `idea.created`, `vote.updated` (no vote values leaked pre-match), `match.created`, `itinerary.updated`, `date.proposed|confirmed|cancelled`, `hint.released`, `surprise.revealed`, `rating.revealed`.

---

## 10. UX & design

- **UX-1 (MUST) Look & feel: warm & playful.** Soft warm palette (e.g. blush/coral/peach with a deep accent), rounded cards, friendly microcopy, tasteful swipe and match animations. Romantic but not cheesy; no clip-art hearts everywhere. — ⚠️ *Built; colour pleasantness has no automated check.*
- **UX-2 (MUST) Mobile-first**, responsive up to desktop. Primary nav (bottom tab bar on mobile): **Home** (big "Plan a date" button, next date, finishing-touches due, upcoming occasions), **Ideas** (my private ideas + shared swipe deck + matches), **Calendar**, **Chat**, **Us** (both profiles, my notes about her, couple settings). Before the partner joins, the swipe deck shows an invite CTA instead. — ✅ *Complete (Us is Profile; Settings was added; invite is inactive under NO_LOGIN).*
- **UX-3 (MUST)** Light and dark themes following the system setting. — ✅ *Complete (no jsdom media-query test).*
- **UX-4 (MUST) Accessibility:** WCAG 2.2 AA: every swipe has a button equivalent, full keyboard support, screen-reader labels, respects `prefers-reduced-motion`, sufficient contrast. — ⚠️ *Built; colour contrast has not been machine-checked.*
- **UX-5 (MUST)** Clear loading/streaming states for AI (skeleton cards, streaming text); AI calls longer than ~3s show progress ("checking the weather…", "finding places near you…"). — ✅ *Complete (AI waits use staged progress, not skeleton cards).*
- **UX-6 (MUST)** Empty states that teach (e.g. empty deck → "Generate ideas" CTA; unpaired → invite partner CTA). — ✅ *Complete.*
- **UX-7 (MUST)** Surprise UI is unmistakable for the planner (distinct color/badge, "Only you can see this") so they never accidentally share. — ✅ *Complete (the itinerary header pill says “Surprise” only).*
- **UX-8 (SHOULD)** Install prompt / iOS "Add to Home Screen" guide. — ✅ *Complete.*

---

## 11. Non-functional requirements

### Security
- **NFR-SEC-1 (MUST)** OWASP ASVS L1 basics: input validation (Pydantic), parameterized SQL (ORM), secure cookies, CSRF protection, CORS restricted to the app origin, security headers/CSP, rate limiting on auth endpoints, magic-link tokens single-use + short TTL (15 min) + stored hashed. — ✅ *Complete.*
- **NFR-SEC-2 (MUST)** Server-side authorization on every endpoint and WebSocket message; automated tests prove a user cannot read/write another couple's data a surprisee cannot see unrevealed surprise data via any endpoint (ideas, itineraries, calendar, chat, notifications, realtime), and a partner can never read the other's private ideas, planner notes, planner's read, private chat thread or plan sessions. — ✅ *Complete (guarantees are per couple/profile under NO_LOGIN=1, decision 7).*
- **NFR-SEC-3 (MUST)** Secrets only in env; no keys in the frontend bundle; provider calls made server-side only. — ✅ *Complete (no built-bundle key-scan test).*

### Privacy
- **NFR-PRIV-1 (MUST)** Location stored at neighborhood precision for the home area; no continuous location tracking. Device geolocation, if used for "near me now", is per-request and not stored. — ✅ *Complete.*
- **NFR-PRIV-2 (MUST)** Account deletion removes the user's personal data (and the couple space if they were the last member) within 30 days; data export of a user's own data as JSON. — ✅ *Complete.*
- **NFR-PRIV-3 (MUST)** A privacy notice page listing the third parties data is sent to (OpenAI, Google Maps Platform, Ticketmaster, weather provider, email provider) and what is sent. — ✅ *Complete.*
- **NFR-PRIV-4 (SHOULD)** Send the LLM only what's needed: first names / "Partner A/B", no emails. — ✅ *Complete.*

### Performance & reliability
- **NFR-PERF-1 (MUST)** Non-AI API p95 < 300 ms; app shell interactive < 2.5 s on mid-range mobile over 4G. — ⚠️ *Built; no p95 or shell-interactive timing measurement.*
- **NFR-PERF-2 (MUST)** Idea generation first card streamed/visible < 8 s target; itinerary build < 30 s target, with progress feedback. — ⚠️ *Built; first-card and itinerary-build targets are unmeasured.*
- **NFR-REL-1 (MUST)** Scheduled jobs (reminders, hint releases, reveals, rating prompts) are idempotent and survive restarts; a missed reveal fires on recovery. — ✅ *Complete (no specific worker-restart test for overdue reveals).*
- **NFR-REL-2 (MUST)** Health endpoint; structured JSON logs; error tracking hook (e.g. Sentry DSN optional via env). — ✅ *Complete (no JSON-log or Sentry-hook test).*

### Quality
- **NFR-QA-1 (MUST)** Backend unit + integration tests (with provider fakes and a mocked OpenAI client); frontend component tests; Playwright smoke, two paths: (a) **solo planner**: sign up → fill both profiles → plan a date (options) → itinerary → schedule as surprise → planner's read; (b) **match**: invite → partner joins → generate → swipe both → match → itinerary → schedule → both rate. — ✅ *Complete (smoke uses NO_LOGIN instead of sign-up/invite, decision 7).*
- **NFR-QA-2 (MUST)** Lint/format/type-check: ruff + mypy (backend), ESLint + Prettier + `tsc --noEmit` (frontend). One command each to run tests and checks, documented in the README. — 🚧 *Partial: README lists many commands; only backend has a test/check wrapper.*
- **NFR-QA-3 (MUST)** *(amended 2026-11-19, §14 decision 5)* `dateapp build && dateapp serve` brings up the app (API, in-process worker, frontend) against a local Postgres, and `dateapp daemon install` / `dateapp daemon update` deploy it; previously: `docker compose up` brings up db, api, worker and web locally; `DEMO_MODE=1` works with no external keys (fake providers + canned AI responses). — ✅ *Complete (docker-compose is superseded by decision 5).*

---

## 12. Suggested milestones (for the dev session)

1. **Foundation:** repo layout (`backend/`, `frontend/`, `docker-compose.yml`), DB + migrations, magic-link auth, couple space (single member works), both profiles incl. planner-maintained partner profile, planner notes, PWA shell with nav. — ✅ *Complete (docker-compose superseded by decision 5; magic-link/invite inactive under NO_LOGIN).*
2. **AI + plan mode:** AI integration (OpenAI; originally built on Claude), tools (places, events, weather, travel), caching, caps/usage logging, AI-PLAN options + plan-it-all, private ideas. — ✅ *Complete (prompt-cache gap tracked under AI-3).*
3. **Itineraries & calendar:** AI-ITIN + validation/repair, itinerary editor, map links, finishing-touches checklist, calendar views, suggest-times, announce, offline itineraries. *At this point the owner can fully plan dates solo.* — ✅ *Complete.*
4. **Surprise mode:** hints + release scheduling, reveal, share-ready hints when unpaired, redaction across every surface + tests. — ✅ *Complete.*
5. **Partner & match mode:** invite/join, profile hand-over, shared deck, AI-GEN, swipe, private votes, matches, realtime events. — ✅ *Complete (invite/join inactive under NO_LOGIN, decision 7).*
6. **Chat:** private planning thread, shared thread, streaming, confirmable actions. — ✅ *Complete.*
7. **Feedback & notifications:** ratings, planner's read, taste summary, web push, reminders with weather, checklist deadlines, partner activity, quiet hours. — ✅ *Complete.*
8. **Hardening:** security/authorization test sweep, accessibility pass, privacy page, deletion/export, ops usage report, e2e smoke. — ✅ *Complete (contrast is by-eye only; smoke uses NO_LOGIN; docker-compose superseded by decision 5).*

Each milestone should end with its tests passing and a working `docker compose up`.

---

## 13. v2 backlog (explicitly deferred)

- Calendar export: per-date `.ics`, subscribed webcal feed; iCloud CalDAV / Google Calendar free/busy sync.
- Date journal: photos, notes, memories timeline, "on this day".
- Budget tracking: actual spend vs estimates, monthly date budget.
- Occasion nudges and email notifications; rating-prompt pushes. — 🚧 *Rating-prompt pushes built; the rest deferred.*
- AI-written cryptic surprise teasers released over the preceding days.
- Group/friends outings; native mobile apps; booking/reservation integrations.
- More event providers; multi-city/travel trips.

---

## 14. Open questions (decide during development, document the choice)

1. Job runner: Postgres-backed scheduler vs Redis + ARQ/Celery. — ✅ *Answered by Decision 1.*
2. Transactional email provider for magic links. — ✅ *Answered by Decision 2.*
3. Exact default caps (COST-2) and global monthly ceiling (COST-3) once real usage costs are measured. — ✅ *Answered by Decision 3.*
4. Whether the owner may still edit the partner's profile directly after she has confirmed it (current spec: suggestions only, FR-PROF-7). — ✅ *Answered by Decision 4.*
5. Hosting target for production (Fly.io / Railway / other). — ✅ *Answered by Decision 5.*

### Decisions

| # | Decision | Date | Why |
|---|---|---|---|
| 1 | **Postgres-backed job queue**: a `jobs` table (`run_at`, `status`, `attempts`, unique `dedupe_key`), and a worker that claims due jobs with `SELECT … FOR UPDATE SKIP LOCKED`. No Redis. | 2026-11-19 | One fewer service; job state lives in the same DB, so it survives restarts, and overdue jobs (e.g. a missed reveal) run on recovery (NFR-REL-1). Owner approved. |
| 2 | **Resend** for magic-link email (`EMAIL_PROVIDER=resend`, `EMAIL_API_KEY`, `EMAIL_FROM`). DEMO_MODE uses a console sender with a dev outbox. | 2026-11-19 | Simplest HTTP API with a free tier; owner approved. |
| 5 | Production runs **natively (no Docker) as a systemd service** on the owner's Linux box `rogue@192.168.1.167` (Debian 13, aarch64), port **8090** (brothereye already uses 8080 there), managed by a brothereye-style `dateapp` CLI. Public entry is **`https://dates.lan`** through the owner's citadel Caddy + Pi-hole on 192.168.1.164 (HTTPS is required for PWA install, service worker and Web Push). | 2026-11-19 | Owner: deploy like brothereye, "Move date app to rogue@192.168.1.167. Then, add both brothereye and dates to caddy dns". Replaces the earlier docker-compose plan: the docker bridge networks on the Pi had no internet egress. |
| 3 | **Caps:** per couple per day, 20 idea-generation runs, 20 itinerary builds and 100 chat messages; per couple per month, 4M tokens. A **global ceiling of $25/month** (`AI_GLOBAL_MONTHLY_USD=25`) pauses AI features (503 `ai_paused`) until the 1st. A per-couple cap returns 429 `ai_cap_reached` with `reset_at`. | 2026-11-20 | Owner chose $25/month; the daily caps keep one runaway day from eating the month. |
| 4 | **Suggestions only:** once the partner has joined and confirmed, the owner can't edit her profile directly, only suggest changes she accepts or declines (FR-PROF-7 as written). | 2026-11-20 | Keeps her profile hers; matches the spec. Orchestrator decision. |
| 8 | **Providers:** each provider is real if its key is set, else a fake if `DEMO_MODE=1`, else unavailable (features degrade to evergreen ideas). Keys: `OPENAI_API_KEY`, `GOOGLE_MAPS_API_KEY` (Places, Routes, Geocoding), `TICKETMASTER_API_KEY`; weather is Open-Meteo, keyless. One set of keys in rogue's `.env`, shared by both profiles. Google Places, Routes and Geocoding content is never persisted: only place ids are kept indefinitely, and lat/lng for at most 30 days (refreshed by place id). Within a run, an in-memory memo only. Ticketmaster events are cached 6 h and Open-Meteo weather 1 h in the DB. Venue details are fetched live when an itinerary is read. The home-area label stores the user's own typed text. | 2026-11-20 | Owner will supply Google Maps + Ticketmaster keys. |
| 9 | **iPhone is the target device.** Push uses Web Push on the Home-Screen PWA (iOS 16.4+). The app ships an in-app install guide (Share → Add to Home Screen) and a guide to trusting the dates.lan Caddy root certificate (FR-NOTIF-4, UX-8). | 2026-11-20 | Owner: both phones are iPhones. |
| 6 | **PostgreSQL 17** from Debian's packages (`apt install postgresql`), not 16. | 2026-11-19 | Distro-supported with apt security updates; owner approved. |
| 7 | **No login for now (`NO_LOGIN=1`)**: the first screen asks "Who's using Date Planner?" (Husband / Wife), and a Profile tab shows and switches the current profile. *(2026-11-22: that tab is now called Settings.)* No PIN or other protection: anyone who can reach the app can act as either profile, so surprise privacy (FR-SURP-2) rests on trust. Husband = couple owner, Wife = partner member; the household's fixed accounts use reserved `@household.invalid` addresses. Email magic-link sign-in (FR-AUTH-1) stays in the code, switched off (404) while `NO_LOGIN=1`; turning it back on is a config change. | 2026-11-19 | Owner: "Dont have any login for now. Maybe just have a profile tab where you can select if you are the husband or wife"; chose "No protection, trust" and "Keep it, switched off". LAN-only deployment. |
| 10 | **No default home area or timezone:** the couple sets the home area in the app, and the timezone comes from the browser. | 2026-11-20 | Owner's answer. |
| 11 | **Currency:** USD. | 2026-11-20 | Owner's answer. |
| 12 | **Display units:** the UI shows miles and °F; the backend stays metric internally and converts for display. | 2026-11-20 | Owner's answer. |
| 13 | **Notification quiet hours:** default 22:00–08:00 local, per profile and changeable (FR-NOTIF-3). Per-kind toggles gate push only; the in-app notification centre always gets the entry (orchestrator decision). | 2026-11-20 | Owner's answer. |
| 14 | **Surprise dates:** the surprisee can get planner-released hints and a dress-code note; the venue stays hidden until the reveal (FR-SURP). | 2026-11-20 | Owner's answer. |
| 15 | **Alcohol in AI suggestions:** follows the couple's alcohol setting on the Us page *(now the Profile tab).* | 2026-11-20 | Owner's answer. |
| 16 | **Tone: flirty.** Cheekier copy and a bigger match celebration, still tasteful, and reduced-motion respected (UX). | 2026-11-20 | Owner's answer. |
| 17 | **Weekly nudge:** a notification when nothing is scheduled in the next 2 weeks. | 2026-11-20 | Owner's answer. |
| 18 | **Priority order:** AI planning end to end (plan → options → itinerary → schedule) first. | 2026-11-20 | Owner's answer. |
| 19 | **Past dates:** no photos; ratings and comments are text only. | 2026-11-20 | Owner's answer. |
| 20 | **Apple/Google sign-in dropped:** FR-AUTH-2 (Sign in with Apple / Google) is removed from the plan, along with its OAuth provider keys and callbacks. Email magic link (FR-AUTH-1) stays as the only sign-in method, switched off while `NO_LOGIN=1`. | 2026-11-22 | Owner's decision. The app is LAN-only (`dates.lan`), and neither provider accepts a non-public redirect domain. |
| 21 | **AI provider: OpenAI.** The AI client uses the OpenAI Responses API through the official `openai` SDK instead of Anthropic Claude (AI-1). Fast tier `gpt-6-luna`, smart tier `gpt-6.2-sol`. Requests are sent with `store=false`. OpenAI caches repeated prompt prefixes automatically, so the explicit cache markers and Claude's server-side model fallback are gone. | 2026-11-22 | Owner request. |

---

## 15. Human verification

Swipe feel, match celebration, surprise-reveal experience and suggestion quality
can only be judged by the couple actually using it. The project should get a
`HUMAN.md` (per the workspace convention) once there's a runnable build, to
capture the owners' feedback verbatim.
