# Date Planner

## What it is

Date Planner is an AI-powered web app for one partner to plan great dates for the other, with an optional shared side for deciding together. The planner can get tailored ideas, build an itinerary grounded in real venues, events, weather and travel time, then schedule it as an announced date or a surprise. Partners can also swipe on ideas together, match, share the in-app calendar and rate dates afterwards. It is an installable PWA backed by a FastAPI API and PostgreSQL.

## Layout

- `backend/` - FastAPI, SQLAlchemy async, migrations, services and the `dateapp` CLI.
- `frontend/` - React, Vite, Tailwind and PWA client.
- `scripts/` - operational and remote-check scripts.
- `docs/design/` - milestone and design notes.

## Prerequisites

- Python 3.12 or newer and [uv](https://docs.astral.sh/uv/).
- Node.js `^22.22.2 || ^24.15.0 || >=26.0.0` and npm.
- PostgreSQL, with a database for the app and a separate scratch database for tests.

## Configuration

Copy `.env.example` to `.env`. Settings are read from the environment (and from `.env`); the defaults below are the defaults in `backend/app/config.py`. Empty optional values are shown as `None`.

| Variable | Default | What it does |
| --- | --- | --- |
| **`DEMO_MODE`** | `false` | **Uses fake providers and canned AI responses for demo runs; `.env.example` uses `1` locally.** |
| `APP_ORIGIN` | `http://localhost:8090` | Public origin used for CORS, links and cookies. |
| `DATABASE_URL` | `postgresql+asyncpg://dateapp:dateapp@localhost:5432/dateapp` | Async PostgreSQL URL for the app. |
| `TEST_DATABASE_URL` | `postgresql+asyncpg://dateapp:dateapp@localhost:5432/dateapp_test` | Separate database used by database tests. |
| `COOKIE_SECURE` | `true` | Marks session cookies Secure; use `false` for plain HTTP local development. |
| `SESSION_TTL_DAYS` | `30` | Session lifetime in days. |
| `MAGIC_LINK_TTL_MINUTES` | `15` | Magic-link lifetime in minutes. |
| `LOG_LEVEL` | `INFO` | Logging level. |
| `SENTRY_DSN` | `None` | Optional Sentry DSN. |
| `DEV_OUTBOX` | `false` | Exposes the local unauthenticated sign-in-link outbox endpoint. |
| **`NO_LOGIN`** | `false` | **Replaces email sign-in with the Husband/Wife picker; there is no PIN or other protection.** |
| `PORT` | `8090` | API and built-frontend port. |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | Proxies whose `X-Forwarded-*` headers the server trusts. |
| `FRONTEND_DIST` | `None` (resolved to `<repo>/frontend/dist`) | Built frontend directory served by the API. |
| `RUN_WORKER` | `true` | Runs the background job worker inside the API process. |
| **`ANTHROPIC_API_KEY`** | `None` | **Anthropic key for real AI calls.** |
| `AI_MODEL_FAST` | `claude-haiku-5-5` | Fast AI model. |
| `AI_MODEL_SMART` | `claude-sonnet-5-5` | Smart AI model. |
| `AI_CAP_IDEAS_PER_DAY` | `20` | Per-couple daily idea-run cap. |
| `AI_CAP_BUILDS_PER_DAY` | `20` | Per-couple daily build cap. |
| `AI_CAP_CHAT_PER_DAY` | `100` | Per-couple daily chat-message cap. |
| `AI_MONTHLY_TOKENS_PER_COUPLE` | `4000000` | Per-couple monthly token cap. |
| **`AI_GLOBAL_MONTHLY_USD`** | `25.0` | **Global monthly AI spend ceiling in USD.** |
| `AI_MAX_TOOL_ITERATIONS` | `8` | Maximum AI tool-loop iterations. |
| `AI_EFFORT_SMART` | `medium` | Effort level for the smart model. |
| `AI_EFFORT_FAST` | `low` | Effort level for the fast model. |
| `AI_PRICES` | Built-in map: `claude-haiku-5-5` `{input: 0.10, output: 0.50, cache_read: 0.01, cache_write: 0.125}`, `claude-sonnet-5-5` `{input: 2.00, output: 10.00, cache_read: 0.10, cache_write: 2.50}`, `claude-sonnet-5` `{input: 2.00, output: 10.00, cache_read: 0.20, cache_write: 2.50}` | JSON prices per million tokens for `input`, `output`, `cache_read` and `cache_write`. |
| `TRAVEL_BUFFER_MINUTES` | `10` | Buffer added between travel and stops. |
| **`GOOGLE_MAPS_API_KEY`** | `None` | **Google Maps Platform Places, Routes and Geocoding key.** |
| **`TICKETMASTER_API_KEY`** | `None` | **Ticketmaster events key.** |
| `WEATHER_PROVIDER` | `open-meteo` | Weather provider name. |
| `EMAIL_PROVIDER` | `console` | Magic-link email provider (`console` or `resend`). |
| `EMAIL_API_KEY` | `None` | Optional transactional-email provider key. |
| `EMAIL_FROM` | `None` | Optional sender address. |
| **`VAPID_PUBLIC_KEY`** | `None` | **Web Push VAPID public key.** |
| **`VAPID_PRIVATE_KEY`** | `None` | **Web Push VAPID private key.** |
| **`VAPID_SUBJECT`** | `None` | **Web Push VAPID subject.** |
| `APPLE_CLIENT_ID` | `None` | Optional Apple OAuth client id. |
| `APPLE_TEAM_ID` | `None` | Optional Apple OAuth team id. |
| `APPLE_KEY_ID` | `None` | Optional Apple OAuth key id. |
| `APPLE_PRIVATE_KEY` | `None` | Optional Apple OAuth private key. |
| `GOOGLE_OAUTH_CLIENT_ID` | `None` | Optional Google OAuth client id. |
| `GOOGLE_OAUTH_CLIENT_SECRET` | `None` | Optional Google OAuth client secret. |
| `CACHE_TTL_EVENTS` | `21600` seconds | Ticketmaster cache lifetime. |
| `CACHE_TTL_WEATHER` | `3600` seconds | Open-Meteo cache lifetime. |
| `VENUE_SNAPSHOT_MAX_AGE_DAYS` | `30` | Maximum age of an itinerary venue snapshot before refresh. |
| `PROVIDER_CALLS_PER_COUPLE_PER_MINUTE` | `60` | Per-couple provider-call limit. |
| `PROVIDER_CALLS_GLOBAL_PER_MINUTE` | `300` | Global provider-call limit. |

`DEMO_MODE=1` and `NO_LOGIN=1` are useful for a local household demo. `NO_LOGIN` is a trust-mode setting: anyone who can reach the app can act as either profile. Set the real provider keys when using real AI, maps or events; Open-Meteo needs no key.

## Run it locally

From `backend/`, install/sync dependencies and build the frontend:

```text
uv sync
uv run dateapp build
uv run dateapp serve
```

`dateapp serve` serves the API and built frontend. It uses `PORT` (8090 by default), and accepts `--host`, `--port`, `--build` and `--no-build`. The frontend also has a Vite development server: from `frontend/`, run `npm ci` and then `npm run dev`.

## Checks

Backend checks (run from `backend/`):

```text
uv run ruff check .
uv run ruff format --check .
uv run mypy app
uv run pytest
```

`uv run pytest` needs `TEST_DATABASE_URL` for the database tests; it must point to a scratch database whose name starts with `dateapp_test` because the fixture drops and recreates its public schema.

Frontend checks (run from `frontend/`):

```text
npm run typecheck
npm run lint
npx prettier --check src
npx vitest run
npm run build
```

The remote backend check is:

```text
bash scripts/remote-check.sh
```

It streams the backend to the Linux check host and runs dependency sync, lint, format, mypy and pytest there. This is needed because Smart App Control on the Windows development machine blocks the compiled Python dependencies; the Linux host has PostgreSQL for the database tests. See the script header for `--tree`, `--name`, pytest arguments and connection variables.

## Deploy

The deployed app runs as the `date-app` systemd service under the configured user on Linux:

```text
dateapp daemon install
dateapp daemon update
```

The CLI commands are:

- `dateapp build` - sync Python dependencies and build the frontend (`--sync/--no-sync`, `--install/--no-install`).
- `dateapp serve` - serve the API and built frontend (`--host`, `--port`, `--build/--no-build`).
- `dateapp status` - print API health and metadata (`--host`, `--port`).
- `dateapp migrate` - apply `alembic upgrade head`.
- `dateapp backup` - write a compressed PostgreSQL dump (`--keep N`, default `14`).
- `dateapp magic-link EMAIL` - print a one-use sign-in link without sending email.
- `dateapp usage` - report AI runs, tokens and estimated cost (`--month YYYY-MM`).
- `dateapp vapid generate` - print a VAPID key pair for the environment.
- `dateapp daemon install` - build, migrate, install and start the systemd service (`--port`, `--user`, `--dry-run`).
- `dateapp daemon update` - fast-forward pull, back up, build, migrate, restart and wait for health.
- `dateapp daemon uninstall` - stop, disable and remove the service files.
- `dateapp daemon status` - show systemd status.
- `dateapp daemon logs` - show the service log (`--follow`/`-f`, `--lines`/`-n`).
- `dateapp daemon start` - start the service.
- `dateapp daemon stop` - stop the service.
- `dateapp daemon restart` - restart the service.

## API types

From `backend/`, regenerate the API schema with `python -m app.openapi_export`; this writes `backend/openapi.json`. From `frontend/`, regenerate TypeScript types with `npm run gen:api`; this writes `frontend/src/api/schema.d.ts`.

## Privacy

The app's `/privacy` page describes what third parties receive, including Anthropic, Google Maps Platform, Ticketmaster, Open-Meteo and Resend. In `NO_LOGIN` mode, the profile picker replaces sign-in and there is no PIN or other identity check, so anyone who can reach the app can choose either household profile; use it only where that trust model is acceptable.

## End-to-end smoke

```text
bash scripts/e2e.sh            # both paths
bash scripts/e2e.sh -g match   # extra arguments go to `playwright test`
```

The smoke runs two paths in Playwright (`frontend/e2e/smoke.e2e.ts`):

- **Solo planner:** setup, both profiles, plan with options, schedule as a surprise, then the planner's read after the date completes.
- **Match:** both profiles swipe yes, match, make it a date, schedule, and both rate.

The script runs the server and the browser on different machines:

1. It builds the frontend.
2. It copies `backend/` and `frontend/dist` to `~/projects/date-app-ci/e2e` on `REMOTE_HOST` (default `rogue@192.168.1.167`).
3. There it starts a throwaway `dateapp serve` on `E2E_PORT` (default `18090`). It uses `DEMO_MODE=1`, `NO_LOGIN=1`, fake weather, no provider keys, and a scratch database `dateapp_test_e2e`.
4. It runs Playwright on this machine against `http://localhost:18090`, through an SSH tunnel.
5. On exit it stops that server, closes the tunnel, and drops the database. It never touches the deployed app.

Dates are scheduled in the past, so the in-process worker completes them at once.

Run `npx playwright install chromium` once, in `frontend/`, to get the browser.
