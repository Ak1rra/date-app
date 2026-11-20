"""`dateapp` CLI: build, serve, status, migrate, backup and the pure helpers.

Nothing here runs uv, npm, pg_dump, alembic or uvicorn for real: `subprocess.run` and the
platform probes are replaced on the cli module, so the tests run on any OS.
"""

import os
import subprocess
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from click.testing import CliRunner

from app import __version__
from app import cli as cli_mod
from tests.conftest import make_settings

DB_URL = "postgresql+asyncpg://dateapp:s3cr3t-pw@127.0.0.1:5432/dateapp"


def invoke(*args: str) -> Any:
    return CliRunner().invoke(cli_mod.cli, list(args))


def test_help_lists_top_level_commands() -> None:
    result = invoke("--help")
    assert result.exit_code == 0
    for name in ("build", "serve", "status", "migrate", "backup", "daemon"):
        assert name in result.output


def test_console_script_target_is_the_click_group() -> None:
    assert isinstance(cli_mod.cli, cli_mod.click.Group)


# -- _pg_dump_args -------------------------------------------------------------------------


def test_pg_dump_args_keep_password_out_of_argv() -> None:
    argv, env = cli_mod._pg_dump_args(DB_URL, "/tmp/out.sql.gz")
    assert env == {"PGPASSWORD": "s3cr3t-pw"}
    assert not any("s3cr3t-pw" in a for a in argv)
    assert argv[0] == "pg_dump"
    assert argv[argv.index("-h") + 1] == "127.0.0.1"
    assert argv[argv.index("-p") + 1] == "5432"
    assert argv[argv.index("-U") + 1] == "dateapp"
    assert argv[argv.index("-d") + 1] == "dateapp"
    assert argv[argv.index("-f") + 1] == "/tmp/out.sql.gz"
    assert "postgresql" not in " ".join(argv)  # no URL, so no +asyncpg either


def test_pg_dump_args_without_password() -> None:
    argv, env = cli_mod._pg_dump_args("postgresql+asyncpg://dateapp@db.lan/dateapp", "x")
    assert env == {}
    assert "-p" not in argv


# -- backup --------------------------------------------------------------------------------


@pytest.fixture
def backups(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "backups"
    monkeypatch.setattr(cli_mod, "BACKUPS_DIR", str(directory))
    monkeypatch.setattr(cli_mod, "_settings", lambda: make_settings(database_url=DB_URL))
    monkeypatch.setattr(cli_mod.shutil, "which", lambda name: f"/usr/bin/{name}")
    return directory


def test_backup_runs_pg_dump_with_password_in_env_only(backups: Path) -> None:
    seen: dict[str, Any] = {}

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        seen["cmd"], seen["env"] = cmd, kwargs["env"]
        Path(cmd[cmd.index("-f") + 1]).write_bytes(b"dump")
        return subprocess.CompletedProcess(cmd, 0)

    with mock.patch.object(cli_mod.subprocess, "run", side_effect=fake_run):
        result = invoke("backup")
    assert result.exit_code == 0, result.output
    assert not any("s3cr3t-pw" in a for a in seen["cmd"])
    assert "s3cr3t-pw" not in result.output
    assert seen["env"]["PGPASSWORD"] == "s3cr3t-pw"
    [written] = [p.name for p in backups.iterdir()]
    assert written.startswith("dateapp-") and written.endswith(".sql.gz")


def test_backup_prunes_to_newest_keep(backups: Path) -> None:
    backups.mkdir()
    for stamp in ("20260101T000000Z", "20260102T000000Z", "20260103T000000Z"):
        (backups / f"dateapp-{stamp}.sql.gz").write_bytes(b"old")
    (backups / "notes.txt").write_text("not a backup")

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        Path(cmd[cmd.index("-f") + 1]).write_bytes(b"dump")
        return subprocess.CompletedProcess(cmd, 0)

    with mock.patch.object(cli_mod.subprocess, "run", side_effect=fake_run):
        result = invoke("backup", "--keep", "2")
    assert result.exit_code == 0, result.output
    names = sorted(p.name for p in backups.iterdir())
    assert "notes.txt" in names
    dumps = [n for n in names if n.endswith(".sql.gz")]
    assert len(dumps) == 2
    assert "dateapp-20260101T000000Z.sql.gz" not in dumps
    assert "dateapp-20260102T000000Z.sql.gz" not in dumps


def test_backup_failure_leaves_no_file_and_exits_nonzero(backups: Path) -> None:
    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        Path(cmd[cmd.index("-f") + 1]).write_bytes(b"half")
        return subprocess.CompletedProcess(cmd, 1)

    with mock.patch.object(cli_mod.subprocess, "run", side_effect=fake_run):
        result = invoke("backup")
    assert result.exit_code == 1
    assert "pg_dump failed" in result.output
    assert list(backups.iterdir()) == []


def test_backup_interrupt_leaves_no_partial_file(backups: Path) -> None:
    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        Path(cmd[cmd.index("-f") + 1]).write_bytes(b"half")
        raise KeyboardInterrupt

    with (
        mock.patch.object(cli_mod.subprocess, "run", side_effect=fake_run),
        pytest.raises(KeyboardInterrupt),
    ):
        cli_mod._run_backup(keep=14)
    assert list(backups.iterdir()) == []


def test_backup_replace_failure_leaves_no_partial_file(backups: Path) -> None:
    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        Path(cmd[cmd.index("-f") + 1]).write_bytes(b"dump")
        return subprocess.CompletedProcess(cmd, 0)

    with (
        mock.patch.object(cli_mod.subprocess, "run", side_effect=fake_run),
        mock.patch.object(cli_mod.os, "replace", side_effect=OSError("disk")),
        pytest.raises(OSError),
    ):
        cli_mod._run_backup(keep=14)
    assert list(backups.iterdir()) == []


def test_backup_prune_ignores_partial_files(backups: Path) -> None:
    backups.mkdir()
    (backups / "dateapp-20260101T000000Z.sql.gz").write_bytes(b"old")
    stale = backups / "dateapp-20251231T000000Z.sql.gz.part"
    stale.write_bytes(b"stale")
    assert cli_mod._backup_files(str(backups)) == ["dateapp-20260101T000000Z.sql.gz"]

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        Path(cmd[cmd.index("-f") + 1]).write_bytes(b"dump")
        return subprocess.CompletedProcess(cmd, 0)

    with mock.patch.object(cli_mod.subprocess, "run", side_effect=fake_run):
        result = invoke("backup", "--keep", "1")
    assert result.exit_code == 0, result.output
    assert stale.exists()
    assert len(cli_mod._backup_files(str(backups))) == 1


def test_backup_without_pg_dump_is_an_error(backups: Path) -> None:
    with (
        mock.patch.object(cli_mod.shutil, "which", return_value=None),
        mock.patch.object(cli_mod.subprocess, "run") as run,
    ):
        result = invoke("backup")
    assert result.exit_code == 1
    assert "[!] pg_dump not found" in result.output
    run.assert_not_called()


# -- migrate -------------------------------------------------------------------------------


def test_migrate_runs_alembic_with_current_interpreter() -> None:
    with mock.patch.object(
        cli_mod.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)
    ) as run:
        result = invoke("migrate")
    assert result.exit_code == 0, result.output
    run.assert_called_once_with(
        [cli_mod.sys.executable, "-m", "alembic", "upgrade", "head"], cwd=cli_mod.BACKEND_DIR
    )


def test_migrate_failure_exits_with_alembics_code() -> None:
    with mock.patch.object(
        cli_mod.subprocess, "run", return_value=subprocess.CompletedProcess([], 3)
    ):
        result = invoke("migrate")
    assert result.exit_code == 3
    assert "[!] Migration failed" in result.output


# -- build ---------------------------------------------------------------------------------


@pytest.fixture
def frontend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "frontend"
    (root / "node_modules").mkdir(parents=True)
    (root / "dist").mkdir()
    (root / "dist" / "index.html").write_text("<html></html>")
    (root / "package-lock.json").write_text("{}")
    old = 1_000_000_000
    os.utime(root / "package-lock.json", (old, old))
    monkeypatch.setattr(cli_mod, "FRONTEND_DIR", str(root))
    monkeypatch.setattr(cli_mod, "DIST_DIR", str(root / "dist"))
    monkeypatch.setattr(cli_mod, "DIST_INDEX", str(root / "dist" / "index.html"))
    monkeypatch.setattr(cli_mod, "_find_uv", lambda: "/usr/bin/uv")
    monkeypatch.setattr(cli_mod, "_find_npm", lambda: "/usr/bin/npm")
    return root


def run_build(*args: str) -> tuple[Any, list[list[str]]]:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    with mock.patch.object(cli_mod.subprocess, "run", side_effect=fake_run):
        return invoke("build", *args), calls


def test_build_syncs_then_builds_and_skips_fresh_node_modules(frontend: Path) -> None:
    result, calls = run_build()
    assert result.exit_code == 0, result.output
    assert f"DATE APP  v{__version__}  build" in result.output
    assert calls == [["/usr/bin/uv", "sync", "--frozen"], ["/usr/bin/npm", "run", "build"]]


def test_build_runs_npm_ci_when_lockfile_is_newer(frontend: Path) -> None:
    old = 1_000_000_000
    os.utime(frontend / "node_modules", (old, old))
    os.utime(frontend / "package-lock.json", (old + 100, old + 100))
    result, calls = run_build()
    assert result.exit_code == 0, result.output
    assert ["/usr/bin/npm", "ci"] in calls
    assert calls.index(["/usr/bin/npm", "ci"]) < calls.index(["/usr/bin/npm", "run", "build"])


def test_build_flags_skip_sync_and_install(frontend: Path) -> None:
    (frontend / "node_modules").rmdir()
    result, calls = run_build("--no-sync", "--no-install")
    assert result.exit_code == 0, result.output
    assert calls == [["/usr/bin/npm", "run", "build"]]


def test_build_without_npm_is_an_error(frontend: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_mod, "_find_npm", lambda: None)
    result, calls = run_build("--no-sync")
    assert result.exit_code == 1
    assert "[!] npm not found" in result.output
    assert calls == []


def test_build_without_uv_is_an_error(frontend: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_mod, "_find_uv", lambda: None)
    result, calls = run_build()
    assert result.exit_code == 1
    assert "[!] uv not found" in result.output
    assert calls == []


def test_find_uv_falls_back_to_local_bin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_mod.shutil, "which", lambda name: None)
    local = os.path.expanduser("~/.local/bin/uv")
    monkeypatch.setattr(cli_mod.os.path, "isfile", lambda p: p == local)
    assert cli_mod._find_uv() == local


# -- serve ---------------------------------------------------------------------------------


def serve(tmp_path: Path, *args: str, with_dist: bool = True) -> tuple[Any, mock.MagicMock]:
    dist = tmp_path / "dist"
    if with_dist:
        dist.mkdir(exist_ok=True)
        (dist / "index.html").write_text("<html></html>")
    settings = make_settings(
        port=8123, forwarded_allow_ips="192.168.1.164", frontend_dist=dist, demo_mode=False
    )
    with (
        mock.patch.object(cli_mod, "_settings", return_value=settings),
        mock.patch.object(cli_mod, "_frontend_sources_newer_than_build", return_value=False),
        mock.patch("uvicorn.run") as run,
    ):
        return invoke("serve", *args), run


def test_serve_launches_uvicorn_with_proxy_settings(tmp_path: Path) -> None:
    result, run = serve(tmp_path)
    assert result.exit_code == 0, result.output
    run.assert_called_once_with(
        "app.main:app",
        host="0.0.0.0",
        port=8123,
        proxy_headers=True,
        forwarded_allow_ips="192.168.1.164",
        workers=1,
    )


def test_serve_port_and_host_flags_win(tmp_path: Path) -> None:
    result, run = serve(tmp_path, "--port", "9000", "--host", "127.0.0.1", "--no-build")
    assert result.exit_code == 0, result.output
    assert run.call_args.kwargs["port"] == 9000
    assert run.call_args.kwargs["host"] == "127.0.0.1"


def test_serve_no_build_without_dist_warns_but_still_starts(tmp_path: Path) -> None:
    with mock.patch.object(cli_mod, "_run_build") as build:
        result, run = serve(tmp_path, "--no-build", with_dist=False)
    assert result.exit_code == 0, result.output
    assert "frontend/dist missing and --no-build" in result.output
    assert "frontend_unavailable" in result.output
    build.assert_not_called()
    run.assert_called_once()


def test_serve_builds_when_dist_missing_by_default(tmp_path: Path) -> None:
    with mock.patch.object(cli_mod, "_run_build") as build:
        result, run = serve(tmp_path, with_dist=False)
    assert result.exit_code == 0, result.output
    build.assert_called_once_with(install=True)
    run.assert_called_once()


def test_serve_build_flag_forces_a_build(tmp_path: Path) -> None:
    with mock.patch.object(cli_mod, "_run_build") as build:
        result, _ = serve(tmp_path, "--build")
    assert result.exit_code == 0, result.output
    build.assert_called_once_with(install=True)


# -- status --------------------------------------------------------------------------------


def test_status_up() -> None:
    answers = {
        "http://127.0.0.1:8123/api/v1/health": (200, {"status": "ok", "db": "ok"}),
        "http://127.0.0.1:8123/api/v1/meta": (200, {"demo_mode": True, "version": "9.9.9"}),
    }
    with (
        mock.patch.object(cli_mod, "_settings", return_value=make_settings(port=8123)),
        mock.patch.object(cli_mod, "_get_json", side_effect=answers.get),
    ):
        result = invoke("status")
    assert result.exit_code == 0, result.output
    assert "up" in result.output
    assert "9.9.9" in result.output
    assert "Demo mode : on" in result.output


def test_status_down() -> None:
    with mock.patch.object(cli_mod, "_get_json", return_value=None):
        result = invoke("status", "--port", "8123")
    assert result.exit_code == 1
    assert "[!] Backend not reachable on 127.0.0.1:8123" in result.output


def test_status_degraded_when_database_is_down() -> None:
    answers = {
        "http://127.0.0.1:8123/api/v1/health": (503, {"status": "degraded", "db": "error"}),
        "http://127.0.0.1:8123/api/v1/meta": (200, {"demo_mode": False, "version": "1.0.0"}),
    }
    with mock.patch.object(cli_mod, "_get_json", side_effect=answers.get):
        result = invoke("status", "--port", "8123")
    assert result.exit_code == 1
    assert "degraded" in result.output
    assert "Demo mode : off" in result.output
