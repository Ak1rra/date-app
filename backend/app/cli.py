"""DATE APP - CLI.

One entry point for building, running and deploying the app. Installed with
`uv sync` it is the `dateapp` console script:

    dateapp build                 # uv sync --frozen + npm ci + build frontend into frontend/dist
    dateapp serve                 # launch the API + built frontend on PORT (default 8090)
    dateapp serve --build         # rebuild the frontend, then launch
    dateapp status                # pretty-print /api/v1/health and /api/v1/meta
    dateapp migrate               # alembic upgrade head
    dateapp backup --keep 14      # pg_dump to backups/, keep the newest 14
    dateapp daemon install        # run as a systemd service (Linux); see `daemon --help`
    dateapp daemon update         # git pull, backup, build, migrate, restart, health check

`serve` does not install anything: dependency setup and the frontend bundle live in
`dateapp build`. Node.js 20+ is needed to build the frontend; uv is found on PATH, in
.venv/, ~/.local/bin or ~/.cargo/bin (non-login SSH sessions and systemd do not have
~/.local/bin on PATH).
"""

from __future__ import annotations

import getpass
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from typing import Any, NoReturn

import click
import httpx
from sqlalchemy.engine import make_url

from app import __version__
from app.config import REPO_ROOT, Settings, get_settings

REPO_DIR = str(REPO_ROOT)
BACKEND_DIR = os.path.join(REPO_DIR, "backend")
VENV_DIR = os.path.join(BACKEND_DIR, ".venv")
FRONTEND_DIR = os.path.join(REPO_DIR, "frontend")
DIST_DIR = os.path.join(FRONTEND_DIR, "dist")
DIST_INDEX = os.path.join(DIST_DIR, "index.html")
BACKUPS_DIR = os.path.join(REPO_DIR, "backups")
BACKUP_PREFIX = "dateapp-"
BACKUP_SUFFIX = ".sql.gz"
HEALTH_PATH = "/api/v1/health"
META_PATH = "/api/v1/meta"


# -- Helpers -------------------------------------------------------------------------------


def _settings() -> Settings:
    """Settings as the app itself reads them: ../.env is resolved relative to backend/."""
    os.chdir(BACKEND_DIR)
    return get_settings()


def _fail(message: str, *hints: str) -> NoReturn:
    click.secho(f"  [!] {message}", fg="red")
    for hint in hints:
        click.secho(f"      {hint}", fg="yellow")
    sys.exit(1)


def _banner(title: str) -> None:
    click.echo("")
    click.echo("=" * 58)
    click.echo(f"  DATE APP  v{__version__}  {title}")
    click.echo("=" * 58)


def _base_url(host: str, port: int) -> str:
    return f"http://{host}:{port}"


def _find_uv() -> str | None:
    """Locate `uv`: PATH first, then the places the installers put it."""
    found = shutil.which("uv")
    if found:
        return found
    candidates = [
        os.path.join(VENV_DIR, "bin", "uv"),
        os.path.join(VENV_DIR, "Scripts", "uv.exe"),
        os.path.expanduser("~/.local/bin/uv"),
        os.path.expanduser("~/.cargo/bin/uv"),
    ]
    return next((c for c in candidates if os.path.isfile(c)), None)


def _sync_python_deps() -> None:
    """Bring backend/.venv in line with pyproject.toml / uv.lock (`uv sync --frozen`)."""
    uv = _find_uv()
    if uv is None:
        _fail(
            "uv not found - cannot sync Python dependencies.",
            "Install it with:  pip3 install uv",
            "Or skip this step with:  dateapp build --no-sync",
        )

    click.echo("  Syncing Python dependencies (uv sync --frozen)...")
    result = subprocess.run([uv, "sync", "--frozen"], cwd=BACKEND_DIR)
    if result.returncode != 0:
        click.secho("  [!] uv sync failed.", fg="red")
        sys.exit(result.returncode)
    click.secho("  Python dependencies in sync (backend/.venv)", fg="green")


def _find_npm() -> str | None:
    """Locate `npm`: PATH first, then a repo-local toolchain at .toolchain/node/bin."""
    found = shutil.which("npm")
    if found:
        return found
    local = os.path.join(REPO_DIR, ".toolchain", "node", "bin", "npm")
    return local if os.path.isfile(local) else None


def _npm_env(npm_path: str) -> dict[str, str]:
    """Environment for an npm run, with npm's own directory on PATH."""
    env = os.environ.copy()
    bin_dir = os.path.dirname(os.path.abspath(npm_path))
    env["PATH"] = bin_dir + os.pathsep + env.get("PATH", "")
    return env


def _node_modules_stale() -> bool:
    """True when node_modules is missing or older than package-lock.json."""
    modules = os.path.join(FRONTEND_DIR, "node_modules")
    if not os.path.isdir(modules):
        return True
    lock = os.path.join(FRONTEND_DIR, "package-lock.json")
    return os.path.isfile(lock) and os.path.getmtime(lock) > os.path.getmtime(modules)


def _frontend_sources_newer_than_build(dist_index: str | None = None) -> bool:
    """True when a frontend source is newer than the built index.html (cheap mtime walk)."""
    dist_index = dist_index or DIST_INDEX
    if not os.path.isfile(dist_index):
        return True
    built_at = os.path.getmtime(dist_index)
    watched = [
        os.path.join(FRONTEND_DIR, "src"),
        os.path.join(FRONTEND_DIR, "public"),
        os.path.join(FRONTEND_DIR, "index.html"),
        os.path.join(FRONTEND_DIR, "package.json"),
        os.path.join(FRONTEND_DIR, "vite.config.ts"),
    ]
    for target in watched:
        if os.path.isfile(target):
            if os.path.getmtime(target) > built_at:
                return True
        elif os.path.isdir(target):
            for root, _dirs, files in os.walk(target):
                if any(os.path.getmtime(os.path.join(root, name)) > built_at for name in files):
                    return True
    return False


def _run_build(install: bool) -> None:
    """Build the frontend into frontend/dist. Exits non-zero on failure.

    `install` allows `npm ci`, which only runs when node_modules is missing or older than
    package-lock.json.
    """
    npm = _find_npm()
    if npm is None:
        _fail(
            "npm not found.",
            "The frontend is a Vite app and needs Node.js 20+ to build.",
            "Install Node (https://nodejs.org), or unpack a runtime into .toolchain/node/.",
        )

    env = _npm_env(npm)
    if install and _node_modules_stale():
        click.echo("  Installing frontend dependencies (npm ci)...")
        result = subprocess.run([npm, "ci"], cwd=FRONTEND_DIR, env=env)
        if result.returncode != 0:
            click.secho("  [!] npm ci failed.", fg="red")
            sys.exit(result.returncode)

    click.echo("  Building frontend (npm run build)...")
    result = subprocess.run([npm, "run", "build"], cwd=FRONTEND_DIR, env=env)
    if result.returncode != 0:
        click.secho("  [!] Frontend build failed.", fg="red")
        sys.exit(result.returncode)

    if not os.path.isfile(DIST_INDEX):
        _fail("Build reported success but frontend/dist/index.html is missing.")

    total = sum(
        os.path.getsize(os.path.join(root, name))
        for root, _dirs, files in os.walk(DIST_DIR)
        for name in files
    )
    click.secho(f"  Built frontend/dist ({total / 1024:.0f} kB)", fg="green")


def _run_migrate() -> None:
    """`alembic upgrade head` in backend/, with the interpreter running this CLI."""
    click.echo("  Applying migrations (alembic upgrade head)...")
    result = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=BACKEND_DIR)
    if result.returncode != 0:
        click.secho("  [!] Migration failed.", fg="red")
        sys.exit(result.returncode)
    click.secho("  Database at head", fg="green")


def _pg_dump_args(database_url: str, out_path: str) -> tuple[list[str], dict[str, str]]:
    """pg_dump argv and extra environment for a SQLAlchemy URL. Pure.

    The password only ever travels in PGPASSWORD, never on the command line. -Z makes
    pg_dump gzip the plain-text dump itself.
    """
    url = make_url(database_url)
    argv = ["pg_dump", "--no-password"]
    if url.host:
        argv += ["-h", url.host]
    if url.port:
        argv += ["-p", str(url.port)]
    if url.username:
        argv += ["-U", url.username]
    if url.database:
        argv += ["-d", url.database]
    argv += ["-Z", "6", "-f", out_path]
    env = {"PGPASSWORD": url.password} if url.password else {}
    return argv, env


def _backup_files(directory: str) -> list[str]:
    """Backup files in `directory`, oldest first (the timestamp in the name sorts)."""
    if not os.path.isdir(directory):
        return []
    return sorted(
        name
        for name in os.listdir(directory)
        if name.startswith(BACKUP_PREFIX) and name.endswith(BACKUP_SUFFIX)
    )


def _run_backup(keep: int) -> str:
    """Dump the database to backups/ and prune to the newest `keep`. Returns the file path."""
    if shutil.which("pg_dump") is None:
        _fail(
            "pg_dump not found - cannot back up the database.",
            "Install the PostgreSQL client tools (Debian: apt install postgresql-client).",
        )

    os.makedirs(BACKUPS_DIR, mode=0o700, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = os.path.join(BACKUPS_DIR, f"{BACKUP_PREFIX}{stamp}{BACKUP_SUFFIX}")
    partial = path + ".part"

    argv, extra_env = _pg_dump_args(_settings().database_url, partial)
    click.echo("  Backing up the database (pg_dump)...")
    try:
        result = subprocess.run(argv, env={**os.environ, **extra_env})
        if result.returncode != 0:
            click.secho("  [!] pg_dump failed.", fg="red")
            sys.exit(result.returncode)
        os.replace(partial, path)
    finally:
        # An interrupt, an exception or a failed dump never leaves a half-written .part behind.
        if os.path.isfile(partial):
            os.remove(partial)

    for name in _backup_files(BACKUPS_DIR)[: -keep if keep > 0 else None]:
        os.remove(os.path.join(BACKUPS_DIR, name))
    click.secho(f"  Backup written: {path} ({os.path.getsize(path) / 1024:.0f} kB)", fg="green")
    return path


# -- CLI -----------------------------------------------------------------------------------


@click.group()
def cli() -> None:
    """DATE APP command-line interface."""


@cli.command()
@click.option(
    "--sync/--no-sync",
    default=True,
    show_default=True,
    help="Sync Python dependencies into backend/.venv with `uv sync --frozen` first.",
)
@click.option(
    "--install/--no-install",
    default=True,
    show_default=True,
    help="Run `npm ci` when node_modules is missing or older than package-lock.json.",
)
def build(sync: bool, install: bool) -> None:
    """
    Sync Python dependencies and build the frontend.

    \b
      1. `uv sync --frozen` - brings backend/.venv in line with pyproject.toml / uv.lock
      2. `npm ci` (when node_modules is stale), then `npm run build` - compiles
         frontend/src into frontend/dist, which the API process serves

    Run it after a fresh checkout and after pulling. Use --no-sync to build the frontend alone.
    """
    _banner("build")
    if sync:
        _sync_python_deps()
    _run_build(install)
    click.echo("")


@cli.command()
@click.option(
    "--port",
    default=None,
    type=int,
    show_default="PORT env or 8090",
    help="Port to serve the API + frontend on.",
)
@click.option("--host", default="0.0.0.0", show_default=True, help="Interface to bind.")
@click.option(
    "--build/--no-build",
    "do_build",
    default=None,
    help="Build the frontend before serving. Default: build only when frontend/dist is "
    "missing; warn (but still start) when it is stale.",
)
def serve(port: int | None, host: str, do_build: bool | None) -> None:
    """
    Launch the API server (which also serves the built frontend).

    Does NOT sync dependencies - run `dateapp build` first. The job worker runs inside
    this process (RUN_WORKER). X-Forwarded-* headers are trusted only from
    FORWARDED_ALLOW_IPS.
    """
    settings = _settings()
    port = settings.port if port is None else port
    dist_index = os.path.join(str(settings.resolved_frontend_dist), "index.html")

    _banner("Date Planner")
    db = make_url(settings.database_url)
    click.echo(f"  Demo mode  : {'on' if settings.demo_mode else 'off'}")
    click.echo(f"  Database   : {db.host}:{db.port or 5432}/{db.database}")
    click.echo(f"  Worker     : {'in-process' if settings.run_worker else 'disabled'}")
    click.echo(f"  Proxies    : {settings.forwarded_allow_ips}")

    # A missing bundle is not fatal to the API, so only an explicit --no-build starts without
    # it (the app answers 503 frontend_unavailable for pages). A stale one only warns.
    built = os.path.isfile(dist_index)
    if do_build is True:
        _run_build(install=True)
    elif do_build is None and not built:
        click.secho("  [!] frontend/dist missing - building it now.", fg="yellow")
        _run_build(install=True)
    elif not built:
        click.secho(
            "  [!] frontend/dist missing and --no-build given - the API will run, but "
            "pages answer 503 frontend_unavailable.",
            fg="yellow",
        )
        click.secho("      Build it with: dateapp build", fg="yellow")
    elif _frontend_sources_newer_than_build(dist_index):
        click.secho(
            "  [!] frontend/dist is older than frontend/src - serving a stale frontend.",
            fg="yellow",
        )
        click.secho("      Rebuild with: dateapp build", fg="yellow")

    click.echo("")
    click.echo(f"  Serving on http://{host}:{port}")
    click.echo("=" * 58)
    click.echo("")

    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        proxy_headers=True,
        forwarded_allow_ips=settings.forwarded_allow_ips,
        workers=1,
    )


def _get_json(url: str, timeout: float = 6.0) -> tuple[int, Any] | None:
    """GET `url`; (status, parsed body or None), or None when nothing answers."""
    try:
        resp = httpx.get(url, timeout=timeout)
    except httpx.HTTPError:
        return None
    try:
        body = resp.json()
    except ValueError:
        body = None
    return resp.status_code, body


@cli.command()
@click.option("--host", default="127.0.0.1", show_default=True, help="Backend host.")
@click.option(
    "--port",
    default=None,
    type=int,
    show_default="PORT env or 8090",
    help="Backend port.",
)
def status(host: str, port: int | None) -> None:
    """Fetch and pretty-print /api/v1/health and /api/v1/meta."""
    port = _settings().port if port is None else port
    base = _base_url(host, port)
    health = _get_json(base + HEALTH_PATH)
    if health is None:
        click.secho(f"  [!] Backend not reachable on {host}:{port}  (down)", fg="red")
        sys.exit(1)

    code, body = health
    body = body if isinstance(body, dict) else {}
    meta = _get_json(base + META_PATH)
    meta_body = meta[1] if meta and meta[0] == 200 and isinstance(meta[1], dict) else {}
    healthy = code == 200

    click.echo("")
    click.secho(
        f"  Status    : {'up' if healthy else 'degraded'}",
        fg="green" if healthy else "red",
    )
    click.echo(f"  Database  : {body.get('db', 'unknown')}")
    click.echo(f"  Version   : {meta_body.get('version', 'unknown')}")
    demo = meta_body.get("demo_mode")
    click.echo(f"  Demo mode : {'unknown' if demo is None else ('on' if demo else 'off')}")
    click.echo("")
    if not healthy:
        sys.exit(1)


@cli.command()
def migrate() -> None:
    """Apply database migrations (`alembic upgrade head`)."""
    _banner("migrate")
    _run_migrate()
    click.echo("")


@cli.command()
@click.option(
    "--keep",
    default=14,
    show_default=True,
    type=click.IntRange(min=1),
    help="Keep only the newest N backups.",
)
def backup(keep: int) -> None:
    """Dump the database to backups/dateapp-<UTC timestamp>.sql.gz and prune old dumps."""
    _banner("backup")
    _run_backup(keep)
    click.echo("")


# -- Daemon (systemd) ----------------------------------------------------------------------
#
# `dateapp daemon ...` installs and maintains the app as a systemd service. One rule keeps
# privilege handling consistent: git / uv / npm / pg_dump always run as the invoking user (so
# .venv, node_modules and .git stay owned by the repo's owner), and every *privileged* step goes
# through `_privileged()`, which runs it directly as root or prefixes `sudo` otherwise. The
# service control calls are exactly `sudo systemctl <start|stop|restart> date-app` so a sudoers
# rule can match them literally. Nothing ever re-execs this CLI under sudo.

SERVICE_NAME = "date-app"
UNIT_PATH = f"/etc/systemd/system/{SERVICE_NAME}.service"
LOGROTATE_PATH = f"/etc/logrotate.d/{SERVICE_NAME}"
LOGS_DIR = os.path.join(REPO_DIR, "logs")
LOG_FILE = os.path.join(LOGS_DIR, f"{SERVICE_NAME}.log")
DEFAULT_PORT = 8090
HEALTH_WAIT_SECONDS = 30


_NAME_RE = re.compile(r"[a-z_][a-z0-9_-]{0,31}")
_REPO_DIR_RE = re.compile(r"/[A-Za-z0-9/._-]*")


def _check_name(kind: str, value: str) -> None:
    """Raise ValueError unless `value` is a plain account name (no whitespace, %, quotes)."""
    if _NAME_RE.fullmatch(value) is None:
        raise ValueError(f"invalid {kind} {value!r}: must match [a-z_][a-z0-9_-]{{0,31}}")


def _check_repo_dir(repo_dir: str) -> None:
    """Raise ValueError unless `repo_dir` is an absolute path of only [A-Za-z0-9/._-]."""
    if _REPO_DIR_RE.fullmatch(repo_dir) is None:
        raise ValueError(
            f"invalid repository path {repo_dir!r}: must be absolute and contain only "
            "letters, digits and / . _ - (no spaces, quotes or %)"
        )


def _render_unit(repo_dir: str, user: str, port: int) -> str:
    """
    Render the systemd unit text. Pure - no filesystem or platform access - so it is
    testable anywhere. `repo_dir` is the checkout on the *target* box. Raises ValueError for
    a user or path that could alter the unit (the file is written as root).

    WorkingDirectory is backend/ because Settings reads ../.env relative to it. stdout and
    stderr both append to <repo>/logs/date-app.log (not the journal); `daemon install`
    creates the directory. TimeoutStopSec is the backstop for a worker that will not stop.
    """
    _check_repo_dir(repo_dir)
    _check_name("user", user)
    exe = f"{repo_dir}/backend/.venv/bin/dateapp"
    lines = [
        "[Unit]",
        "Description=Date App date planner",
        "After=network-online.target postgresql.service",
        "Wants=network-online.target",
        "",
        "[Service]",
        "Type=simple",
        f"User={user}",
        f"WorkingDirectory={repo_dir}/backend",
        "Environment=PYTHONUNBUFFERED=1",
        f"StandardOutput=append:{repo_dir}/logs/{SERVICE_NAME}.log",
        f"StandardError=append:{repo_dir}/logs/{SERVICE_NAME}.log",
        f"ExecStart={exe} serve --port {port} --no-build",
        "Restart=on-failure",
        "RestartSec=5",
        "TimeoutStopSec=20",
        "",
        "[Install]",
        "WantedBy=multi-user.target",
        "",
    ]
    return "\n".join(lines)


def _render_logrotate(repo_dir: str, user: str, group: str) -> str:
    """
    Render the logrotate config for <repo>/logs/date-app.log. Pure.

    `copytruncate` is required: systemd holds the `append:` fd open and there is no postrotate
    hook to reopen it. `su` is required because logs/ is owned by the service user, and
    logrotate refuses to rotate in a directory writable by a non-root user without it.
    Raises ValueError for a user, group or path that could alter the file.
    """
    _check_repo_dir(repo_dir)
    _check_name("user", user)
    _check_name("group", group)
    path = f"{repo_dir}/logs/{SERVICE_NAME}.log"
    return "\n".join(
        [
            f"{path} {{",
            f"    su {user} {group}",
            "    daily",
            "    rotate 14",
            "    compress",
            "    delaycompress",
            "    missingok",
            "    notifempty",
            "    maxsize 50M",
            "    copytruncate",
            "}",
            "",
        ]
    )


def _primary_group(user: str) -> str:
    """The user's primary group name (raises KeyError/ImportError off-Linux or if unknown)."""
    import grp
    import pwd

    return grp.getgrgid(pwd.getpwnam(user).pw_gid).gr_name


def _is_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0


def _default_daemon_user() -> str:
    """The user the service should run as: whoever invoked sudo, else whoever is running us."""
    return os.environ.get("SUDO_USER") or getpass.getuser()


def _require_systemd() -> None:
    """Exit non-zero with a clear message unless this is a systemd Linux host."""
    if not sys.platform.startswith("linux"):
        _fail(f"The daemon commands need Linux with systemd (this is {sys.platform}).")
    if shutil.which("systemctl") is None or not os.path.isdir("/run/systemd/system"):
        _fail("systemd is not running on this machine (no systemctl).")


def _privileged(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
    """Run `cmd` as root: directly when already root, through `sudo` otherwise."""
    if not _is_root():
        if shutil.which("sudo") is None:
            _fail("Root is required and sudo is not available - re-run this command as root.")
        cmd = ["sudo", *cmd]
    return subprocess.run(cmd, **kwargs)


def _systemctl(*args: str, check: bool = True) -> int:
    result = _privileged(["systemctl", *args])
    if check and result.returncode != 0:
        click.secho(f"  [!] systemctl {' '.join(args)} failed.", fg="red")
        sys.exit(result.returncode)
    return result.returncode


def _daemon_banner(title: str) -> None:
    _banner(f"daemon {title}")


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    """Run git in the repo as the current user, capturing text output."""
    return subprocess.run(["git", *args], cwd=REPO_DIR, capture_output=True, text=True)


def _git_fail(what: str, result: subprocess.CompletedProcess[str]) -> NoReturn:
    click.secho(f"  [!] {what} failed.", fg="red")
    detail = (result.stderr or result.stdout or "").strip()
    if detail:
        click.echo("      " + detail.replace("\n", "\n      "))
    sys.exit(result.returncode or 1)


def _service_port() -> int:
    """The port the installed service listens on (from its unit), else the configured one."""
    try:
        with open(UNIT_PATH, encoding="utf-8") as f:
            match = re.search(r"^ExecStart=.*\sserve\s.*--port\s+(\d+)", f.read(), re.MULTILINE)
    except OSError:
        match = None
    return int(match.group(1)) if match else _settings().port


def _log_tail(lines: int = 20) -> list[str]:
    try:
        with open(LOG_FILE, encoding="utf-8", errors="replace") as f:
            return f.read().splitlines()[-lines:]
    except OSError:
        return []


def _wait_healthy(port: int, seconds: int = HEALTH_WAIT_SECONDS) -> bool:
    """Poll /api/v1/health for at most `seconds` of wall time; True on the first HTTP 200.

    Deadline-based: each request's timeout is capped by the time left, so a hung socket cannot
    stretch the wait past `seconds`.
    """
    url = _base_url("127.0.0.1", port) + HEALTH_PATH
    deadline = time.monotonic() + seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        probe = _get_json(url, timeout=min(2.0, remaining))
        if probe is not None and probe[0] == 200:
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(1.0, remaining))


@cli.group()
def daemon() -> None:
    """
    Run Date App as a systemd service (Linux).

    \b
      install    build, migrate, write the unit, enable it at boot, (re)start it
      update     git pull --ff-only, backup, build, migrate, restart, wait for health
      uninstall  stop, disable and remove the unit
      status / logs / start / stop / restart   (logs: <repo>/logs/date-app.log)

    git, uv, npm and pg_dump run as you; only the unit write and systemctl use sudo
    (automatically, unless you are already root). status and logs need no sudo.
    """


@daemon.command("install")
@click.option(
    "--port",
    default=DEFAULT_PORT,
    show_default=True,
    type=int,
    help="Port the service listens on.",
)
@click.option(
    "--user",
    "user",
    default=_default_daemon_user,
    show_default="you (or SUDO_USER)",
    help="User account the service runs as.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Print the unit that would be written and do nothing else.",
)
def daemon_install(port: int, user: str, dry_run: bool) -> None:
    """
    Install (or reinstall) the systemd service and start it.

    Builds first (uv sync, frontend) and applies migrations, so DATABASE_URL in .env must
    already point at a reachable database. Idempotent: re-running rewrites the unit, reloads
    systemd and restarts the service, so it is also how you change --port or --user.
    """
    # The unit and logrotate file are written as root, so resolve the group and refuse anything
    # that could alter them before printing, probing systemd or writing a thing.
    try:
        _check_repo_dir(REPO_DIR)
        _check_name("user", user)
        try:
            group = _primary_group(user)
        except ImportError:
            group = user  # not Linux; the systemd check below says so (dry run: a preview)
        except KeyError:
            if not dry_run:
                _fail(f"User '{user}' does not exist on this machine.")
            group = user  # preview only
        unit = _render_unit(REPO_DIR, user, port)
        logrotate = _render_logrotate(REPO_DIR, user, group)
    except ValueError as exc:
        _fail(str(exc))
    _daemon_banner("install")

    if dry_run:
        click.echo(f"  Would write {UNIT_PATH}:")
        click.echo("")
        click.echo(unit)
        click.echo(f"  Would write {LOGROTATE_PATH}:")
        click.echo("")
        click.echo(logrotate)
        click.echo(
            "  Then: systemctl daemon-reload; "
            f"systemctl enable {SERVICE_NAME}; systemctl restart {SERVICE_NAME}"
        )
        click.secho("  Dry run - nothing written.", fg="yellow")
        return

    _require_systemd()

    if shutil.which("logrotate") is None:
        click.secho(
            f"  [!] logrotate not found - logs/{SERVICE_NAME}.log will grow unbounded "
            "until it is installed.",
            fg="yellow",
        )

    _sync_python_deps()
    _run_build(install=True)
    _run_migrate()

    if not os.path.isfile(os.path.join(VENV_DIR, "bin", "dateapp")):
        _fail("backend/.venv/bin/dateapp not found - nothing to run.", "Create it with:  uv sync")

    click.echo(f"  Creating {LOGS_DIR} (owned by {user})")
    if user == getpass.getuser():
        os.makedirs(LOGS_DIR, exist_ok=True)
    else:
        result = _privileged(["install", "-d", "-o", user, "-m", "755", LOGS_DIR])
        if result.returncode != 0:
            click.secho(f"  [!] Could not create {LOGS_DIR}.", fg="red")
            sys.exit(result.returncode)

    click.echo(f"  Writing {UNIT_PATH}")
    result = _privileged(["tee", UNIT_PATH], input=unit, text=True, stdout=subprocess.DEVNULL)
    if result.returncode != 0:
        click.secho(f"  [!] Could not write {UNIT_PATH}.", fg="red")
        sys.exit(result.returncode)

    click.echo(f"  Writing {LOGROTATE_PATH}")
    result = _privileged(
        ["tee", LOGROTATE_PATH],
        input=logrotate,
        text=True,
        stdout=subprocess.DEVNULL,
    )
    if result.returncode != 0:
        click.secho(f"  [!] Could not write {LOGROTATE_PATH}.", fg="red")
        sys.exit(result.returncode)

    _systemctl("daemon-reload")
    _systemctl("enable", SERVICE_NAME)
    _systemctl("restart", SERVICE_NAME)
    click.secho(
        f"  Service '{SERVICE_NAME}' installed and running as {user} on port {port}.",
        fg="green",
    )
    click.echo("  Check it with:  dateapp daemon status   |   dateapp daemon logs -f")
    click.echo("")


@daemon.command("update")
def daemon_update() -> None:
    """
    Update the installed app and restart the service.

    Runs, in the repo as you: `git pull --ff-only`, a database backup, the same steps as
    `dateapp build`, `alembic upgrade head`, then restarts the service and waits up to 30 s
    for /api/v1/health. Aborts before touching anything if the service is not installed, the
    working tree has uncommitted changes to tracked files or the pull is not a fast-forward;
    a failed backup, build or migration stops the update before the restart.
    """
    _daemon_banner("update")
    _require_systemd()

    if not os.path.isfile(UNIT_PATH):
        _fail(
            f"{UNIT_PATH} not found - the service is not installed, nothing was changed.",
            "Install it with:  dateapp daemon install",
        )

    dirty = _git("status", "--porcelain", "--untracked-files=no")
    if dirty.returncode != 0:
        _git_fail("git status", dirty)
    if dirty.stdout.strip():
        click.secho("  [!] Working tree has uncommitted changes - refusing to update.", fg="red")
        click.echo("      " + dirty.stdout.strip().replace("\n", "\n      "))
        click.secho("      Commit, stash or discard them, then re-run.", fg="yellow")
        sys.exit(1)

    head = _git("rev-parse", "HEAD")
    if head.returncode != 0:
        _git_fail("git rev-parse", head)
    before = head.stdout.strip()

    click.echo("  Pulling (git pull --ff-only)...")
    pull = _git("pull", "--ff-only")
    if pull.returncode != 0:
        click.secho(
            "  [!] git pull --ff-only failed - local history has diverged or the remote is "
            "unreachable.",
            fg="red",
        )
        _git_fail("git pull", pull)

    after = _git("rev-parse", "HEAD").stdout.strip()
    if after != before:
        diff = _git("diff", "--name-only", before, after)
        changed = [p for p in diff.stdout.splitlines() if p] if diff.returncode == 0 else []
        click.secho(f"  Updated {before[:7]} -> {after[:7]} ({len(changed)} file(s))", fg="green")
    else:
        click.echo("  Already up to date.")

    # Each step exits non-zero on failure, so a broken backup/build/migration never restarts
    # the service onto a half-updated tree.
    _run_backup(keep=14)
    _sync_python_deps()
    _run_build(install=True)
    _run_migrate()

    click.echo(f"  Restarting {SERVICE_NAME}...")
    _systemctl("restart", SERVICE_NAME)

    port = _service_port()
    click.echo(f"  Waiting for health on port {port} (up to {HEALTH_WAIT_SECONDS} s)...")
    if _wait_healthy(port):
        click.secho("  Update complete; service restarted and healthy.", fg="green")
        click.echo("")
        return

    click.secho(
        f"  [!] {SERVICE_NAME} restarted but {HEALTH_PATH} did not answer 200 within "
        f"{HEALTH_WAIT_SECONDS} s.",
        fg="red",
    )
    tail = _log_tail()
    if tail:
        click.echo(f"      Last lines of {LOG_FILE}:")
        click.echo("      " + "\n      ".join(tail))
    sys.exit(1)


@daemon.command("uninstall")
def daemon_uninstall() -> None:
    """Stop and disable the service, then remove its unit file and logrotate config."""
    _daemon_banner("uninstall")
    _require_systemd()
    if not os.path.isfile(UNIT_PATH):
        # No unit to disable. A service still loaded and running without its unit file must be
        # stopped by hand first, or removing anything else would orphan it.
        if subprocess.run(["systemctl", "is-active", "--quiet", SERVICE_NAME]).returncode == 0:
            _fail(
                f"{SERVICE_NAME} is still active but its unit file is gone - nothing was removed.",
                f"Stop it with:  sudo systemctl stop {SERVICE_NAME}",
            )
        if os.path.isfile(LOGROTATE_PATH):
            result = _privileged(["rm", "-f", LOGROTATE_PATH])
            if result.returncode != 0:
                click.secho(f"  [!] Could not remove {LOGROTATE_PATH}.", fg="red")
                sys.exit(result.returncode)
            click.echo(f"  {UNIT_PATH} not found; removed leftover {LOGROTATE_PATH}.")
        else:
            click.echo(f"  Service '{SERVICE_NAME}' is not installed - nothing to remove.")
        return
    code = _systemctl("disable", "--now", SERVICE_NAME, check=False)
    if code != 0:
        click.secho(
            f"  [!] systemctl disable --now {SERVICE_NAME} failed - nothing was removed.",
            fg="red",
        )
        sys.exit(code)
    result = _privileged(["rm", "-f", UNIT_PATH, LOGROTATE_PATH])
    if result.returncode != 0:
        click.secho(f"  [!] Could not remove {UNIT_PATH} / {LOGROTATE_PATH}.", fg="red")
        sys.exit(result.returncode)
    _systemctl("daemon-reload")
    click.secho(f"  Service '{SERVICE_NAME}' removed.", fg="green")
    click.echo("")


@daemon.command("status")
def daemon_status() -> None:
    """Show `systemctl status` for the service (no sudo; exit code is systemctl's)."""
    _require_systemd()
    sys.exit(subprocess.run(["systemctl", "status", "--no-pager", SERVICE_NAME]).returncode)


@daemon.command("logs")
@click.option("--follow", "-f", is_flag=True, default=False, help="Stream new log lines.")
@click.option(
    "--lines", "-n", default=100, show_default=True, type=int, help="How many recent lines."
)
def daemon_logs(follow: bool, lines: int) -> None:
    """Show the service log (logs/date-app.log in the repo; no sudo)."""
    if not os.path.isfile(LOG_FILE):
        _fail(
            f"{LOG_FILE} does not exist yet.",
            "The service writes it once installed and started: dateapp daemon install",
        )
    cmd = ["tail", "-n", str(lines), LOG_FILE]
    if follow:
        cmd.insert(1, "-F")
    try:
        sys.exit(subprocess.run(cmd).returncode)
    except KeyboardInterrupt:
        sys.exit(0)


def _daemon_control(action: str) -> None:
    _require_systemd()
    _systemctl(action, SERVICE_NAME)
    click.secho(f"  {SERVICE_NAME}: {action} OK", fg="green")


@daemon.command("start")
def daemon_start() -> None:
    """Start the service."""
    _daemon_control("start")


@daemon.command("stop")
def daemon_stop() -> None:
    """Stop the service."""
    _daemon_control("stop")


@daemon.command("restart")
def daemon_restart() -> None:
    """Restart the service."""
    _daemon_control("restart")


if __name__ == "__main__":
    cli()
