import os
from pathlib import Path
from unittest.mock import MagicMock

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
