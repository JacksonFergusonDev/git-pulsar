from pathlib import Path
from unittest.mock import MagicMock

from git_pulsar.git_wrapper import GitRepo


def test_list_refs_logs_error_on_failure(
    mocker: MagicMock, caplog: MagicMock, tmp_path: Path
) -> None:
    """Verifies that git failures are logged instead of passing silently."""
    # Mock subprocess to raise an exception
    mocker.patch("subprocess.run", side_effect=Exception("Git is broken"))

    # Create a fake .git directory so GitRepo accepts the path
    (tmp_path / ".git").mkdir()
    repo = GitRepo(tmp_path)

    # Run the method
    results = repo.list_refs("refs/heads/*")

    # Assert it handled the error gracefully
    assert results == []

    # Assert it logged the warning
    assert "Git error listing refs" in caplog.text


def test_run_diff_with_file_targeting(mocker: MagicMock, tmp_path: Path) -> None:
    """Verifies that run_diff correctly appends the file boundary double-dash."""
    (tmp_path / ".git").mkdir()
    repo = GitRepo(tmp_path)
    mock_run = mocker.patch.object(repo, "_run")

    # Diff against target without file
    repo.run_diff("HEAD")
    mock_run.assert_called_with(["diff", "HEAD"], capture=False)

    # Diff against target with specific file
    repo.run_diff("refs/backup/main", file="src/main.py")
    mock_run.assert_called_with(
        ["diff", "refs/backup/main", "--", "src/main.py"], capture=False
    )
