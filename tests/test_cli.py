"""Tests for the Command Line Interface (CLI) module."""

from pathlib import Path
from typing import Any
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


def test_check_remote_drift_no_branch(tmp_path: Path, mocker: MagicMock) -> None:
    """Verifies that drift detection aborts if the repository is in a detached HEAD state.

    Args:
        tmp_path (Path): Pytest fixture for a temporary directory.
        mocker (MagicMock): Pytest fixture for mocking.
    """
    mock_cls = mocker.patch("git_pulsar.cli.GitRepo")
    repo = mock_cls.return_value
    repo.current_branch.return_value = ""

    result = cli._check_remote_drift(tmp_path)
    assert result is None


def test_check_remote_drift_fetch_fails(tmp_path: Path, mocker: MagicMock) -> None:
    """Verifies that drift detection fails gracefully when offline.

    Args:
        tmp_path (Path): Pytest fixture for a temporary directory.
        mocker (MagicMock): Pytest fixture for mocking.
    """
    mock_cls = mocker.patch("git_pulsar.cli.GitRepo")
    repo = mock_cls.return_value
    repo.current_branch.return_value = "main"
    repo._run.side_effect = Exception("Network offline")

    result = cli._check_remote_drift(tmp_path)
    assert result is None


def test_check_remote_drift_local_is_newer(tmp_path: Path, mocker: MagicMock) -> None:
    """Verifies that no warning is issued when the local session is the most recent.

    Args:
        tmp_path (Path): Pytest fixture for a temporary directory.
        mocker (MagicMock): Pytest fixture for mocking.
    """
    mock_cls = mocker.patch("git_pulsar.cli.GitRepo")
    repo = mock_cls.return_value
    repo.current_branch.return_value = "main"

    mocker.patch("git_pulsar.system.get_identity_slug", return_value="laptop--123")
    mocker.patch(
        "git_pulsar.ops.get_backup_ref",
        return_value="refs/heads/wip/pulsar/laptop--123/main",
    )

    repo.list_refs.return_value = [
        "refs/heads/wip/pulsar/desktop--456/main",
        "refs/heads/wip/pulsar/laptop--123/main",
    ]

    def mock_run_side_effect(cmd: list[str], **kwargs: Any) -> str:
        if cmd[0] == "fetch":
            return ""
        if cmd[0] == "log":
            # Local is 2000, remote is 1000
            if "desktop" in cmd[-1]:
                return "1000"
            if "laptop" in cmd[-1]:
                return "2000"
        return "0"

    repo._run.side_effect = mock_run_side_effect

    result = cli._check_remote_drift(tmp_path)
    assert result is None


def test_check_remote_drift_remote_is_newer(tmp_path: Path, mocker: MagicMock) -> None:
    """Verifies that a warning is issued when another machine has a newer backup stream.

    Args:
        tmp_path (Path): Pytest fixture for a temporary directory.
        mocker (MagicMock): Pytest fixture for mocking.
    """
    mock_cls = mocker.patch("git_pulsar.cli.GitRepo")
    repo = mock_cls.return_value
    repo.current_branch.return_value = "main"

    mocker.patch("git_pulsar.system.get_identity_slug", return_value="laptop--123")
    mocker.patch(
        "git_pulsar.ops.get_backup_ref",
        return_value="refs/heads/wip/pulsar/laptop--123/main",
    )

    repo.list_refs.return_value = [
        "refs/heads/wip/pulsar/desktop--456/main",
        "refs/heads/wip/pulsar/laptop--123/main",
    ]

    def mock_run_side_effect(cmd: list[str], **kwargs: Any) -> str:
        if cmd[0] == "fetch":
            return ""
        if cmd[0] == "log":
            # Remote is 2000, Local is 1000
            if "desktop" in cmd[-1]:
                return "2000"
            if "laptop" in cmd[-1]:
                return "1000"
        return "0"

    repo._run.side_effect = mock_run_side_effect

    # Mock time.time() to simulate 15 minutes since the remote commit (2000 + 900)
    mocker.patch("time.time", return_value=2900.0)

    result = cli._check_remote_drift(tmp_path)
    assert result is not None
    assert "desktop--456" in result
    assert "15 mins" in result


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
