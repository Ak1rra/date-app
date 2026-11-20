#!/usr/bin/env bash
# Run the backend checks (lint, format, types, tests) on the Linux box over SSH.
#
# Why: Smart App Control on the Windows dev machine blocks the compiled Python
# dependencies, so `uv run` does not work there. The Linux box has Postgres for the db tests.
#
# Usage:  bash scripts/remote-check.sh [--tree DIR] [--name NAME] [-- PYTEST_ARGS...]
#   --tree DIR    tree to check (default: the repo root containing this script)
#   --name NAME   remote workspace + test database name (default: basename of DIR).
#                 Lowercased, anything but a-z 0-9 '-' becomes '-', then it must match
#                 ^[a-z0-9][a-z0-9-]{0,39}$ (else exit 2). The database is
#                 dateapp_test_<NAME with '-' as '_'>, so name -> database is one-to-one and
#                 parallel worktrees need different names or they would share (and wipe) a DB.
#   -- ARGS       passed to pytest, e.g. `-- -m "not db" -k csrf`
# Env:
#   REMOTE_HOST   ssh target (default rogue@192.168.1.167)
#   TEST_PG       Postgres server URL WITHOUT a database name
#                 (default postgresql+asyncpg://dateapp_test:dateapp_test@127.0.0.1:5432).
#                 The DB tests DROP SCHEMA public CASCADE, so the script refuses (exit 2)
#                 unless the user is the dedicated role "dateapp_test" (which owns nothing
#                 but scratch databases) and the database name starts with dateapp_test_.
#   ALLOW_TEST_PG_USER   relax the user check to this one other role name (interim setups
#                 only); the dateapp_test_ database-name check always applies.
#
# What it does: streams DIR/backend to ~/projects/date-app-ci/NAME/backend on REMOTE_HOST,
# then runs every step even if an earlier one fails: uv sync --frozen, create the database
# if missing (a small asyncpg snippet in the synced env, so no psql is needed), ruff check,
# ruff format --check, mypy app, pytest (TEST_DATABASE_URL=<TEST_PG>/<db>).
# Prints PASS/FAIL per step and exits non-zero if any failed.

set -u -o pipefail

REMOTE_HOST="${REMOTE_HOST:-rogue@192.168.1.167}"
TEST_PG="${TEST_PG:-postgresql+asyncpg://dateapp_test:dateapp_test@127.0.0.1:5432}"
TREE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAME=""
PYTEST_ARGS=()

while [ $# -gt 0 ]; do
  case "$1" in
    --tree) TREE="$(cd "$2" && pwd)" || exit 2; shift 2 ;;
    --name) NAME="$2"; shift 2 ;;
    --) shift; PYTEST_ARGS=("$@"); break ;;
    -h | --help) sed -n '2,29p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown argument: $1 (see --help)" >&2; exit 2 ;;
  esac
done

[ -d "$TREE/backend" ] || { echo "no backend/ in $TREE" >&2; exit 2; }
NAME="${NAME:-$(basename "$TREE")}"
NAME="$(printf '%s' "$NAME" | tr 'A-Z' 'a-z' | tr -c 'a-z0-9\n-' '-')"
if ! [[ "$NAME" =~ ^[a-z0-9][a-z0-9-]{0,39}$ ]]; then
  echo "invalid --name '$NAME': must match ^[a-z0-9][a-z0-9-]{0,39}\$ after normalising" >&2
  exit 2
fi
DB="dateapp_test_${NAME//-/_}"   # one-to-one with NAME: no truncation, no collisions
DEST="projects/date-app-ci/${NAME}"   # relative to the remote $HOME

# Safety: the DB tests drop the public schema, so only ever point them at scratch databases.
case "$TEST_PG" in
  *://*) ;;
  *) echo "TEST_PG must be a URL like postgresql+asyncpg://user:pass@host:port" >&2; exit 2 ;;
esac
PG_AUTHORITY="${TEST_PG#*://}"
case "$PG_AUTHORITY" in
  */*) echo "TEST_PG must not include a database name (the script appends $DB)" >&2; exit 2 ;;
  *'?'*|*'#'*) echo "TEST_PG must not include query parameters or a fragment" >&2; exit 2 ;;
esac
PG_USER="${PG_AUTHORITY%%@*}"
PG_USER="${PG_USER%%:*}"
if [ "$PG_USER" != "dateapp_test" ] && { [ -z "${ALLOW_TEST_PG_USER:-}" ] || [ "$PG_USER" != "$ALLOW_TEST_PG_USER" ]; }; then
  echo "refusing: TEST_PG user is '$PG_USER', expected 'dateapp_test'" >&2
  echo "(DB tests drop schema public; use the dedicated role, or set ALLOW_TEST_PG_USER)" >&2
  exit 2
fi
case "$DB" in
  dateapp_test_?*) ;;
  *) echo "refusing: database name '$DB' must start with dateapp_test_" >&2; exit 2 ;;
esac

SSH=(ssh -o BatchMode=yes "$REMOTE_HOST")

echo ">> syncing $TREE/backend -> $REMOTE_HOST:~/$DEST/backend (db: $DB)"
tar -C "$TREE" \
  --exclude=.venv --exclude=__pycache__ --exclude=.mypy_cache \
  --exclude=.ruff_cache --exclude=.pytest_cache \
  -cf - backend |
  "${SSH[@]}" "rm -rf \"\$HOME/$DEST/backend\" && mkdir -p \"\$HOME/$DEST\" && tar -xf - -C \"\$HOME/$DEST\"" ||
  { echo "sync failed" >&2; exit 2; }

# The runner executes remotely; arguments: dest name db test_pg [pytest args...]
"${SSH[@]}" "bash -s -- $(printf '%q ' "$DEST" "$NAME" "$DB" "$TEST_PG" "${PYTEST_ARGS[@]}")" <<'REMOTE'
export PATH="$HOME/.local/bin:$PATH"
DEST="$HOME/$1" NAME="$2" DB="$3" TEST_PG="$4"
shift 4
cd "$DEST/backend" || exit 2

summary=()
failed=0
step() {
  local label="$1"
  shift
  echo
  echo "=== $label"
  "$@" </dev/null   # stdin is this script; keep commands from reading it
  local rc=$?
  if [ $rc -eq 0 ]; then summary+=("PASS  $label"); else summary+=("FAIL  $label (exit $rc)"); failed=1; fi
}

PYSNIP=$(cat <<'PY'
import asyncio
import sys

import asyncpg


async def main(base: str, db: str) -> None:
    assert db.startswith("dateapp_test_"), db  # never touch anything but scratch databases
    conn = await asyncpg.connect(base.replace("+asyncpg", "", 1) + "/postgres")
    try:
        if await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", db):
            print(f"database {db} exists")
        else:
            await conn.execute(f'CREATE DATABASE "{db}"')
            print(f"created database {db}")
    finally:
        await conn.close()


asyncio.run(main(sys.argv[1], sys.argv[2]))
PY
)

step "uv sync --frozen" uv sync --frozen
step "test database ($DB)" uv run python -c "$PYSNIP" "$TEST_PG" "$DB"
step "ruff check" uv run ruff check .
step "ruff format --check" uv run ruff format --check .
step "mypy app" uv run mypy app
step "pytest" env "TEST_DATABASE_URL=$TEST_PG/$DB" uv run pytest -q "$@"

echo
echo "=== summary ($NAME)"
printf '%s\n' "${summary[@]}"
exit $failed
REMOTE
