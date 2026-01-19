import os
from pathlib import Path
from unittest.mock import MagicMock, call

import pytest

from src import cli


def test_setup_repo_initializes_git_and_gitignore(
    tmp_path: Path, mocker: MagicMock
) -> None:
    """Ensure git init is called and .gitignore is created if missing."""
    # 1. Simulate running inside a temp dir
    os.chdir(tmp_path)

    # 2. Mock subprocess to prevent actual git execution
    mock_run = mocker.patch("subprocess.run")

    # 3. Inject a fake registry file inside tmp_path
    fake_registry = tmp_path / ".registry"

    cli.setup_repo(registry_path=fake_registry)

    # Assertions
    # Check .gitignore creation
    gitignore = tmp_path / ".gitignore"
    assert gitignore.exists()
    assert "__pycache__/" in gitignore.read_text()

    # Check git init call
    # We expect 'git init' because tmp_path has no .git folder yet
    mock_run.assert_any_call(["git", "init"], check=True)

    # Check registry update
    assert fake_registry.exists()
    assert str(tmp_path) in fake_registry.read_text()


def test_setup_repo_skips_existing_registry_entry(
    tmp_path: Path, mocker: MagicMock
) -> None:
    """Ensure we don't duplicate entries in the registry."""
    os.chdir(tmp_path)
    mocker.patch("subprocess.run")

    fake_registry = tmp_path / ".registry"
    fake_registry.write_text(f"{tmp_path}\n")  # Pre-fill

    cli.setup_repo(registry_path=fake_registry)

    # content should stay the same (one entry), not double up
    lines = fake_registry.read_text().strip().splitlines()
    assert len(lines) == 1


def test_bootstrap_env_enforces_macos(mocker: MagicMock) -> None:
    """Ensure we exit early if not on macOS."""
    mocker.patch("sys.platform", "linux")
    mock_print = mocker.patch("builtins.print")

    cli.bootstrap_env()

    mock_print.assert_called_with(
        "❌ The --env workflow is currently optimized for macOS."
    )


def test_bootstrap_env_checks_dependencies(tmp_path: Path, mocker: MagicMock) -> None:
    """Ensure script fails if uv or direnv are missing."""
    mocker.patch("sys.platform", "darwin")
    os.chdir(tmp_path)

    # Mock shutil.which to return None (simulating missing tools)
    mocker.patch("shutil.which", return_value=None)

    # We expect the script to exit with code 1
    # We do NOT mock sys.exit; we catch the exception instead.
    with pytest.raises(SystemExit) as excinfo:
        cli.bootstrap_env()

    assert excinfo.value.code == 1


def test_bootstrap_env_scaffolds_files(tmp_path: Path, mocker: MagicMock) -> None:
    """Ensure pyproject.toml, .envrc, and vscode settings are generated."""
    mocker.patch("sys.platform", "darwin")
    os.chdir(tmp_path)

    # Mock dependencies present
    mocker.patch("shutil.which", return_value="/usr/bin/fake")
    mock_run = mocker.patch("subprocess.run")

    cli.bootstrap_env()

    # 1. Check uv init called
    # (File won't actually exist since we mocked subprocess, but we verify the call)
    mock_run.assert_any_call(
        ["uv", "init", "--no-workspace", "--python", "3.12"], check=True
    )

    # 2. Check .envrc creation
    envrc = tmp_path / ".envrc"
    assert envrc.exists()
    assert "source .venv/bin/activate" in envrc.read_text()

    # 3. Check VS Code settings
    settings = tmp_path / ".vscode" / "settings.json"
    assert settings.exists()
    assert "python.defaultInterpreterPath" in settings.read_text()


def test_bootstrap_env_skips_existing_files(tmp_path: Path, mocker: MagicMock) -> None:
    """Ensure we do not overwrite existing configuration."""
    mocker.patch("sys.platform", "darwin")
    os.chdir(tmp_path)
    mocker.patch("shutil.which", return_value="/usr/bin/fake")
    mock_run = mocker.patch("subprocess.run")

    # Pre-create files
    (tmp_path / "pyproject.toml").touch()

    envrc = tmp_path / ".envrc"
    envrc.write_text("# old content")

    cli.bootstrap_env()

    # Should NOT have called uv init
    # We inspect all calls to subprocess.run to ensure 'uv' wasn't one of them
    for mock_call in mock_run.call_args_list:
        args = mock_call[0][0]
        assert "uv" not in args

    # Content should be preserved
    assert envrc.read_text() == "# old content"


def test_main_triggers_bootstrap(mocker: MagicMock) -> None:
    """Ensure --env flag calls the bootstrap function and then setup."""
    mock_bootstrap = mocker.patch("src.cli.bootstrap_env")
    mock_setup = mocker.patch("src.cli.setup_repo")

    # Test long flag
    mocker.patch("sys.argv", ["git-pulsar", "--env"])
    cli.main()
    mock_bootstrap.assert_called_once()
    mock_setup.assert_called_once()

    mock_bootstrap.reset_mock()
    mock_setup.reset_mock()

    # Test short flag
    mocker.patch("sys.argv", ["git-pulsar", "-e"])
    cli.main()
    mock_bootstrap.assert_called_once()
    mock_setup.assert_called_once()


def test_main_default_behavior(mocker: MagicMock) -> None:
    """Ensure running without flags defaults to setup_repo."""
    mock_bootstrap = mocker.patch("src.cli.bootstrap_env")
    mock_setup = mocker.patch("src.cli.setup_repo")

    mocker.patch("sys.argv", ["git-pulsar"])
    cli.main()

    mock_bootstrap.assert_not_called()
    mock_setup.assert_called_once()


def test_install_service_accepts_interval(mocker: MagicMock) -> None:
    """Ensure --interval is passed to the service installer."""
    mock_install = mocker.patch("src.service.install")

    # Simulate: git-pulsar install-service --interval 300
    mocker.patch("sys.argv", ["git-pulsar", "install-service", "--interval", "300"])

    cli.main()

    # Verify the interval argument was parsed and passed correctly
    mock_install.assert_called_once_with(interval=300)


def test_now_command_triggers_interactive_daemon(mocker: MagicMock) -> None:
    """Ensure 'now' command calls daemon in interactive mode."""
    # We must mock the module where main is defined.
    # Since cli imports daemon, we can patch src.daemon.main
    mock_daemon_main = mocker.patch("src.daemon.main")

    # Simulate: git-pulsar now
    mocker.patch("sys.argv", ["git-pulsar", "now"])

    cli.main()

    mock_daemon_main.assert_called_once_with(interactive=True)


def test_restore_clean(mocker: MagicMock) -> None:
    """Should checkout the file from wip/pulsar if working tree is clean."""
    mocker.patch("src.cli.Path.exists", return_value=True)
    # Mock check_output to return empty string (indicating no changes)
    mocker.patch("src.cli.subprocess.check_output", return_value="")
    mock_run = mocker.patch("src.cli.subprocess.run")

    cli.restore_file("script.py")

    mock_run.assert_called_with(
        ["git", "checkout", "wip/pulsar", "--", "script.py"], check=True
    )


def test_restore_dirty_fails(mocker: MagicMock) -> None:
    """Should abort if the file has local changes."""
    mocker.patch("src.cli.Path.exists", return_value=True)
    # Simulate git status returning a modification
    mocker.patch("src.cli.subprocess.check_output", return_value="M script.py")

    with pytest.raises(SystemExit):
        cli.restore_file("script.py")


def test_restore_force(mocker: MagicMock) -> None:
    """Should overwrite local changes if --force is provided."""
    mocker.patch("src.cli.Path.exists", return_value=True)
    mocker.patch("src.cli.subprocess.check_output", return_value="M script.py")
    mock_run = mocker.patch("src.cli.subprocess.run")

    cli.restore_file("script.py", force=True)

    mock_run.assert_called_with(
        ["git", "checkout", "wip/pulsar", "--", "script.py"], check=True
    )


def test_finalize_success(mocker: MagicMock) -> None:
    """Should switch to main, squash merge, commit, and reset backup branch."""
    mocker.patch("src.cli.Path.exists", return_value=True)
    mocker.patch("src.cli.subprocess.check_output", return_value="")
    mock_run = mocker.patch("src.cli.subprocess.run")

    cli.finalize_work()

    expected_calls = [
        call(["git", "checkout", "main"], check=True),
        call(["git", "merge", "--squash", "wip/pulsar"], check=True),
        call(["git", "commit"], check=True),
        call(["git", "branch", "-f", "wip/pulsar", "main"], check=True),
    ]
    mock_run.assert_has_calls(expected_calls)


def test_finalize_dirty_fails(mocker: MagicMock) -> None:
    """Should abort if there are uncommitted changes."""
    mocker.patch("src.cli.Path.exists", return_value=True)
    mocker.patch("src.cli.subprocess.check_output", return_value="?? new_file.py")

    with pytest.raises(SystemExit):
        cli.finalize_work()


def test_pause_creates_marker_file(tmp_path: Path) -> None:
    """Ensure 'git-pulsar pause' creates the .git/pulsar_paused file."""
    (tmp_path / ".git").mkdir()
    os.chdir(tmp_path)

    cli.set_pause_state(paused=True)
    assert (tmp_path / ".git" / "pulsar_paused").exists()


def test_resume_removes_marker_file(tmp_path: Path) -> None:
    """Ensure 'git-pulsar resume' deletes the .git/pulsar_paused file."""
    (tmp_path / ".git").mkdir()
    marker = tmp_path / ".git" / "pulsar_paused"
    marker.touch()
    os.chdir(tmp_path)

    cli.set_pause_state(paused=False)
    assert not marker.exists()


def test_status_reports_pause_state(
    tmp_path: Path, capsys: pytest.CaptureFixture, mocker: MagicMock
) -> None:
    """Ensure 'git-pulsar status' explicitly reports the PAUSED state."""
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "pulsar_paused").touch()
    os.chdir(tmp_path)

    # Mock git calls to avoid system errors
    mocker.patch("subprocess.run")
    mocker.patch("subprocess.check_output", return_value="15 minutes ago")

    cli.show_status()

    captured = capsys.readouterr()
    assert "⏸️  PAUSED" in captured.out


def test_diff_shows_untracked_files(
    tmp_path: Path, capsys: pytest.CaptureFixture, mocker: MagicMock
) -> None:
    """Ensure 'git-pulsar diff' lists untracked files."""
    (tmp_path / ".git").mkdir()
    os.chdir(tmp_path)

    mocker.patch("subprocess.run")
    # Simulate git ls-files returning a new file
    mocker.patch("subprocess.check_output", return_value="new_script.py")

    cli.show_diff()

    captured = capsys.readouterr()
    assert "Untracked (New) Files" in captured.out
    assert "+ new_script.py" in captured.out
