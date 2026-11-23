#!/usr/bin/env bash
# Run the Playwright smoke (frontend/e2e) against a throwaway server on the Linux box.
#
# Why: Smart App Control on the Windows dev machine blocks the compiled Python dependencies, so
# the backend cannot run here, and the Linux box cannot run Chromium (no root for its system
# libraries). So the server runs on REMOTE_HOST and Playwright runs here, through an SSH tunnel.
#
# Usage:  bash scripts/e2e.sh [PLAYWRIGHT_ARGS...]     e.g. bash scripts/e2e.sh -g solo
# Env:
#   REMOTE_HOST   ssh target (default rogue@192.168.1.167)
#   E2E_PORT      port of the server on REMOTE_HOST and of the local end of the tunnel (default 18090)
#
# What it does: builds the frontend, streams backend/ and frontend/dist to
# ~/projects/date-app-ci/e2e on REMOTE_HOST, recreates the scratch database dateapp_test_e2e
# (role dateapp_test, which owns nothing else), migrates it, and starts `dateapp serve` there
# with DEMO_MODE + NO_LOGIN (fake providers, no login, no outbound calls). It opens a tunnel
# to it, waits for /api/v1/health, and runs `playwright test`. On the way out, pass or fail, it
# stops the server, closes the tunnel and drops the database; after a failure it first prints
# the end of the server log. The exit code is Playwright's. ~/projects/date-app and its .env are
# never touched: the server's env comes only from this script.

set -euo pipefail

REMOTE_HOST="${REMOTE_HOST:-rogue@192.168.1.167}"
E2E_PORT="${E2E_PORT:-18090}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="projects/date-app-ci/e2e"   # relative to the remote $HOME
DB="dateapp_test_e2e"
PG="postgresql+asyncpg://dateapp_test:dateapp_test@127.0.0.1:5432"
BASE_URL="http://localhost:${E2E_PORT}"

if ! [[ "$E2E_PORT" =~ ^[1-9][0-9]{3,4}$ ]] || [ "$E2E_PORT" -lt 1024 ] || [ "$E2E_PORT" -gt 65535 ]; then
  echo "E2E_PORT must be a number from 1024 to 65535" >&2
  exit 2
fi
if [ "$E2E_PORT" = 8090 ]; then   # the real deployment's port
  echo "E2E_PORT must not be 8090 (the real service)" >&2
  exit 2
fi

SSH=(ssh -o BatchMode=yes "$REMOTE_HOST")

# The runner executes remotely, in one of three modes: start | stop | dropdb.
# Arguments: MODE DEST DB PG PORT [TESTS_RC]
read -r -d '' RUNNER <<'REMOTE' || true
export PATH="$HOME/.local/bin:$PATH"
MODE="$1" DEST="$HOME/$2" DB="$3" PG="$4" PORT="$5" RC="${6:-0}"
RUN="$DEST/run"
mkdir -p "$RUN"
case "$DB" in dateapp_test_?*) ;; *) echo "refusing database $DB" >&2; exit 2 ;; esac

# Is $1 our test server rather than some other process (or the real service on port 8090)? A
# pid above 1 whose command line is `dateapp serve ... --port $PORT` and whose working directory
# is this tree's backend/.
matches_ours() {
  [[ "$1" =~ ^[1-9][0-9]*$ ]] && [ "$1" -gt 1 ] || return 1
  local cmd cwd
  cmd=$(tr '\0' ' ' <"/proc/$1/cmdline" 2>/dev/null) || return 1
  case "$cmd" in *dateapp*serve*"--port $PORT "*) ;; *) return 1 ;; esac
  cwd=$(readlink "/proc/$1/cwd" 2>/dev/null) || return 1
  [ "$cwd" = "$(realpath "$DEST/backend")" ]
}

# The pid file's pid, if it is still the process we started: matches_ours, leads its own
# process group (setsid made it so), and has the start time recorded next to the pid (so a
# reused pid is not mistaken for it).
is_our_server() {
  matches_ours "$1" || return 1
  [ "$(ps -o pgid= -p "$1" 2>/dev/null | tr -d ' ')" = "$1" ] || return 1
  [ -f "$RUN/server.start" ] && [ "$(ps -o lstart= -p "$1" 2>/dev/null)" = "$(cat "$RUN/server.start")" ]
}

stop_server() {
  if [ -f "$RUN/server.pid" ]; then
    pid=$(cat "$RUN/server.pid")
    if is_our_server "$pid"; then
      # The pid is the process group, so this also takes down the python child `uv run` spawns.
      kill -- "-$pid" 2>/dev/null || true
      for _ in $(seq 1 20); do kill -0 "$pid" 2>/dev/null || break; sleep 0.5; done
      kill -9 -- "-$pid" 2>/dev/null || true
    else
      echo "ignoring a stale or foreign server.pid ('$pid'); not killing anything"
    fi
    rm -f "$RUN/server.pid" "$RUN/server.start"
  fi
}

# Backstop for a server that outlived its pid file: kill a listener on $PORT that matches_ours
# (so never the real service). Anything else on the port is left alone; the start mode's port
# check then refuses.
stop_port_listener() {
  local listeners pid
  listeners=$(ss -ltnpH "sport = :$PORT" 2>/dev/null) || true
  for pid in $(printf '%s\n' "$listeners" | grep -o 'pid=[0-9]*' | cut -d= -f2 | sort -u); do
    matches_ours "$pid" || continue
    echo "killing a leftover test server on port $PORT (pid $pid)"
    kill "$pid" 2>/dev/null || true
    for _ in $(seq 1 20); do kill -0 "$pid" 2>/dev/null || break; sleep 0.5; done
    kill -9 "$pid" 2>/dev/null || true
  done
}

PYSNIP=$(cat <<'PY'
import asyncio
import sys

import asyncpg


async def main(base: str, db: str, create: bool) -> None:
    assert db.startswith("dateapp_test_"), db  # never touch anything but scratch databases
    conn = await asyncpg.connect(base.replace("+asyncpg", "", 1) + "/postgres")
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{db}" WITH (FORCE)')
        if create:
            await conn.execute(f'CREATE DATABASE "{db}"')
        print(f"{'recreated' if create else 'dropped'} database {db}")
    finally:
        await conn.close()


asyncio.run(main(sys.argv[1], sys.argv[2], sys.argv[3] == "create"))
PY
)

case "$MODE" in
  stop)
    cd "$DEST/backend" 2>/dev/null || exit 0
    stop_server
    stop_port_listener   # in case the pid file was lost
    if [ "$RC" != 0 ] && [ -f "$RUN/server.log" ]; then
      echo "--- last 50 lines of the server log ---"
      tail -n 50 "$RUN/server.log"
      echo "---------------------------------------"
    fi
    ;;
  dropdb)
    cd "$DEST/backend" 2>/dev/null || exit 0
    uv run python -c "$PYSNIP" "$PG" "$DB" drop </dev/null
    ;;
  start)
    cd "$DEST/backend" || exit 2
    # Settings read .env and ../.env; none may exist here or it would override the demo setup.
    if [ -e "$DEST/.env" ] || [ -e "$DEST/backend/.env" ]; then
      echo "refusing: a .env exists under $DEST" >&2
      exit 2
    fi
    stop_server   # a leftover from an earlier run
    stop_port_listener
    listening=$(ss -ltn)   # captured first: under pipefail, grep -q closing the pipe can fail it
    if grep -q ":$PORT " <<<"$listening"; then
      echo "port $PORT is already in use on this host" >&2
      exit 2
    fi
    uv sync --frozen </dev/null || exit 1
    uv run python -c "$PYSNIP" "$PG" "$DB" create </dev/null || exit 1
    # Nothing inherited from the login environment may turn on a real provider.
    unset OPENAI_API_KEY GOOGLE_MAPS_API_KEY TICKETMASTER_API_KEY EMAIL_API_KEY EMAIL_PROVIDER \
      VAPID_PUBLIC_KEY VAPID_PRIVATE_KEY VAPID_SUBJECT SENTRY_DSN
    unset DEV_OUTBOX
    export DATABASE_URL="$PG/$DB" DEMO_MODE=1 NO_LOGIN=1 COOKIE_SECURE=0 WEATHER_PROVIDER=fake
    export RUN_WORKER=1 FRONTEND_DIST="$DEST/frontend/dist" PORT="$PORT"
    export APP_ORIGIN="http://localhost:$PORT"
    uv run alembic upgrade head </dev/null || exit 1
    : > "$RUN/server.log"
    setsid nohup uv run dateapp serve --no-build --host 127.0.0.1 --port "$PORT" \
      </dev/null >"$RUN/server.log" 2>&1 &
    echo $! > "$RUN/server.pid"
    # The start time identifies this very process later, even if its pid is reused.
    ps -o lstart= -p "$(cat "$RUN/server.pid")" > "$RUN/server.start"
    echo "server started (pid $(cat "$RUN/server.pid"), port $PORT)"
    ;;
esac
REMOTE

run_remote() {   # run_remote MODE [TESTS_RC]
  local mode="$1" rc="${2:-0}"
  printf '%s\n' "$RUNNER" |
    "${SSH[@]}" "bash -s -- $(printf '%q ' "$mode" "$DEST" "$DB" "$PG" "$E2E_PORT" "$rc")"
}

TUNNEL_PID=""
REMOTE_TOUCHED=0

cleanup() {
  local rc=$?
  trap - EXIT
  set +e
  if [ -n "$TUNNEL_PID" ]; then
    kill "$TUNNEL_PID" 2>/dev/null
    wait "$TUNNEL_PID" 2>/dev/null
  fi
  if [ "$REMOTE_TOUCHED" = 1 ]; then
    echo ">> stopping the server on $REMOTE_HOST"
    run_remote stop "$rc"
    run_remote dropdb
  fi
  exit "$rc"
}
trap cleanup EXIT

if curl -fsS -o /dev/null --max-time 2 "$BASE_URL/api/v1/health" 2>/dev/null; then
  echo "something already answers on $BASE_URL (a stale tunnel?); free the port or set E2E_PORT" >&2
  exit 2
fi

echo ">> building the frontend"
(cd "$ROOT/frontend" && npm run build)

# The sync deletes things remotely, so it first refuses a destination that is, or resolves
# through, a symlink (e.g. one pointing at the real ~/projects/date-app). $1 = DEST (under $HOME).
SYNC_REMOTE='
d="$HOME/$1"
home=$(realpath "$HOME")
# Every part of the path that exists must be a real directory under $HOME, checked before
# anything is created or deleted.
for p in "$HOME/projects" "$HOME/projects/date-app-ci" "$d" "$d/backend" "$d/backend/.venv" "$d/frontend" "$d/frontend/dist" "$d/run" "$d/run/server.log" "$d/run/server.pid" "$d/run/server.start"; do
  if [ -L "$p" ]; then echo "refusing: $p is a symlink" >&2; exit 2; fi
  if [ -e "$p" ] && [ "$(realpath "$p")" != "$home/${p#"$HOME"/}" ]; then
    echo "refusing: $p does not resolve to $home/${p#"$HOME"/}" >&2
    exit 2
  fi
done
# Settings read .env and ../.env, which would override the demo setup. Refuse before deleting.
if [ -e "$d/.env" ] || [ -e "$d/backend/.env" ]; then
  echo "refusing: a .env exists under $d" >&2
  exit 2
fi
mkdir -p "$d/backend" "$d/frontend" || exit 2
cd "$d" &&
  find backend -mindepth 1 -maxdepth 1 ! -name .venv -exec rm -rf {} + &&
  rm -rf frontend/dist && tar -xf -
'

echo ">> syncing backend/ and frontend/dist -> $REMOTE_HOST:~/$DEST"
tar -C "$ROOT" \
  --exclude=.venv --exclude=__pycache__ --exclude=.mypy_cache \
  --exclude=.ruff_cache --exclude=.pytest_cache \
  -cf - backend frontend/dist |
  "${SSH[@]}" "bash -c $(printf '%q' "$SYNC_REMOTE") _ $(printf '%q' "$DEST")" ||
  { echo "sync failed" >&2; exit 2; }

echo ">> starting the server on $REMOTE_HOST (db $DB, port $E2E_PORT)"
REMOTE_TOUCHED=1
run_remote start

echo ">> opening the tunnel $BASE_URL -> $REMOTE_HOST:$E2E_PORT"
ssh -o BatchMode=yes -o ExitOnForwardFailure=yes -N \
  -L "${E2E_PORT}:127.0.0.1:${E2E_PORT}" "$REMOTE_HOST" &
TUNNEL_PID=$!

up=0
for _ in $(seq 1 60); do
  if curl -fsS -o /dev/null --max-time 2 "$BASE_URL/api/v1/health" 2>/dev/null; then up=1; break; fi
  kill -0 "$TUNNEL_PID" 2>/dev/null || { echo "the tunnel died" >&2; break; }
  sleep 1
done
[ "$up" = 1 ] || { echo "the server did not answer on $BASE_URL within 60 s" >&2; exit 1; }
echo ">> server is up; running Playwright"

cd "$ROOT/frontend"
E2E_BASE_URL="$BASE_URL" npx playwright test "$@"
