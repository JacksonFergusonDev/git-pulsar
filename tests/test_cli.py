"""Tests for the Command Line Interface (CLI) module."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from git_pulsar import cli
from git_pulsar.config import Config


def test_show_status_displays_timestamps(
    tmp_path: Path, capsys: pytest.CaptureFixture, mocker: MagicMock
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
    assert isinstance(args[0], cli.GitRepo)


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
