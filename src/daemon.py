import subprocess
import os
import datetime
from pathlib import Path

# Config
APP_NAME = "git-pulsar"
REGISTRY_FILE = Path.home() / ".git_pulsar_registry"
BACKUP_BRANCH = "wip/pulsar"
LOG_FILE = Path.home() / ".git_pulsar_log"
MAX_LOG_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB
GITHUB_FILE_LIMIT_BYTES = 100 * 1024 * 1024  # 100 MB


def log(message):
    """Logs to file, rotating if too large."""
    if LOG_FILE.exists() and LOG_FILE.stat().st_size > MAX_LOG_SIZE_BYTES:
        try:
            os.remove(LOG_FILE)
        except OSError:
            pass

    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(LOG_FILE, "a") as f:
            f.write(f"[{timestamp}] {message}\n")
    except OSError:
        pass


def notify(title, message):
    """Sends a native macOS notification."""
    clean_msg = message.replace('"', "'")
    script = (
        f'display notification "{clean_msg}" with title "{title}" subtitle "{APP_NAME}"'
    )
    try:
        subprocess.run(["osascript", "-e", script], stderr=subprocess.DEVNULL)
    except Exception:
        pass


def is_repo_busy(repo_path):
    git_dir = repo_path / ".git"
    critical_files = [
        "MERGE_HEAD",
        "REBASE_HEAD",
        "CHERRY_PICK_HEAD",
        "BISECT_LOG",
        "rebase-merge",
        "rebase-apply",
    ]
    for f in critical_files:
        if (git_dir / f).exists():
            return True
    return False


def has_large_files(repo_path):
    """
    Scans for files larger than GitHub's 100MB limit.
    Returns True if a large file is found (and notifies user).
    """
    # Only scan files git knows about or sees as untracked
    try:
        cmd = ["git", "ls-files", "--others", "--modified", "--exclude-standard"]
        candidates = subprocess.check_output(cmd, cwd=repo_path, text=True).splitlines()
    except subprocess.CalledProcessError:
        return False

    for name in candidates:
        file_path = repo_path / name
        try:
            if file_path.stat().st_size > GITHUB_FILE_LIMIT_BYTES:
                log(
                    f"WARNING {repo_path.name}: Large file detected ({name}). Backup aborted."
                )
                notify("Backup Aborted", f"File >100MB detected: {name}")
                return True
        except OSError:
            continue

    return False


def prune_registry(original_path_str):
    if not REGISTRY_FILE.exists():
        return
    try:
        with open(REGISTRY_FILE, "r") as f:
            lines = f.readlines()
        with open(REGISTRY_FILE, "w") as f:
            for line in lines:
                if line.strip() != original_path_str:
                    f.write(line)
        repo_name = Path(original_path_str).name
        log(f"PRUNED: {original_path_str} removed from registry.")
        notify("Backup Stopped", f"Removed missing repo: {repo_name}")
    except OSError as e:
        log(f"ERROR: Could not prune registry. {e}")


def run_backup(original_path_str):
    try:
        repo_path = Path(original_path_str).expanduser().resolve()
    except Exception as e:
        log(f"ERROR: Could not resolve path {original_path_str}: {e}")
        return

    repo_name = repo_path.name

    if not repo_path.exists():
        log(f"MISSING {original_path_str}: Path not found. Pruning.")
        prune_registry(original_path_str)
        return

    if not (repo_path / ".git").exists():
        log(f"SKIPPED {repo_name}: Not a git repo anymore.")
        return

    if is_repo_busy(repo_path):
        log(f"SKIPPED {repo_name}: Repo is busy (merge/rebase).")
        return

    if has_large_files(repo_path):
        return

    try:
        # Check branch
        current_branch = subprocess.check_output(
            ["git", "branch", "--show-current"], cwd=repo_path, text=True
        ).strip()

        if current_branch != BACKUP_BRANCH:
            return

        # Check status
        status = subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=repo_path, text=True
        )
        if not status.strip():
            return

        # Commit
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        subprocess.run(["git", "add", "."], cwd=repo_path, check=True)
        subprocess.run(
            ["git", "commit", "-m", f"Pulsar auto-backup: {timestamp}"],
            cwd=repo_path,
            check=True,
            stdout=subprocess.DEVNULL,
        )

        # Push
        subprocess.run(
            ["git", "push", "origin", BACKUP_BRANCH],
            cwd=repo_path,
            check=True,
            timeout=45,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        log(f"SUCCESS {repo_name}: Pushed.")

    except subprocess.TimeoutExpired:
        log(f"TIMEOUT {repo_name}: Push timed out.")
    except subprocess.CalledProcessError as e:
        err_text = e.stderr.decode("utf-8") if e.stderr else "Unknown git error"
        log(f"ERROR {repo_name}: {err_text.strip()}")
        notify("Backup Failed", f"{repo_name}: Check logs.")
    except Exception as e:
        log(f"CRITICAL {repo_name}: {e}")
        notify("Pulsar Crash", f"{repo_name}: {e}")


def main():
    if not REGISTRY_FILE.exists():
        return

    with open(REGISTRY_FILE, "r") as f:
        repos = [line.strip() for line in f if line.strip()]

    for repo_str in set(repos):
        run_backup(repo_str)


if __name__ == "__main__":
    main()
