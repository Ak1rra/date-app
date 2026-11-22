# HUMAN.md

A ledger of what the owner asked for or needs to judge in the running Date
Planner. Human words stay verbatim; an addressed entry cites the change that
earned its status.

---

## The ledger

- **2026-11-19 — ✅ Addressed:** “Dont have any login for now. Maybe just have a profile tab where you can select if you are the husband or wife” — no-login Husband/Wife profile picker in commit `90bf5b9`, matching REQUIREMENTS §14 decision 7.
- **2026-11-20 — ✅ Addressed:** “Have the husband and wife colors be differnt” — husband/wife palette in commit `5672d17`.
- **2026-11-20 — ✅ Addressed:** “Make the husbands blue and the wifes keep the current colors” — commit `5672d17`, with the husband blue variables at `frontend/src/styles.css :root[data-person='husband']`.

## Standing facts a human tester needs

- **Open the app:** on the LAN, visit [https://dates.lan](https://dates.lan).
- **Install on iPhone:** in Safari, tap Share → Add to Home Screen.
- **Trust the `dates.lan` certificate on iPhone:** install Caddy's local root CA profile, then go to Settings → General → About → Certificate Trust Settings and enable full trust. The Caddy host is `192.168.1.164`; its root is `/var/lib/caddy/.local/share/caddy/pki/authorities/local/root.crt`. Send that file to the phone by AirDrop or email, then install it under Settings → General → VPN & Device Management.
- **Switch Husband/Wife:** use the Profile tab.
- **Run a local demo:** copy `.env.example` to `.env`, set `DEMO_MODE=1` and `NO_LOGIN=1`, then from `backend/` run:

  ```text
  uv sync
  uv run dateapp build
  uv run dateapp serve
  ```

  `dateapp serve` uses `PORT` (default `8090`), and its build flag is `--build` or `--no-build`; it does not sync dependencies. In `NO_LOGIN` mode there is no PIN: anyone who can reach the app can choose either profile.
- **Deploy:** install or reinstall with `dateapp daemon install [--port PORT] [--user USER] [--dry-run]`, then update with:

  ```text
  ssh rogue@192.168.1.167
  cd ~/projects/date-app
  dateapp daemon update
  ```

  The CLI's `daemon update` pulls with `git pull --ff-only`, backs up the database, syncs dependencies, builds the frontend, runs `alembic upgrade head`, restarts `date-app`, and waits up to 30 seconds for `/api/v1/health`. It refuses an uninstalled service, tracked uncommitted changes, or a non-fast-forward pull (`backend/app/cli.py:1043-1121`).
- **Owner `.env` keys on rogue:** set `ANTHROPIC_API_KEY`, `GOOGLE_MAPS_API_KEY`, `TICKETMASTER_API_KEY`, and `AI_GLOBAL_MONTHLY_USD=25` in `~/projects/date-app/.env`. All four names are present in `.env.example` (`.env.example:37-45`), and the monthly ceiling is the REQUIREMENTS §14 decision 3 setting.

## Awaiting a human

- Does the setup wizard feel right on an iPhone?
- Are the husband-blue and wife-warm colours pleasant in light and dark mode?
- Is the “Only you can see this” notes badge clear?
- Does switching profiles on a shared phone ever show the other person's data?
- Surprise mode, end to end on two phones: as the planner, schedule a date as a surprise, add hints (including "Suggest hints"), and set a reveal time; as the surprisee, does the Home card, the calendar's "Surprise date ✨" entry and the surprise page give away nothing beyond the time and the released hints, and does the reveal land you on the full date?
- Does a released hint's push/in-app notification feel exciting rather than spoilery?
