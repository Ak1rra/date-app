"""`dateapp daemon ...`: pure unit/logrotate renderers and the command sequences.

Nothing here touches systemctl, sudo, git, uv or npm: `subprocess.run` and every platform
probe are replaced on the cli module, so the tests run on any OS.
"""

import subprocess
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from click.testing import CliRunner

from app import cli as cli_mod

REPO = "/home/rogue/date-app"


def invoke(*args: str) -> Any:
    return CliRunner().invoke(cli_mod.cli, list(args))


def lines(text: str) -> list[str]:
    return text.splitlines()


def ran_systemctl(events: list[Any]) -> bool:
    return any(isinstance(e, list) and "systemctl" in e for e in events)


# -- renderers -----------------------------------------------------------------------------


def test_daemon_help_lists_commands() -> None:
    result = invoke("daemon", "--help")
    assert result.exit_code == 0
    for name in ("install", "update", "uninstall", "status", "logs", "start", "stop", "restart"):
        assert name in result.output


def test_unit_core_fields() -> None:
    unit = lines(cli_mod._render_unit(REPO, "rogue", 8090))
    assert "User=rogue" in unit
    assert f"WorkingDirectory={REPO}/backend" in unit
    assert "Environment=PYTHONUNBUFFERED=1" in unit
    assert f"ExecStart={REPO}/backend/.venv/bin/dateapp serve --port 8090 --no-build" in unit
    assert f"StandardOutput=append:{REPO}/logs/date-app.log" in unit
    assert f"StandardError=append:{REPO}/logs/date-app.log" in unit
    assert "Restart=on-failure" in unit
    assert "RestartSec=5" in unit
    assert "TimeoutStopSec=20" in unit
    assert "After=network-online.target postgresql.service" in unit
    assert "Wants=network-online.target" in unit
    assert "WantedBy=multi-user.target" in unit


def test_unit_port_is_rendered() -> None:
    assert "serve --port 9001 --no-build" in cli_mod._render_unit(REPO, "rogue", 9001)


BAD_REPOS = ["/home/r/date app", "/home/r/100%", '/home/r/"q"', "/home/r/a\nb", "rel/path", ""]
BAD_NAMES = ["rogue\nExecStartPre=/bin/evil", "ro gue", "ro%gue", "Rogue", "1rogue", "", "a" * 33]


@pytest.mark.parametrize("repo", BAD_REPOS)
def test_renderers_refuse_unsafe_repo_paths(repo: str) -> None:
    with pytest.raises(ValueError, match="repository path"):
        cli_mod._render_unit(repo, "rogue", 8090)
    with pytest.raises(ValueError, match="repository path"):
        cli_mod._render_logrotate(repo, "rogue", "rogue")


@pytest.mark.parametrize("name", BAD_NAMES)
def test_renderers_refuse_unsafe_user_and_group(name: str) -> None:
    with pytest.raises(ValueError, match="user"):
        cli_mod._render_unit(REPO, name, 8090)
    with pytest.raises(ValueError, match="user"):
        cli_mod._render_logrotate(REPO, name, "rogue")
    with pytest.raises(ValueError, match="group"):
        cli_mod._render_logrotate(REPO, "rogue", name)


def test_logrotate_directives() -> None:
    out = lines(cli_mod._render_logrotate(REPO, "rogue", "rogue"))
    assert out[0] == f"{REPO}/logs/date-app.log {{"
    assert out[-1] == "}"
    body = [line.strip() for line in out[1:-1]]
    assert "su rogue rogue" in body
    for directive in (
        "daily",
        "rotate 14",
        "compress",
        "delaycompress",
        "missingok",
        "notifempty",
        "maxsize 50M",
        "copytruncate",
    ):
        assert directive in body


# -- install -------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def linux_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real REPO_DIR is a Windows path on the dev machine; the validators want a Linux one."""
    monkeypatch.setattr(cli_mod, "REPO_DIR", REPO)


def test_install_dry_run_prints_units_and_runs_nothing() -> None:
    with (
        mock.patch.object(cli_mod.subprocess, "run") as run,
        mock.patch.object(cli_mod, "_require_systemd") as require,
    ):
        result = invoke("daemon", "install", "--dry-run", "--port", "8090", "--user", "rogue")
    assert result.exit_code == 0, result.output
    assert "[Service]" in result.output
    assert "User=rogue" in result.output
    assert "TimeoutStopSec=20" in result.output
    assert cli_mod.LOGROTATE_PATH in result.output
    assert "serve --port 8090 --no-build" in result.output
    run.assert_not_called()
    require.assert_not_called()


def test_install_sequence_non_root_uses_sudo_for_privileged_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], dict[str, Any]]] = []
    steps: list[str] = []
    monkeypatch.setattr(cli_mod, "_require_systemd", lambda: None)
    monkeypatch.setattr(cli_mod, "_is_root", lambda: False)
    monkeypatch.setattr(cli_mod, "_primary_group", lambda user: "rogue")
    monkeypatch.setattr(cli_mod.getpass, "getuser", lambda: "someone-else")
    monkeypatch.setattr(cli_mod.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(cli_mod.os.path, "isfile", lambda p: True)
    monkeypatch.setattr(cli_mod, "_sync_python_deps", lambda: steps.append("sync"))
    monkeypatch.setattr(cli_mod, "_run_build", lambda install: steps.append("build"))
    monkeypatch.setattr(cli_mod, "_run_migrate", lambda: steps.append("migrate"))

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0)

    with mock.patch.object(cli_mod.subprocess, "run", side_effect=fake_run):
        result = invoke("daemon", "install", "--port", "8090", "--user", "rogue")
    assert result.exit_code == 0, result.output
    assert steps == ["sync", "build", "migrate"]
    cmds = [c for c, _ in calls]
    assert all(c[0] == "sudo" for c in cmds)
    assert [c[1:] for c in cmds] == [
        ["install", "-d", "-o", "rogue", "-m", "755", cli_mod.LOGS_DIR],
        ["tee", cli_mod.UNIT_PATH],
        ["tee", cli_mod.LOGROTATE_PATH],
        ["systemctl", "daemon-reload"],
        ["systemctl", "enable", "date-app"],
        ["systemctl", "restart", "date-app"],
    ]
    assert "User=rogue" in calls[1][1]["input"]
    assert "su rogue rogue" in calls[2][1]["input"]


@pytest.mark.parametrize("dry_run", [True, False])
@pytest.mark.parametrize(
    "user", ["rogue\nExecStartPre=/bin/evil", "ro%gue", "ro gue", "Rogue", "$(id)"]
)
def test_install_refuses_unsafe_user_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, user: str, dry_run: bool
) -> None:
    monkeypatch.setattr(cli_mod, "_require_systemd", lambda: None)
    args = ["daemon", "install", "--user", user, *(["--dry-run"] if dry_run else [])]
    with mock.patch.object(cli_mod.subprocess, "run") as run:
        result = invoke(*args)
    assert result.exit_code == 1
    assert "[!] invalid user" in result.output
    assert "[Service]" not in result.output
    # The value is echoed escaped (repr), never as a raw newline that starts a new line.
    assert not any(line.startswith("ExecStartPre") for line in result.output.splitlines())
    run.assert_not_called()


@pytest.mark.parametrize("dry_run", [True, False])
@pytest.mark.parametrize("repo", ["/home/r/date app", "/home/r/100%", '/home/r/"q"'])
def test_install_refuses_unsafe_repo_path(
    monkeypatch: pytest.MonkeyPatch, repo: str, dry_run: bool
) -> None:
    monkeypatch.setattr(cli_mod, "REPO_DIR", repo)
    monkeypatch.setattr(cli_mod, "_require_systemd", lambda: None)
    args = ["daemon", "install", "--user", "rogue", *(["--dry-run"] if dry_run else [])]
    with mock.patch.object(cli_mod.subprocess, "run") as run:
        result = invoke(*args)
    assert result.exit_code == 1
    assert "[!] invalid repository path" in result.output
    assert "[Service]" not in result.output
    run.assert_not_called()


@pytest.mark.parametrize("group", ["bad%group", "bad\ngroup", "Domain Users"])
def test_install_refuses_unsafe_group_before_writing(
    monkeypatch: pytest.MonkeyPatch, group: str
) -> None:
    monkeypatch.setattr(cli_mod, "_require_systemd", lambda: None)
    monkeypatch.setattr(cli_mod, "_primary_group", lambda user: group)
    require = mock.Mock()
    monkeypatch.setattr(cli_mod, "_require_systemd", require)
    with (
        mock.patch.object(cli_mod.subprocess, "run") as run,
        mock.patch.object(cli_mod, "_sync_python_deps") as sync,
    ):
        result = invoke("daemon", "install", "--user", "rogue")
    assert result.exit_code == 1
    assert "[!] invalid group" in result.output
    assert result.output.lstrip().startswith("[!]")  # no banner or anything else first
    assert "DATE APP" not in result.output
    require.assert_not_called()
    run.assert_not_called()
    sync.assert_not_called()


def test_install_on_non_systemd_host_runs_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_mod, "_primary_group", lambda user: user)
    with (
        mock.patch.object(cli_mod.subprocess, "run") as run,
        mock.patch.object(cli_mod.sys, "platform", "win32"),
    ):
        result = invoke("daemon", "install", "--user", "rogue")
    assert result.exit_code != 0
    assert "systemd" in result.output
    run.assert_not_called()


# -- uninstall -----------------------------------------------------------------------------


@pytest.fixture
def uninstall_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Path, Path]:
    unit, logrotate = tmp_path / "date-app.service", tmp_path / "date-app.logrotate"
    monkeypatch.setattr(cli_mod, "UNIT_PATH", str(unit))
    monkeypatch.setattr(cli_mod, "LOGROTATE_PATH", str(logrotate))
    monkeypatch.setattr(cli_mod, "_require_systemd", lambda: None)
    monkeypatch.setattr(cli_mod, "_is_root", lambda: True)
    return unit, logrotate


def run_uninstall(disable_rc: int = 0, active_rc: int = 3) -> tuple[Any, list[list[str]]]:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        calls.append(cmd)
        if "is-active" in cmd:
            return subprocess.CompletedProcess(cmd, active_rc)
        return subprocess.CompletedProcess(cmd, disable_rc if "disable" in cmd else 0)

    with mock.patch.object(cli_mod.subprocess, "run", side_effect=fake_run):
        return invoke("daemon", "uninstall"), calls


def test_uninstall_sequence(uninstall_env: tuple[Path, Path]) -> None:
    unit, logrotate = uninstall_env
    unit.write_text("[Service]\n")
    result, calls = run_uninstall()
    assert result.exit_code == 0, result.output
    assert calls == [
        ["systemctl", "disable", "--now", "date-app"],
        ["rm", "-f", cli_mod.UNIT_PATH, cli_mod.LOGROTATE_PATH],
        ["systemctl", "daemon-reload"],
    ]


def test_uninstall_removes_nothing_when_disable_fails(uninstall_env: tuple[Path, Path]) -> None:
    uninstall_env[0].write_text("[Service]\n")
    result, calls = run_uninstall(disable_rc=5)
    assert result.exit_code == 5
    assert "[!]" in result.output
    assert calls == [["systemctl", "disable", "--now", "date-app"]]


def test_uninstall_without_unit_removes_only_leftover_logrotate(
    uninstall_env: tuple[Path, Path],
) -> None:
    uninstall_env[1].write_text("leftover\n")
    result, calls = run_uninstall()
    assert result.exit_code == 0, result.output
    assert calls == [
        ["systemctl", "is-active", "--quiet", "date-app"],
        ["rm", "-f", cli_mod.LOGROTATE_PATH],
    ]


def test_uninstall_without_unit_but_active_service_removes_nothing(
    uninstall_env: tuple[Path, Path],
) -> None:
    uninstall_env[1].write_text("leftover\n")
    result, calls = run_uninstall(active_rc=0)
    assert result.exit_code == 1
    assert "[!] date-app is still active but its unit file is gone" in result.output
    assert "sudo systemctl stop date-app" in result.output
    assert calls == [["systemctl", "is-active", "--quiet", "date-app"]]  # no sudo, no rm


def test_uninstall_when_nothing_is_installed_does_nothing(
    uninstall_env: tuple[Path, Path],
) -> None:
    result, calls = run_uninstall()
    assert result.exit_code == 0, result.output
    assert "not installed" in result.output
    assert calls == [["systemctl", "is-active", "--quiet", "date-app"]]


# -- start / stop / restart / status / logs ------------------------------------------------


@pytest.fixture
def non_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_mod, "_require_systemd", lambda: None)
    monkeypatch.setattr(cli_mod, "_is_root", lambda: False)
    monkeypatch.setattr(cli_mod.shutil, "which", lambda name: f"/usr/bin/{name}")


@pytest.mark.parametrize("action", ["start", "stop", "restart"])
def test_control_runs_exact_sudo_systemctl_argv(non_root: None, action: str) -> None:
    with mock.patch.object(
        cli_mod.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)
    ) as run:
        result = invoke("daemon", action)
    assert result.exit_code == 0, result.output
    run.assert_called_once_with(["sudo", "systemctl", action, "date-app"])


def test_control_as_root_has_no_sudo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_mod, "_require_systemd", lambda: None)
    monkeypatch.setattr(cli_mod, "_is_root", lambda: True)
    with mock.patch.object(
        cli_mod.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)
    ) as run:
        invoke("daemon", "restart")
    run.assert_called_once_with(["systemctl", "restart", "date-app"])


def test_status_never_uses_sudo(non_root: None) -> None:
    with mock.patch.object(
        cli_mod.subprocess, "run", return_value=subprocess.CompletedProcess([], 3)
    ) as run:
        result = invoke("daemon", "status")
    assert result.exit_code == 3
    run.assert_called_once_with(["systemctl", "status", "--no-pager", "date-app"])


def test_logs_tails_repo_log_without_sudo(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    log = tmp_path / "date-app.log"
    log.write_text("hello\n")
    monkeypatch.setattr(cli_mod, "LOG_FILE", str(log))
    with mock.patch.object(
        cli_mod.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)
    ) as run:
        result = invoke("daemon", "logs", "-f", "-n", "5")
    assert result.exit_code == 0, result.output
    run.assert_called_once_with(["tail", "-F", "-n", "5", str(log)])


def test_logs_without_file_is_an_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli_mod, "LOG_FILE", str(tmp_path / "missing.log"))
    with mock.patch.object(cli_mod.subprocess, "run") as run:
        result = invoke("daemon", "logs")
    assert result.exit_code == 1
    run.assert_not_called()


# -- update --------------------------------------------------------------------------------


class UpdateHarness:
    """Records every subprocess call and every build step, in order, in `events`."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        self.events: list[Any] = []
        self.git: dict[str, tuple[int, str]] = {}
        self.healthy = True
        unit = tmp_path / "date-app.service"
        unit.write_text("[Service]\nExecStart=/x/dateapp serve --port 8099 --no-build\n")
        log = tmp_path / "date-app.log"
        log.write_text("boot line 1\nboot line 2\n")
        monkeypatch.setattr(cli_mod, "UNIT_PATH", str(unit))
        monkeypatch.setattr(cli_mod, "LOG_FILE", str(log))
        monkeypatch.setattr(cli_mod, "_require_systemd", lambda: None)
        monkeypatch.setattr(cli_mod, "_is_root", lambda: False)
        monkeypatch.setattr(cli_mod.shutil, "which", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr(cli_mod, "_run_backup", self._step("backup"))
        monkeypatch.setattr(cli_mod, "_sync_python_deps", self._step("sync"))
        monkeypatch.setattr(cli_mod, "_run_build", self._step("build"))
        monkeypatch.setattr(cli_mod, "_run_migrate", self._step("migrate"))
        monkeypatch.setattr(cli_mod, "_wait_healthy", self._wait)

    def _step(self, name: str) -> Any:
        def step(*args: Any, **kwargs: Any) -> str:
            self.events.append(name)
            return ""

        return step

    def _wait(self, port: int, seconds: int = 30) -> bool:
        self.events.append(("health", port))
        return self.healthy

    def run(self, cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.events.append(cmd)
        if cmd[0] == "git":
            rc, out = self.git.get(cmd[1], (0, ""))
            return subprocess.CompletedProcess(cmd, rc, stdout=out, stderr="boom" if rc else "")
        return subprocess.CompletedProcess(cmd, 0)

    def invoke(self) -> Any:
        with mock.patch.object(cli_mod.subprocess, "run", side_effect=self.run):
            return invoke("daemon", "update")


@pytest.fixture
def update(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> UpdateHarness:
    return UpdateHarness(monkeypatch, tmp_path)


def test_update_dirty_tree_aborts_before_pull(update: UpdateHarness) -> None:
    update.git["status"] = (0, " M backend/app/cli.py\n")
    result = update.invoke()
    assert result.exit_code == 1
    assert "uncommitted" in result.output
    assert not any(isinstance(e, list) and e[:2] == ["git", "pull"] for e in update.events)
    assert "backup" not in update.events
    assert not ran_systemctl(update.events)


def test_update_non_fast_forward_aborts_before_backup(update: UpdateHarness) -> None:
    update.git["pull"] = (128, "")
    result = update.invoke()
    assert result.exit_code != 0
    assert "git pull --ff-only failed" in result.output
    assert not {"backup", "sync", "build", "migrate"} & {
        e for e in update.events if isinstance(e, str)
    }


def test_update_full_sequence_in_order(update: UpdateHarness) -> None:
    result = update.invoke()
    assert result.exit_code == 0, result.output
    assert update.events == [
        ["git", "status", "--porcelain", "--untracked-files=no"],
        ["git", "rev-parse", "HEAD"],
        ["git", "pull", "--ff-only"],
        ["git", "rev-parse", "HEAD"],
        "backup",
        "sync",
        "build",
        "migrate",
        ["sudo", "systemctl", "restart", "date-app"],
        ("health", 8099),
    ]
    assert "healthy" in result.output


@pytest.mark.parametrize("failing", ["_run_backup", "_run_build", "_run_migrate"])
def test_update_step_failure_stops_before_restart(
    update: UpdateHarness, monkeypatch: pytest.MonkeyPatch, failing: str
) -> None:
    def boom(*args: Any, **kwargs: Any) -> None:
        raise SystemExit(2)

    monkeypatch.setattr(cli_mod, failing, boom)
    result = update.invoke()
    assert result.exit_code == 2
    assert not ran_systemctl(update.events)


def test_update_unhealthy_reports_log_tail_and_fails(update: UpdateHarness) -> None:
    update.healthy = False
    result = update.invoke()
    assert result.exit_code == 1
    assert "[!] date-app restarted but" in result.output
    assert "boot line 2" in result.output


def test_update_without_unit_refuses_before_changing_anything(
    update: UpdateHarness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cli_mod, "UNIT_PATH", str(tmp_path / "nope.service"))
    result = update.invoke()
    assert result.exit_code == 1
    assert "[!]" in result.output
    assert "dateapp daemon install" in result.output
    assert update.events == []


def test_update_as_root_restarts_without_sudo(
    update: UpdateHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli_mod, "_is_root", lambda: True)
    update.invoke()
    assert ["systemctl", "restart", "date-app"] in update.events


# -- health polling ------------------------------------------------------------------------


class FakeClock:
    """A monotonic clock that only moves when something blocks (a request or a sleep)."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.timeouts: list[float] = []
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def patch_clock(clock: FakeClock) -> Any:
    return mock.patch.multiple(cli_mod.time, monotonic=clock.monotonic, sleep=clock.sleep)


def test_wait_healthy_returns_true_after_retries() -> None:
    clock = FakeClock()
    answers = iter([None, (503, {}), (200, {"status": "ok"})])

    def fake_get(url: str, timeout: float = 6.0) -> Any:
        clock.timeouts.append(timeout)
        return next(answers)

    with patch_clock(clock), mock.patch.object(cli_mod, "_get_json", side_effect=fake_get):
        assert cli_mod._wait_healthy(8090, seconds=30) is True
    assert clock.sleeps == [1.0, 1.0]
    assert clock.timeouts == [2.0, 2.0, 2.0]


def test_wait_healthy_never_exceeds_its_deadline_even_if_requests_hang() -> None:
    clock = FakeClock()
    start = clock.now

    def hanging_get(url: str, timeout: float = 6.0) -> None:
        clock.timeouts.append(timeout)
        clock.now += timeout  # the request blocks for its whole timeout, then fails
        return None

    with patch_clock(clock), mock.patch.object(cli_mod, "_get_json", side_effect=hanging_get):
        assert cli_mod._wait_healthy(8090, seconds=30) is False
    assert clock.now - start <= 30.0 + 1e-9
    assert max(clock.timeouts) <= 2.0
    assert max(clock.sleeps) <= 1.0


def test_wait_healthy_caps_the_last_request_at_the_time_left() -> None:
    clock = FakeClock()

    def hanging_get(url: str, timeout: float = 6.0) -> None:
        clock.timeouts.append(timeout)
        clock.now += timeout
        return None

    with patch_clock(clock), mock.patch.object(cli_mod, "_get_json", side_effect=hanging_get):
        assert cli_mod._wait_healthy(8090, seconds=1) is False
    assert clock.timeouts == [1.0]
