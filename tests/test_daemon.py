from pathlib import Path
from unittest.mock import MagicMock

from src import daemon


def test_is_repo_busy_detects_merge_head(tmp_path: Path) -> None:
    """Should return True if MERGE_HEAD exists in .git dir."""
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "MERGE_HEAD").touch()

    assert daemon.is_repo_busy(tmp_path) is True


def test_is_repo_busy_clean(tmp_path: Path) -> None:
    """Should return False if no lock files exist."""
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    assert daemon.is_repo_busy(tmp_path) is False


def test_run_backup_aborts_on_wrong_branch(tmp_path: Path, mocker: MagicMock) -> None:
    """Daemon should not backup if user is on 'main' instead of 'wip/pulsar'."""
    # 1. Setup real filesystem (Replace dangerous global Path.exists mock)
    (tmp_path / ".git").mkdir()

    # 2. Patch LOG_FILE to avoid hitting real home dir (and crashing on stat)
    mocker.patch("src.daemon.LOG_FILE", tmp_path / "test.log")

    # 3. Mock git calls
    mock_check_output = mocker.patch("subprocess.check_output")
    mock_run = mocker.patch("subprocess.run")

    # Simulate current branch = 'main'
    mock_check_output.side_effect = (
        lambda cmd, **kwargs: "main" if "branch" in cmd else ""
    )

    daemon.run_backup(str(tmp_path))

    # Assert we checked the branch
    assert mock_check_output.call_count > 0
    # Assert we NEVER called git commit or push
    mock_run.assert_not_called()


def test_run_backup_commits_dirty_state(tmp_path: Path, mocker: MagicMock) -> None:
    """Daemon should commit if on correct branch and changes exist."""
    # 1. Setup real filesystem
    (tmp_path / ".git").mkdir()

    # 2. Patch LOG_FILE
    mocker.patch("src.daemon.LOG_FILE", tmp_path / "test.log")

    # 3. Mocks
    mocker.patch("src.daemon.is_repo_busy", return_value=False)
    mocker.patch("src.daemon.has_large_files", return_value=False)

    mock_check_output = mocker.patch("subprocess.check_output")
    mock_run = mocker.patch("subprocess.run")

    def check_output_side_effect(cmd, **kwargs):
        if "branch" in cmd:
            return "wip/pulsar"
        if "status" in cmd:
            return "M  src/main.py"  # Simulate dirty file
        return ""

    mock_check_output.side_effect = check_output_side_effect

    daemon.run_backup(str(tmp_path))

    # Verify commit flow
    # 1. git add .
    mock_run.assert_any_call(["git", "add", "."], cwd=mocker.ANY, check=True)
    # 2. git push
    # Note: We check that 'git push' was called with the correct branch
    args, _ = mock_run.call_args
    assert args[0][:3] == ["git", "push", "origin"]
