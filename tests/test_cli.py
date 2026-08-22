"""Tests for the Command Line Interface (CLI) module."""

import datetime
import os
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from git_pulsar import cli
from git_pulsar.config import Config
from git_pulsar.git_wrapper import GitRepo


def test_show_status_displays_timestamps(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], mocker: MagicMock
) -> None:
    """Verifies that `show_status` displays both commit and push timestamps.

    Args:
        tmp_path (Path): Pytest fixture for a temporary directory.
        capsys (pytest.CaptureFixture): Pytest fixture for capturing stdout.
        mocker (MagicMock): Pytest fixture for mocking.
    """
    (tmp_path / ".git").mkdir()
    mocker.patch.object(Path, "cwd", return_value=tmp_path)

    # Mock Registry to include the current path
    registry_path = tmp_path / "registry"
    registry_path.write_text(str(tmp_path))
    mocker.patch("git_pulsar.system.REGISTRY_FILE", registry_path)

    # Mock Config loading
    mocker.patch("git_pulsar.config.Config.load", return_value=Config())

    # Mock New Observability Integrations
    mocker.patch("git_pulsar.cli.ops.has_large_files", return_value=False)
    mocker.patch("git_pulsar.cli.ops.get_drift_state", return_value=(0.0, 0))

    # Mock system strategy for telemetry
    mock_strat = mocker.patch("git_pulsar.cli.system.get_system").return_value
    mock_strat.get_battery.return_value = (100, True)

    # Mock GitRepo
    mock_cls = mocker.patch("git_pulsar.cli.GitRepo")
    repo = mock_cls.return_value
    repo.status_porcelain.return_value = []

    # Mock timestamp return values from git log
    # First call: Commit time, Second call: Push time
    repo._run.side_effect = ["1600000000", "1600000000"]

    cli.show_status()

    captured = capsys.readouterr()
    assert "Last Commit:" in captured.out
    assert "Last Push:" in captured.out
    assert "Active" in captured.out


@pytest.mark.parametrize(
    ("commit_interval", "time_since_backup", "expected_warning"),
    [
        (300, 1000, True),  # 5 min interval, 16 mins stale -> Warn
        (
            600,
            1000,
            False,
        ),  # 10 min interval, 16 mins stale -> No Warn (threshold 1200)
        (3600, 8000, True),  # 1 hr interval, 2.2 hrs stale -> Warn
    ],
)
def test_check_repo_health_dynamic_threshold(
    tmp_path: Path,
    mocker: MagicMock,
    commit_interval: int,
    time_since_backup: int,
    expected_warning: bool,
) -> None:
    """Verifies that the stalled repository warning scales with the configured interval."""
    conf = Config()
    conf.daemon.commit_interval = commit_interval

    mock_repo = mocker.patch("git_pulsar.cli.GitRepo").return_value
    mock_repo.status_porcelain.return_value = ["M file.txt"]
    mocker.patch(
        "git_pulsar.cli._get_ref", return_value="refs/heads/wip/pulsar/mac/main"
    )

    # Simulate time drift
    current_time = 100000
    mocker.patch("time.time", return_value=current_time)

    # Return a backup timestamp that is exactly `time_since_backup` seconds ago
    mock_repo._run.return_value = str(current_time - time_since_backup)

    result = cli._check_repo_health(tmp_path, conf)

    if expected_warning:
        assert result is not None
        assert "Stalled: Changes pending" in result
    else:
        assert result is None


@pytest.mark.parametrize(
    ("battery_pct", "is_plugged", "expected_text"),
    [
        (100, True, "AC (Unrestricted)"),
        (5, False, "Critical 5% (All Backups Suspended)"),
        (15, False, "Eco-Mode 15% (Pushes Suspended)"),
        (50, False, "Battery 50% (Normal)"),
    ],
)
def test_show_status_power_telemetry(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    mocker: MagicMock,
    battery_pct: int,
    is_plugged: bool,
    expected_text: str,
) -> None:
    """Verifies that the correct telemetry state is rendered based on battery levels."""
    mocker.patch.object(Path, "exists", return_value=False)  # Skip repo status
    mocker.patch("git_pulsar.cli.PID_FILE", mocker.MagicMock(exists=lambda: False))
    mocker.patch("git_pulsar.service.is_service_enabled", return_value=False)

    conf = Config()
    conf.daemon.min_battery_percent = 10
    conf.daemon.eco_mode_percent = 20
    mocker.patch("git_pulsar.config.Config.load", return_value=conf)

    mock_strat = mocker.patch("git_pulsar.cli.system.get_system").return_value
    mock_strat.get_battery.return_value = (battery_pct, is_plugged)

    cli.show_status()
    captured = capsys.readouterr()

    assert expected_text in captured.out


def test_show_status_health_warning_large_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], mocker: MagicMock
) -> None:
    """Verifies that large file pipeline blockers surface in the status dashboard."""
    (tmp_path / ".git").mkdir()
    mocker.patch.object(Path, "cwd", return_value=tmp_path)

    # Pretend it is registered
    mocker.patch("git_pulsar.system.get_registered_repos", return_value=[tmp_path])

    # Trigger large file warning
    mocker.patch("git_pulsar.cli.ops.has_large_files", return_value=True)
    mocker.patch("git_pulsar.cli.ops.get_drift_state", return_value=(0.0, 0))

    mock_repo = mocker.patch("git_pulsar.cli.GitRepo").return_value
    mock_repo.status_porcelain.return_value = ["M big_file.bin"]
    mock_repo._run.return_value = "1600000000"

    cli.show_status()
    captured = capsys.readouterr()

    assert "⚠ WARNING:" in captured.out
    assert "Daemon stalled" in captured.out
    assert "File >100MB detected" in captured.out


def test_show_status_drift_warning(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], mocker: MagicMock
) -> None:
    """Verifies that roaming radar divergence surfaces in the status dashboard."""
    (tmp_path / ".git").mkdir()
    mocker.patch.object(Path, "cwd", return_value=tmp_path)
    mocker.patch("git_pulsar.system.get_registered_repos", return_value=[tmp_path])
    mocker.patch("git_pulsar.cli.ops.has_large_files", return_value=False)

    mock_repo = mocker.patch("git_pulsar.cli.GitRepo").return_value
    mock_repo.status_porcelain.return_value = []

    current_time = time.time()

    # Local commit was 1 hour ago
    local_ts = str(int(current_time - 3600))
    mock_repo._run.side_effect = [local_ts, local_ts]

    # Warned timestamp was 10 mins ago (Newer than local commit)
    warned_ts = int(current_time - 600)
    mocker.patch(
        "git_pulsar.cli.ops.get_drift_state", return_value=(current_time, warned_ts)
    )

    cli.show_status()
    captured = capsys.readouterr()

    assert "Session Drift" in captured.out
    assert "⚠ A remote machine pushed a newer session" in captured.out
    assert "Run 'git pulsar sync'" in captured.out


def test_config_command_opens_editor(mocker: MagicMock) -> None:
    """Verifies that the `config` command attempts to open the editor.

    Args:
        mocker (MagicMock): Pytest fixture for mocking.
    """
    # Mock the editor environment variable
    mocker.patch.dict("os.environ", {"EDITOR": "nano"})

    # Mock subprocess to avoid actually running nano
    mock_run = mocker.patch("subprocess.run")

    # Mock the CONFIG_FILE object entirely to support .exists() and str()
    mock_config_path = mocker.MagicMock(spec=Path)
    mock_config_path.exists.return_value = True
    mock_config_path.__str__.return_value = "/mock/config.toml"

    mocker.patch("git_pulsar.cli.CONFIG_FILE", mock_config_path)

    cli.open_config()

    # Verify that the correct command was executed
    args = mock_run.call_args[0][0]
    assert args[0] == "nano"
    assert "/mock/config.toml" in str(args[1])


def test_main_runs_daemon_command(mocker: MagicMock) -> None:
    """Verifies that the `now` command invokes the daemon main loop.

    Args:
        mocker (MagicMock): Pytest fixture for mocking.
    """
    mocker.patch("sys.argv", ["git-pulsar", "now"])
    mock_daemon = mocker.patch("git_pulsar.daemon.main")

    cli.main()

    mock_daemon.assert_called_with(interactive=True)


def test_setup_repo_triggers_identity_config(tmp_path: Path, mocker: MagicMock) -> None:
    """Verifies that setting up a repo triggers identity configuration."""
    (tmp_path / ".git").mkdir()
    mocker.patch.object(Path, "cwd", return_value=tmp_path)

    # Use a fake registry so we don't pollute the real user's registry
    mock_registry = tmp_path / "registry"

    mocker.patch("git_pulsar.constants.REGISTRY_FILE", mock_registry)

    # Mock system.configure_identity
    mock_config_id = mocker.patch("git_pulsar.system.configure_identity")

    # Pass the mock registry explicitly
    cli.setup_repo(registry_path=mock_registry)

    # Assert it was called with a GitRepo instance
    mock_config_id.assert_called_once()
    args = mock_config_id.call_args[0]
    assert isinstance(args[0], GitRepo)


def test_setup_repo_exact_gitignore_matching(tmp_path: Path, mocker: MagicMock) -> None:
    """Verifies that setup_repo checks .gitignore lines exactly rather than substrings."""
    (tmp_path / ".git").mkdir()
    gitignore = tmp_path / ".gitignore"
    # Write a pattern that contains '*.log' as a substring
    gitignore.write_text("my_special_*.log\n")

    mocker.patch.object(Path, "cwd", return_value=tmp_path)
    mock_registry = tmp_path / "registry"
    mocker.patch("git_pulsar.system.configure_identity")

    cli.setup_repo(registry_path=mock_registry)

    content = gitignore.read_text()
    assert "*.log" in content.splitlines()


def test_unregister_repo_removes_entry(tmp_path: Path, mocker: MagicMock) -> None:
    """Verifies that unregister_repo removes the current directory from the registry file."""
    mocker.patch.object(Path, "cwd", return_value=tmp_path)
    mock_registry = tmp_path / "registry"
    mock_registry.write_text(f"{tmp_path}\n/other/path\n")
    mocker.patch("git_pulsar.cli.REGISTRY_FILE", mock_registry)

    cli.unregister_repo()

    lines = [
        line.strip() for line in mock_registry.read_text().splitlines() if line.strip()
    ]
    assert str(tmp_path) not in lines
    assert "/other/path" in lines


def test_check_systemd_linger_non_linux(mocker: MagicMock) -> None:
    """Verifies that the linger check safely ignores non-Linux platforms.

    Args:
        mocker (MagicMock): Pytest fixture for mocking.
    """
    mocker.patch("sys.platform", "darwin")
    result = cli._check_systemd_linger()
    assert result is None


def test_check_systemd_linger_no_user(mocker: MagicMock) -> None:
    """Verifies that the linger check aborts if the USER env var is missing.

    Args:
        mocker (MagicMock): Pytest fixture for mocking.
    """
    mocker.patch("sys.platform", "linux")
    mocker.patch.dict("os.environ", clear=True)

    result = cli._check_systemd_linger()
    assert result is None


def test_check_systemd_linger_enabled(mocker: MagicMock) -> None:
    """Verifies that no warning is issued if Linger=yes is detected.

    Args:
        mocker (MagicMock): Pytest fixture for mocking.
    """
    mocker.patch("sys.platform", "linux")
    mocker.patch.dict("os.environ", {"USER": "astro_dev"})

    mock_run = mocker.patch("subprocess.run")
    mock_run.return_value = mocker.MagicMock(stdout="Linger=yes\n")

    result = cli._check_systemd_linger()

    mock_run.assert_called_once_with(
        ["loginctl", "show-user", "astro_dev", "-p", "Linger"],
        capture_output=True,
        text=True,
        timeout=2,
    )
    assert result is None


def test_check_systemd_linger_disabled(mocker: MagicMock) -> None:
    """Verifies that a warning is returned if Linger=no is detected.

    Args:
        mocker (MagicMock): Pytest fixture for mocking.
    """
    mocker.patch("sys.platform", "linux")
    mocker.patch.dict("os.environ", {"USER": "astro_dev"})

    mock_run = mocker.patch("subprocess.run")
    mock_run.return_value = mocker.MagicMock(stdout="Linger=no\n")

    result = cli._check_systemd_linger()
    assert result is not None
    assert "disabled" in result
    assert "loginctl enable-linger" in result


def test_check_systemd_linger_exception(mocker: MagicMock) -> None:
    """Verifies that the linger check fails gracefully on subprocess errors.

    Args:
        mocker (MagicMock): Pytest fixture for mocking.
    """
    mocker.patch("sys.platform", "linux")
    mocker.patch.dict("os.environ", {"USER": "astro_dev"})

    mock_run = mocker.patch("subprocess.run")
    mock_run.side_effect = FileNotFoundError("loginctl not found")

    result = cli._check_systemd_linger()
    assert result is None


def test_check_git_hooks_no_dir(tmp_path: Path) -> None:
    """Verifies that the hook check passes silently if no hooks directory exists.

    Args:
        tmp_path (Path): Pytest fixture for a temporary directory.
    """
    (tmp_path / ".git").mkdir()
    warnings = cli._check_git_hooks(tmp_path)
    assert len(warnings) == 0


def test_check_git_hooks_non_executable(tmp_path: Path, mocker: MagicMock) -> None:
    """Verifies that non-executable hooks are safely ignored.

    Args:
        tmp_path (Path): Pytest fixture for a temporary directory.
        mocker (MagicMock): Pytest fixture for mocking.
    """
    hooks_dir = tmp_path / ".git" / "hooks"
    hooks_dir.mkdir(parents=True)
    hook_file = hooks_dir / "pre-push"
    hook_file.write_text("exit 1")

    # Mock os.access to simulate a file lacking the +x bit
    mocker.patch("os.access", return_value=False)

    warnings = cli._check_git_hooks(tmp_path)
    assert len(warnings) == 0


def test_check_git_hooks_with_bypass(tmp_path: Path, mocker: MagicMock) -> None:
    """Verifies that executable hooks containing the 'pulsar' bypass keyword are ignored.

    Args:
        tmp_path (Path): Pytest fixture for a temporary directory.
        mocker (MagicMock): Pytest fixture for mocking.
    """
    hooks_dir = tmp_path / ".git" / "hooks"
    hooks_dir.mkdir(parents=True)
    hook_file = hooks_dir / "pre-commit"

    # Write a hook that includes the 'pulsar' keyword
    script_content = "#!/bin/sh\nif [[ $1 == *pulsar* ]]; then exit 0; fi\nmake test"
    hook_file.write_text(script_content)

    # Force os.access to treat the file as executable
    mocker.patch("os.access", return_value=True)

    warnings = cli._check_git_hooks(tmp_path)
    assert len(warnings) == 0


def test_check_git_hooks_strict_blocking(tmp_path: Path, mocker: MagicMock) -> None:
    """Verifies that strict, executable hooks trigger a warning.

    Args:
        tmp_path (Path): Pytest fixture for a temporary directory.
        mocker (MagicMock): Pytest fixture for mocking.
    """
    hooks_dir = tmp_path / ".git" / "hooks"
    hooks_dir.mkdir(parents=True)

    # Create two blocking hooks
    for hook in ["pre-commit", "pre-push"]:
        hook_file = hooks_dir / hook
        hook_file.write_text(f"#!/bin/sh\necho 'Running strict {hook} linters'")

    mocker.patch("os.access", return_value=True)

    warnings = cli._check_git_hooks(tmp_path)

    # We should get a warning for each strict hook
    assert len(warnings) == 2
    assert "Strict 'pre-commit' hook detected" in warnings[0]
    assert "Strict 'pre-push' hook detected" in warnings[1]


def test_run_doctor_transient_error_suppression(
    tmp_path: Path, mocker: MagicMock
) -> None:
    """Verifies that transient log errors are suppressed when the system state is healthy.

    Args:
        tmp_path (Path): Pytest fixture for a temporary directory.
        mocker (MagicMock): Pytest fixture for mocking.
    """
    # 1. Mock Registry & File System using tmp_path
    mock_repo = tmp_path / "mock_repo"
    mock_repo.mkdir()

    mock_registry = tmp_path / "registry"
    mock_registry.write_text(f"{mock_repo}\n")

    mocker.patch("git_pulsar.system.get_registered_repos", return_value=[mock_repo])
    mocker.patch("git_pulsar.cli.REGISTRY_FILE", mock_registry)

    # 2. Mock environment sub-checks
    mocker.patch("git_pulsar.service.is_service_enabled", return_value=True)
    mocker.patch("git_pulsar.cli._check_systemd_linger", return_value=None)
    mocker.patch(
        "subprocess.run",
        return_value=mocker.MagicMock(stderr="successfully authenticated"),
    )
    mocker.patch(
        "git_pulsar.ops.get_remote_drift_state", return_value=(False, 0, "", "")
    )
    mocker.patch("git_pulsar.cli._check_git_hooks", return_value=[])

    # 3. Mock State & Event Correlation inputs
    # State is healthy (None returned from health check)
    mocker.patch("git_pulsar.cli._check_repo_health", return_value=None)

    mock_conf = mocker.MagicMock()
    mock_conf.daemon.push_interval = 3600
    mocker.patch("git_pulsar.config.Config.load", return_value=mock_conf)

    # Events exist but state is healthy -> Transient
    mocker.patch(
        "git_pulsar.cli._analyze_logs", return_value=["Transient connection drop"]
    )

    # 4. Mock the console to capture output formatting
    mock_console = mocker.patch("git_pulsar.cli.console")

    cli.run_doctor()

    # 5. Assert correlation correctly identified transient anomaly
    output = " ".join(
        [call.args[0] for call in mock_console.print.call_args_list if call.args]
    )
    assert "transient error(s) logged" in output
    assert "automatically recovered" in output


def test_run_doctor_active_error_correlation(tmp_path: Path, mocker: MagicMock) -> None:
    """Verifies that log errors are displayed loudly when the system state is failing.

    Args:
        tmp_path (Path): Pytest fixture for a temporary directory.
        mocker (MagicMock): Pytest fixture for mocking.
    """
    # 1. Mock Registry & File System using tmp_path
    mock_repo = tmp_path / "mock_repo"
    mock_repo.mkdir()

    mock_registry = tmp_path / "registry"
    mock_registry.write_text(f"{mock_repo}\n")

    mocker.patch("git_pulsar.system.get_registered_repos", return_value=[mock_repo])
    mocker.patch("git_pulsar.cli.REGISTRY_FILE", mock_registry)

    # 2. Mock environment sub-checks
    mocker.patch("git_pulsar.service.is_service_enabled", return_value=True)
    mocker.patch("git_pulsar.cli._check_systemd_linger", return_value=None)
    mocker.patch(
        "subprocess.run",
        return_value=mocker.MagicMock(stderr="successfully authenticated"),
    )
    mocker.patch(
        "git_pulsar.ops.get_remote_drift_state", return_value=(False, 0, "", "")
    )
    mocker.patch("git_pulsar.cli._check_git_hooks", return_value=[])

    # 3. State is UNHEALTHY
    mocker.patch(
        "git_pulsar.cli._check_repo_health",
        return_value="Stalled: Changes pending > 2 hours.",
    )

    mock_conf = mocker.MagicMock()
    mock_conf.daemon.push_interval = 3600
    mocker.patch("git_pulsar.config.Config.load", return_value=mock_conf)

    # Events exist and correlate with Unhealthy state
    mocker.patch(
        "git_pulsar.cli._analyze_logs", return_value=["Connection refused", "Timeout"]
    )

    mock_console = mocker.patch("git_pulsar.cli.console")

    cli.run_doctor()

    # Assert correlation correctly escalated the errors
    output = " ".join(
        [call.args[0] for call in mock_console.print.call_args_list if call.args]
    )
    assert "active error(s) in the last" in output
    assert "Connection refused" in output


# Test run_doctor's interactive loop


def test_run_doctor_executes_confirmed_actions(
    tmp_path: Path, mocker: MagicMock
) -> None:
    """Verifies that the interactive loop executes the closure when confirmed."""
    mock_repo = tmp_path / "mock_repo"
    git_dir = mock_repo / ".git"
    git_dir.mkdir(parents=True)
    pause_file = git_dir / "pulsar_paused"
    pause_file.touch()

    mock_registry = tmp_path / "registry"
    mock_registry.write_text(f"{mock_repo}\n")

    mocker.patch("git_pulsar.system.get_registered_repos", return_value=[mock_repo])
    mocker.patch("git_pulsar.cli.REGISTRY_FILE", mock_registry)
    mocker.patch("git_pulsar.service.is_service_enabled", return_value=True)
    mocker.patch("git_pulsar.cli._check_systemd_linger", return_value=None)
    mocker.patch("subprocess.run")
    mocker.patch(
        "git_pulsar.ops.get_remote_drift_state", return_value=(False, 0, "", "")
    )
    mocker.patch("git_pulsar.cli._check_repo_health", return_value=None)
    mocker.patch("git_pulsar.cli._check_git_hooks", return_value=[])
    mocker.patch("git_pulsar.cli._analyze_logs", return_value=[])

    mocker.patch("git_pulsar.cli.Confirm.ask", return_value=True)
    mock_console = mocker.patch("git_pulsar.cli.console")

    cli.run_doctor()

    assert not pause_file.exists()

    output = " ".join(
        [call.args[0] for call in mock_console.print.call_args_list if call.args]
    )
    assert "✔ Resolved:" in output
    assert "Resume backups for mock_repo" in output


def test_run_doctor_skips_declined_actions(tmp_path: Path, mocker: MagicMock) -> None:
    """Verifies that the interactive loop bypasses the closure when declined."""
    mock_repo = tmp_path / "mock_repo"
    git_dir = mock_repo / ".git"
    git_dir.mkdir(parents=True)
    pause_file = git_dir / "pulsar_paused"
    pause_file.touch()

    mock_registry = tmp_path / "registry"
    mock_registry.write_text(f"{mock_repo}\n")

    mocker.patch("git_pulsar.system.get_registered_repos", return_value=[mock_repo])
    mocker.patch("git_pulsar.cli.REGISTRY_FILE", mock_registry)
    mocker.patch("git_pulsar.service.is_service_enabled", return_value=True)
    mocker.patch("git_pulsar.cli._check_systemd_linger", return_value=None)
    mocker.patch("subprocess.run")
    mocker.patch(
        "git_pulsar.ops.get_remote_drift_state", return_value=(False, 0, "", "")
    )
    mocker.patch("git_pulsar.cli._check_repo_health", return_value=None)
    mocker.patch("git_pulsar.cli._check_git_hooks", return_value=[])
    mocker.patch("git_pulsar.cli._analyze_logs", return_value=[])

    mocker.patch("git_pulsar.cli.Confirm.ask", return_value=False)
    mock_console = mocker.patch("git_pulsar.cli.console")

    cli.run_doctor()

    assert pause_file.exists()

    output = " ".join(
        [call.args[0] for call in mock_console.print.call_args_list if call.args]
    )
    assert "[dim]Skipped.[/dim]" in output


def test_run_doctor_fixes_stale_index_lock(tmp_path: Path, mocker: MagicMock) -> None:
    """Verifies that locks older than 2 hours prompt a resolution action."""
    mock_repo = tmp_path / "mock_repo"
    git_dir = mock_repo / ".git"
    git_dir.mkdir(parents=True)
    lock_file = git_dir / "index.lock"
    lock_file.touch()

    old_time = time.time() - (3 * 3600)
    os.utime(lock_file, (old_time, old_time))

    mock_registry = tmp_path / "registry"
    mock_registry.write_text(f"{mock_repo}\n")

    mocker.patch("git_pulsar.system.get_registered_repos", return_value=[mock_repo])
    mocker.patch("git_pulsar.cli.REGISTRY_FILE", mock_registry)
    mocker.patch("git_pulsar.service.is_service_enabled", return_value=True)
    mocker.patch("git_pulsar.cli._check_systemd_linger", return_value=None)
    mocker.patch("subprocess.run")
    mocker.patch(
        "git_pulsar.ops.get_remote_drift_state", return_value=(False, 0, "", "")
    )
    mocker.patch("git_pulsar.cli._check_repo_health", return_value=None)
    mocker.patch("git_pulsar.cli._check_git_hooks", return_value=[])
    mocker.patch("git_pulsar.cli._analyze_logs", return_value=[])

    mocker.patch("git_pulsar.cli.Confirm.ask", return_value=True)

    cli.run_doctor()

    assert not lock_file.exists()


def test_run_doctor_ignores_fresh_index_lock(tmp_path: Path, mocker: MagicMock) -> None:
    """Verifies that fresh index locks do not trigger a resolution prompt."""
    mock_repo = tmp_path / "mock_repo"
    git_dir = mock_repo / ".git"
    git_dir.mkdir(parents=True)
    lock_file = git_dir / "index.lock"
    lock_file.touch()

    # Manipulate file mtime to be 5 minutes old
    recent_time = time.time() - 300
    os.utime(lock_file, (recent_time, recent_time))

    mock_registry = tmp_path / "registry"
    mock_registry.write_text(f"{mock_repo}\n")

    mocker.patch("git_pulsar.system.get_registered_repos", return_value=[mock_repo])
    mocker.patch("git_pulsar.cli.REGISTRY_FILE", mock_registry)
    mocker.patch("git_pulsar.service.is_service_enabled", return_value=True)
    mocker.patch("git_pulsar.cli._check_systemd_linger", return_value=None)
    mocker.patch("subprocess.run")
    mocker.patch(
        "git_pulsar.ops.get_remote_drift_state", return_value=(False, 0, "", "")
    )
    mocker.patch("git_pulsar.cli._check_repo_health", return_value=None)
    mocker.patch("git_pulsar.cli._check_git_hooks", return_value=[])
    mocker.patch("git_pulsar.cli._analyze_logs", return_value=[])

    mock_confirm = mocker.patch("git_pulsar.cli.Confirm.ask", return_value=True)

    cli.run_doctor()

    # Lock file should still exist, and Confirm.ask should not have been called
    assert lock_file.exists()
    mock_confirm.assert_not_called()


def test_run_doctor_cleans_ghost_registry(tmp_path: Path, mocker: MagicMock) -> None:
    """Verifies the registry cleanup action drops missing paths and preserves valid ones."""
    valid_repo = tmp_path / "valid_repo"
    valid_repo.mkdir()
    missing_repo = tmp_path / "missing_repo"

    registry_path = tmp_path / "registry"
    registry_path.write_text(f"{valid_repo}\n{missing_repo}\n")

    mocker.patch(
        "git_pulsar.system.get_registered_repos",
        return_value=[valid_repo, missing_repo],
    )
    mocker.patch("git_pulsar.cli.REGISTRY_FILE", registry_path)
    mocker.patch("git_pulsar.service.is_service_enabled", return_value=True)
    mocker.patch("git_pulsar.cli._check_systemd_linger", return_value=None)
    mocker.patch("subprocess.run")
    mocker.patch(
        "git_pulsar.ops.get_remote_drift_state", return_value=(False, 0, "", "")
    )
    mocker.patch("git_pulsar.cli._check_repo_health", return_value=None)
    mocker.patch("git_pulsar.cli._check_git_hooks", return_value=[])
    mocker.patch("git_pulsar.cli._analyze_logs", return_value=[])

    mocker.patch("git_pulsar.cli.Confirm.ask", return_value=True)

    cli.run_doctor()

    # Verify registry content
    registry_data = registry_path.read_text().splitlines()
    assert str(valid_repo) in registry_data
    assert str(missing_repo) not in registry_data


def test_run_doctor_triggers_sync_on_drift(tmp_path: Path, mocker: MagicMock) -> None:
    """Verifies that detected session drift queues the sync_session closure."""
    mock_repo = tmp_path / "mock_repo"
    (mock_repo / ".git").mkdir(parents=True)

    mocker.patch.object(Path, "cwd", return_value=mock_repo)
    mocker.patch("git_pulsar.system.get_registered_repos", return_value=[mock_repo])
    mocker.patch("git_pulsar.cli.REGISTRY_FILE", tmp_path / "registry")
    mocker.patch("git_pulsar.service.is_service_enabled", return_value=True)
    mocker.patch("subprocess.run")

    # Mock drift detection
    mocker.patch(
        "git_pulsar.ops.get_remote_drift_state",
        return_value=(True, 9999, "remote_mac", "Drift detected!"),
    )

    mock_sync = mocker.patch("git_pulsar.ops.sync_session")
    mocker.patch("git_pulsar.cli.Confirm.ask", return_value=True)

    cli.run_doctor()

    mock_sync.assert_called_once()


def test_run_doctor_outputs_hook_bypass_snippet(
    tmp_path: Path, mocker: MagicMock
) -> None:
    """Verifies that strict hooks output the exact shell snippet needed to bypass them."""
    mock_repo = tmp_path / "mock_repo"
    mock_repo.mkdir()

    mock_registry = tmp_path / "registry"
    mock_registry.write_text(f"{mock_repo}\n")

    mocker.patch("git_pulsar.system.get_registered_repos", return_value=[mock_repo])
    mocker.patch("git_pulsar.cli.REGISTRY_FILE", mock_registry)
    mocker.patch("git_pulsar.service.is_service_enabled", return_value=True)
    mocker.patch("git_pulsar.cli._check_systemd_linger", return_value=None)
    mocker.patch("subprocess.run")
    mocker.patch(
        "git_pulsar.ops.get_remote_drift_state", return_value=(False, 0, "", "")
    )
    mocker.patch("git_pulsar.cli._check_repo_health", return_value=None)
    mocker.patch("git_pulsar.cli._analyze_logs", return_value=[])
    mocker.patch("git_pulsar.cli.Confirm.ask", return_value=True)

    expected_snippet = 'if [[ $GIT_REFLOG_ACTION == *"wip/pulsar"* ]]; then exit 0; fi'
    mocker.patch(
        "git_pulsar.cli._check_git_hooks",
        return_value=[f"Strict hook.\nAction required: Append...\n{expected_snippet}"],
    )

    mock_console = mocker.patch("git_pulsar.cli.console")

    cli.run_doctor()

    output = " ".join(
        [call.args[0] for call in mock_console.print.call_args_list if call.args]
    )
    assert expected_snippet in output


def test_run_doctor_outputs_large_file_action(
    tmp_path: Path, mocker: MagicMock
) -> None:
    """Verifies that large files output clear instructions on how to ignore them."""
    mock_repo = tmp_path / "mock_repo"
    mock_repo.mkdir()

    mock_registry = tmp_path / "registry"
    mock_registry.write_text(f"{mock_repo}\n")

    mocker.patch("git_pulsar.system.get_registered_repos", return_value=[mock_repo])
    mocker.patch("git_pulsar.cli.REGISTRY_FILE", mock_registry)
    mocker.patch("git_pulsar.service.is_service_enabled", return_value=True)
    mocker.patch("git_pulsar.cli._check_systemd_linger", return_value=None)
    mocker.patch("subprocess.run")
    mocker.patch(
        "git_pulsar.ops.get_remote_drift_state", return_value=(False, 0, "", "")
    )
    mocker.patch("git_pulsar.cli._check_repo_health", return_value=None)
    mocker.patch("git_pulsar.cli._check_git_hooks", return_value=[])
    mocker.patch("git_pulsar.cli._analyze_logs", return_value=[])
    mocker.patch("git_pulsar.cli.Confirm.ask", return_value=True)

    mocker.patch("git_pulsar.ops.has_large_files", return_value=True)

    mock_console = mocker.patch("git_pulsar.cli.console")

    cli.run_doctor()

    output = " ".join(
        [call.args[0] for call in mock_console.print.call_args_list if call.args]
    )
    assert "File >100MB detected" in output
    assert "Untrack the file or run 'git pulsar ignore <filename>'" in output


def test_analyze_logs_parsing(tmp_path: Path, mocker: MagicMock) -> None:
    """Verifies that the log analyzer correctly parses timestamps and filters by age."""
    log_file = tmp_path / "daemon.log"
    mocker.patch("git_pulsar.cli.LOG_FILE", log_file)

    now = datetime.datetime.now()
    stale_time = now - datetime.timedelta(days=2)

    # Construct a log file with mixed severity and timestamps
    lines = [
        # 1. Stale error: Parsed successfully, dropped because it exceeds the threshold
        f"[{stale_time.strftime('%Y-%m-%d %H:%M:%S')}] ERROR: Old telemetry dropped\n",
        # 2. INFO line: Ignored entirely by the keyword filter
        f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] INFO: Syncing local session\n",
        # 3. Valid recent error: Parsed and kept
        f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] ERROR: Sensor alignment failed\n",
        # 4. Error without a bracket prefix: Bypasses strptime attempt, appended directly
        "Traceback ERROR: Some unexpected failure occurred\n",
        # 5. Malformed date format: Triggers the ValueError and is passed/dropped
        "[Malformed Timestamp] ERROR: This triggers the except block\n",
        # 6. Valid recent critical: Parsed and kept
        f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] CRITICAL: Core dump initiated\n",
    ]
    log_file.write_text("".join(lines))

    errors = cli._analyze_logs(seconds=86400)

    # Assert it drops the stale error, INFO line, and malformed date, but keeps the rest
    assert len(errors) == 3
    assert "Sensor alignment failed" in errors[0]
    assert "Traceback ERROR" in errors[1]
    assert "Core dump initiated" in errors[2]


@pytest.mark.parametrize(
    ("command", "mock_target"),
    [
        (["restore", "file.py"], "git_pulsar.cli.ops.restore_file"),
        (["finalize"], "git_pulsar.cli.ops.finalize_work"),
        (["pause"], "git_pulsar.cli.set_pause_state"),
        (["resume"], "git_pulsar.cli.set_pause_state"),
        (["diff"], "git_pulsar.cli.show_diff"),
        (["list"], "git_pulsar.cli.list_repos"),
        (["log"], "git_pulsar.cli.tail_log"),
        (["sync"], "git_pulsar.cli.ops.sync_session"),
        (["remove"], "git_pulsar.cli.unregister_repo"),
        (["ignore", "*.log"], "git_pulsar.cli.add_ignore_cli"),
        (["prune", "--days", "15"], "git_pulsar.cli.ops.prune_backups"),
        (["uninstall-service"], "git_pulsar.cli.service.uninstall"),
        (["init"], "git_pulsar.cli.init_wizard"),
    ],
)
def test_cli_router_dispatches(
    mocker: MagicMock, command: list[str], mock_target: str
) -> None:
    """Verifies that the main CLI loop routes subcommands to the correct operations."""
    mocker.patch("sys.argv", ["git-pulsar", *command])

    # Intercept the target function so we don't trigger actual I/O or state mutations
    mocked_func = mocker.patch(mock_target)

    # Stub the rich console status context manager to prevent stdout noise
    mocker.patch("git_pulsar.cli.console.status")

    cli.main()
    mocked_func.assert_called()


def test_init_wizard_default(mocker: MagicMock, tmp_path: Path) -> None:
    """Verifies that the init wizard correctly writes the default pulsar.toml configuration."""
    mocker.patch.object(Path, "cwd", return_value=tmp_path)

    mock_confirm = mocker.patch(
        "git_pulsar.cli.Confirm.ask", side_effect=[True, False]
    )  # Sync enabled, don't overwrite (though we check exists)
    mock_prompt = mocker.patch("git_pulsar.cli.Prompt.ask", side_effect=["balanced"])
    mock_setup = mocker.patch("git_pulsar.cli.setup_repo")

    cli.init_wizard(advanced=False, global_config=False)

    # Assert questions were asked
    mock_confirm.assert_any_call(
        "Do you want to enable multi-machine sync?", default=False
    )
    mock_prompt.assert_any_call(
        "Select a backup intensity preset",
        choices=["paranoid", "aggressive", "balanced", "lazy", "custom"],
        default="balanced",
    )

    # Assert setup_repo called
    mock_setup.assert_called_once()

    # Verify file output
    target_file = tmp_path / "pulsar.toml"
    assert target_file.exists()
    content = target_file.read_text()
    assert "[daemon]" in content
    assert "sync_enabled = true" in content
    assert 'preset = "balanced"' in content


def test_init_wizard_advanced(mocker: MagicMock, tmp_path: Path) -> None:
    """Verifies that the init wizard prompts for advanced configurations and writes them."""
    mocker.patch.object(Path, "cwd", return_value=tmp_path)

    # Sync enabled
    mock_confirm = mocker.patch("git_pulsar.cli.Confirm.ask", return_value=True)

    # preset, commit_interval, push_interval, eco_mode_percent, min_battery_percent, large_file_threshold
    mock_prompt = mocker.patch(
        "git_pulsar.cli.Prompt.ask",
        side_effect=["custom", "600s", "3600s", "30", "15", "500MB"],
    )
    mock_setup = mocker.patch("git_pulsar.cli.setup_repo")

    cli.init_wizard(advanced=True, global_config=False)

    # Assert
    assert mock_confirm.called
    assert mock_prompt.call_count == 6
    mock_setup.assert_called_once()

    # Verify file output
    target_file = tmp_path / "pulsar.toml"
    assert target_file.exists()
    content = target_file.read_text()
    assert "[daemon]" in content
    assert "sync_enabled = true" in content
    assert "preset = " not in content  # custom preset doesn't write 'preset'
    assert 'commit_interval = "600s"' in content
    assert 'push_interval = "3600s"' in content
    assert "eco_mode_percent = 30" in content
    assert "min_battery_percent = 15" in content
    assert "[limits]" in content
    assert 'large_file_threshold = "500MB"' in content


def test_list_repos_missing_path_shows_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], mocker: MagicMock
) -> None:
    """Verifies that list_repos renders 'Missing' for paths that do not exist."""
    missing_path = tmp_path / "nonexistent"
    registry_file = tmp_path / "registry"
    registry_file.write_text(f"{missing_path}\n")

    mocker.patch("git_pulsar.cli.REGISTRY_FILE", registry_file)
    mocker.patch("git_pulsar.system.get_registered_repos", return_value=[missing_path])

    cli.list_repos()
    captured = capsys.readouterr()
    assert "Missing" in captured.out


def test_list_repos_paused_shows_paused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], mocker: MagicMock
) -> None:
    """Verifies that list_repos renders 'Paused' when pulsar_paused sentinel exists."""
    repo_path = tmp_path / "my_repo"
    git_dir = repo_path / ".git"
    git_dir.mkdir(parents=True)
    (git_dir / "pulsar_paused").touch()

    registry_file = tmp_path / "registry"
    registry_file.write_text(f"{repo_path}\n")

    mocker.patch("git_pulsar.cli.REGISTRY_FILE", registry_file)
    mocker.patch("git_pulsar.system.get_registered_repos", return_value=[repo_path])
    mock_git = mocker.patch("git_pulsar.cli.GitRepo")
    mock_git.return_value.get_last_commit_time.return_value = "10 mins ago"

    cli.list_repos()
    captured = capsys.readouterr()
    assert "Paused" in captured.out


def test_list_repos_empty_registry(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], mocker: MagicMock
) -> None:
    """Verifies that list_repos exits cleanly when no registry file exists."""
    nonexistent_registry = tmp_path / "registry"
    mocker.patch("git_pulsar.cli.REGISTRY_FILE", nonexistent_registry)

    cli.list_repos()
    captured = capsys.readouterr()
    assert "Registry is empty." in captured.out


def test_unregister_repo_removes_cwd(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], mocker: MagicMock
) -> None:
    """Verifies that unregister_repo drops the current directory from the registry."""
    repo1 = tmp_path / "repo1"
    repo2 = tmp_path / "repo2"
    repo1.mkdir()
    repo2.mkdir()

    registry_file = tmp_path / "registry"
    registry_file.write_text(f"{repo1}\n{repo2}\n")

    mocker.patch.object(Path, "cwd", return_value=repo1)
    mocker.patch("git_pulsar.cli.REGISTRY_FILE", registry_file)
    mocker.patch("git_pulsar.system.get_registered_repos", return_value=[repo1, repo2])

    cli.unregister_repo()
    captured = capsys.readouterr()
    assert "Unregistered:" in captured.out

    remaining = registry_file.read_text().splitlines()
    assert str(repo1) not in remaining
    assert str(repo2) in remaining


def test_unregister_repo_not_registered(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], mocker: MagicMock
) -> None:
    """Verifies that unregister_repo warns if the current directory is not registered."""
    repo1 = tmp_path / "repo1"
    repo2 = tmp_path / "repo2"
    registry_file = tmp_path / "registry"
    registry_file.write_text(f"{repo2}\n")

    mocker.patch.object(Path, "cwd", return_value=repo1)
    mocker.patch("git_pulsar.cli.REGISTRY_FILE", registry_file)
    mocker.patch("git_pulsar.system.get_registered_repos", return_value=[repo2])

    cli.unregister_repo()
    captured = capsys.readouterr()
    assert "Current path not registered:" in captured.out


def test_unregister_repo_empty_registry(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], mocker: MagicMock
) -> None:
    """Verifies that unregister_repo warns when the registry file does not exist."""
    mocker.patch.object(Path, "cwd", return_value=tmp_path)
    mocker.patch("git_pulsar.cli.REGISTRY_FILE", tmp_path / "nonexistent")

    cli.unregister_repo()
    captured = capsys.readouterr()
    assert "Registry is empty." in captured.out


def test_show_diff_not_git_repo(tmp_path: Path, mocker: MagicMock) -> None:
    """Verifies that show_diff exits with code 1 when executed outside a git repo."""
    mocker.patch.object(Path, "exists", return_value=False)
    mocker.patch.object(Path, "cwd", return_value=tmp_path)
    mocker.patch("git_pulsar.cli.console")

    with pytest.raises(SystemExit) as excinfo:
        cli.show_diff()

    assert excinfo.value.code == 1


def test_show_diff_with_untracked_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], mocker: MagicMock
) -> None:
    """Verifies that show_diff displays untracked files alongside diffs."""
    (tmp_path / ".git").mkdir()
    mocker.patch.object(Path, "cwd", return_value=tmp_path)

    mock_repo = mocker.patch("git_pulsar.cli.GitRepo").return_value
    mock_repo.get_untracked_files.return_value = ["new_file.py", "scratch.txt"]
    mocker.patch("git_pulsar.cli._get_ref", return_value="refs/heads/backup")

    cli.show_diff()
    captured = capsys.readouterr()
    assert "Untracked (New) Files:" in captured.out
    assert "new_file.py" in captured.out
    assert "scratch.txt" in captured.out


def test_check_repo_health_paused_returns_none(
    tmp_path: Path, mocker: MagicMock
) -> None:
    """Verifies that _check_repo_health returns None when repo is paused even with changes."""
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "pulsar_paused").touch()

    mock_repo = mocker.patch("git_pulsar.cli.GitRepo").return_value
    mock_repo.status_porcelain.return_value = ["M dirty.txt"]

    conf = Config()
    assert cli._check_repo_health(tmp_path, conf) is None


def test_check_repo_health_no_backup_found(tmp_path: Path, mocker: MagicMock) -> None:
    """Verifies that _check_repo_health reports no backup found if git log raises."""
    (tmp_path / ".git").mkdir()
    mock_repo = mocker.patch("git_pulsar.cli.GitRepo").return_value
    mock_repo.status_porcelain.return_value = ["M dirty.txt"]
    mock_repo._run.side_effect = RuntimeError("Ref not found")

    conf = Config()
    health = cli._check_repo_health(tmp_path, conf)
    assert health is not None
    assert "Has changes, but NO backup found" in health


def test_show_status_repo_not_registered(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], mocker: MagicMock
) -> None:
    """Verifies that show_status indicates when a git repository is not tracked."""
    (tmp_path / ".git").mkdir()
    mocker.patch.object(Path, "cwd", return_value=tmp_path)
    mocker.patch("git_pulsar.system.get_registered_repos", return_value=[])
    mocker.patch("git_pulsar.service.is_service_enabled", return_value=True)

    cli.show_status()
    captured = capsys.readouterr()
    assert "This repository is not tracked by Git Pulsar" in captured.out


def test_analyze_logs_missing_file(tmp_path: Path, mocker: MagicMock) -> None:
    """Verifies that _analyze_logs returns an empty list when LOG_FILE does not exist."""
    mocker.patch("git_pulsar.cli.LOG_FILE", tmp_path / "nonexistent.log")
    assert cli._analyze_logs() == []


def test_config_command_fallback_editor_linux(
    tmp_path: Path, mocker: MagicMock
) -> None:
    """Verifies that open_config defaults to nano on Linux when EDITOR is unset."""
    mocker.patch("sys.platform", "linux")
    mocker.patch.dict("os.environ", {}, clear=True)

    mock_run = mocker.patch("subprocess.run")
    mock_config_path = tmp_path / "config.toml"
    mock_config_path.touch()
    mocker.patch("git_pulsar.cli.CONFIG_FILE", mock_config_path)

    cli.open_config()
    mock_run.assert_called_once_with(["nano", str(mock_config_path)])


def test_set_pause_state_toggles(tmp_path: Path, mocker: MagicMock) -> None:
    """Verifies that set_pause_state creates and deletes the pulsar_paused sentinel."""
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    mocker.patch.object(Path, "cwd", return_value=tmp_path)

    # Pause
    cli.set_pause_state(paused=True)
    pause_file = git_dir / "pulsar_paused"
    assert pause_file.exists()

    # Resume
    cli.set_pause_state(paused=False)
    assert not pause_file.exists()


def test_set_pause_state_not_git_repo(tmp_path: Path, mocker: MagicMock) -> None:
    """Verifies that set_pause_state exits with code 1 outside a git repository."""
    mocker.patch.object(Path, "cwd", return_value=tmp_path)
    mocker.patch("git_pulsar.cli.console")

    with pytest.raises(SystemExit) as excinfo:
        cli.set_pause_state(paused=True)

    assert excinfo.value.code == 1
