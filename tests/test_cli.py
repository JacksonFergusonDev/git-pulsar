import os
from pathlib import Path
from unittest.mock import MagicMock

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
    for call in mock_run.call_args_list:
        args = call[0][0]
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
