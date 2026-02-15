import atexit
import datetime
import logging
import os
import signal
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path
from types import FrameType
from typing import Iterator

from rich.console import Console

from . import ops, system
from .config import Config
from .constants import (
    APP_NAME,
    GIT_LOCK_FILES,
    LOG_FILE,
    PID_FILE,
    REGISTRY_FILE,
)
from .git_wrapper import GitRepo
from .system import get_system

SYSTEM = get_system()

logger = logging.getLogger(APP_NAME)
logger.setLevel(logging.INFO)

console = Console()
err_console = Console(stderr=True)


@contextmanager
def temporary_index(repo_path: Path) -> Iterator[dict[str, str]]:
    """Context manager for creating an isolated git index environment.

    This allows the daemon to stage and commit files without interfering with the
    user's actual git index or staging area.

    Args:
        repo_path (Path): The path to the repository.

    Yields:
        dict[str, str]: A dictionary containing the modified environment variables.
    """
    temp_index = repo_path / ".git" / "pulsar_index"
    env = os.environ.copy()
    env["GIT_INDEX_FILE"] = str(temp_index)
    try:
        yield env
    finally:
        if temp_index.exists():
            temp_index.unlink()


def run_maintenance(repos: list[str]) -> None:
    """Checks if weekly maintenance (pruning old backups) is required and runs it.

    Args:
        repos (list[str]): A list of registered repository paths.
    """
    # Track the last prune time in the registry directory.
    state_file = REGISTRY_FILE.parent / "last_prune"

    # Enforce a 7-day interval.
    if state_file.exists():
        age = time.time() - state_file.stat().st_mtime
        if age < 7 * 86400:
            return

    logger.info("MAINTENANCE: Running weekly prune (30d retention)...")

    for repo_str in set(repos):
        try:
            ops.prune_backups(30, Path(repo_str))
        except Exception as e:
            logger.error(f"PRUNE ERROR {repo_str}: {e}")

    # Update the timestamp for the next cycle.
    try:
        state_file.touch()
    except OSError as e:
        logger.error(f"MAINTENANCE ERROR: Could not update state file: {e}")


def get_remote_host(repo_path: Path, remote_name: str) -> str | None:
    """Extracts the hostname from a git remote URL.

    Supports both SSH (git@...) and HTTPS (https://...) formats.

    Args:
        repo_path (Path): The local path to the repository.
        remote_name (str): The name of the remote (e.g., 'origin').

    Returns:
        str | None: The hostname (e.g., 'github.com') or None if parsing fails.
    """
    try:
        url = subprocess.check_output(
            ["git", "remote", "get-url", remote_name], cwd=repo_path, text=True
        ).strip()

        # Handle SSH: git@github.com:user/repo.git
        if "@" in url:
            return url.split("@")[1].split(":")[0]
        # Handle HTTPS: https://github.com/user/repo.git
        if "://" in url:
            return url.split("://")[1].split("/")[0]
        return None
    except Exception:
        return None


def is_remote_reachable(host: str) -> bool:
    """Performs a quick TCP connectivity check on the remote host.

    Args:
        host (str): The hostname to check.

    Returns:
        bool: True if the host accepts connections on port 443 or 22, False otherwise.
    """
    if not host:
        return False  # Implicitly offline if host is unknown.

    for port in [443, 22]:
        try:
            # 3 second timeout is sufficient for a local network
            # or decent internet connection.
            with socket.create_connection((host, port), timeout=3):
                return True
        except OSError:
            continue
    return False


def is_repo_busy(repo_path: Path, interactive: bool = False) -> bool:
    """Determines if a repository is currently locked by a Git operation.

    Checks for standard git lock files and stale index locks.

    Args:
        repo_path (Path): The path to the repository.
        interactive (bool, optional):   Whether to print warnings to console.
                                        Defaults to False.

    Returns:
        bool: True if the repository is busy/locked, False otherwise.
    """
    git_dir = repo_path / ".git"

    # 1. Check for operational locks (e.g., MERGE_HEAD).
    for f in GIT_LOCK_FILES:
        if (git_dir / f).exists():
            return True

    # 2. Check for index.lock (Race Condition Handler).
    lock_file = git_dir / "index.lock"
    if lock_file.exists():
        # A. Check for stale lock (> 24 hours).
        try:
            mtime = lock_file.stat().st_mtime
            age_hours = (time.time() - mtime) / 3600
            if age_hours > 24:
                msg = f"Stale lock detected in {repo_path.name} ({age_hours:.1f}h old)."
                logger.warning(msg)
                if interactive:
                    console.print(
                        f"[bold yellow]WARNING:[/bold yellow] {msg}\n   "
                        f"Run 'rm {lock_file}' to fix."
                    )
                else:
                    SYSTEM.notify("Pulsar Warning", f"Stale lock in {repo_path.name}")
                return True
        except OSError:
            pass  # File vanished (race resolved).

        # B. Fail fast if lock still exists
        if lock_file.exists():
            return True

    return False


def has_large_files(repo_path: Path, config: Config) -> bool:
    """Scans untracked or modified files for sizes exceeding the limit.

    Args:
        repo_path (Path): The path to the repository.
        config (Config): The configuration instance for this repository.

    Returns:
        bool: True if a large file is found, False otherwise.
    """
    limit = config.limits.large_file_threshold

    # Only scan files git knows about or sees as untracked.
    try:
        cmd = ["git", "ls-files", "--others", "--modified", "--exclude-standard"]
        candidates = subprocess.check_output(cmd, cwd=repo_path, text=True).splitlines()
    except subprocess.CalledProcessError as e:
        logger.warning(f"Large file scan failed for {repo_path.name}: {e}")
        return False

    for name in candidates:
        file_path = repo_path / name
        try:
            if file_path.stat().st_size > limit:
                # Dynamic size formatting (Bytes -> MB)
                limit_mb = int(limit / (1024 * 1024))

                logger.warning(
                    f"WARNING {repo_path.name}: Large file detected ({name}). "
                    "Backup aborted."
                )
                SYSTEM.notify("Backup Aborted", f"File >{limit_mb}MB detected: {name}")
                return True
        except OSError as e:
            logger.warning(f"Failed to check size of file {name}: {e}")
            continue

    return False


def prune_registry(original_path_str: str) -> None:
    """Removes a missing repository path from the registry file.

    Args:
        original_path_str (str): The path string to remove.
    """
    if not REGISTRY_FILE.exists():
        return

    target = original_path_str.strip()
    tmp_file = REGISTRY_FILE.with_suffix(".tmp")

    try:
        # 1. Read existing registry.
        with open(REGISTRY_FILE, "r") as f:
            lines = f.readlines()

        # 2. Write valid lines to temp file.
        with open(tmp_file, "w") as f:
            for line in lines:
                clean_line = line.strip()
                if clean_line and clean_line != target:
                    f.write(clean_line + "\n")
            f.flush()
            os.fsync(f.fileno())  # Force write to disk.

        # 3. Atomic Swap.
        os.replace(tmp_file, REGISTRY_FILE)

        repo_name = Path(original_path_str).name
        logger.info(f"PRUNED: {original_path_str} removed from registry.")
        SYSTEM.notify("Backup Stopped", f"Removed missing repo: {repo_name}")

    except OSError as e:
        logger.error(f"ERROR: Could not prune registry. {e}")
        if tmp_file.exists():
            tmp_file.unlink()


def _should_skip(repo_path: Path, config: Config, interactive: bool) -> str | None:
    """Determines if the backup for a given repository should be skipped.

    Args:
        repo_path (Path): The repository path.
        config (Config): The configuration instance for this repository.
        interactive (bool): Whether the session is interactive (CLI) or background.

    Returns:
        str | None: The reason for skipping, or None if backup should proceed.
    """
    if not repo_path.exists():
        return "Path missing"

    if (repo_path / ".git" / "pulsar_paused").exists():
        return "Paused by user"

    if not interactive:
        if SYSTEM.is_under_load():
            return "System under load"

        # Check battery levels (don't drain battery on background tasks).
        pct, plugged = SYSTEM.get_battery()
        # Uses config value instead of hardcoded '10'
        if not plugged and pct < config.daemon.min_battery_percent:
            return "Battery critical"

    return None


def _attempt_push(
    repo: GitRepo, refspec: str, config: Config, interactive: bool
) -> None:
    """Attempts to push the backup reference to the remote.

    Respects eco-mode settings and network availability.

    Args:
        repo (GitRepo): The repository instance.
        refspec (str): The refspec to push (e.g., 'ref:ref').
        config (Config): The configuration instance for this repository.
        interactive (bool): Whether to output status to the console.
    """
    # 1. Eco Mode Check.
    percent, plugged = SYSTEM.get_battery()
    # Uses config value instead of hardcoded '20'
    if not plugged and percent < config.daemon.eco_mode_percent:
        logger.info(f"ECO MODE {repo.path.name}: Committed. Push skipped.")
        return

    # 2. Network Connectivity Check.
    remote_name = config.core.remote_name
    host = get_remote_host(repo.path, remote_name)
    if host and not is_remote_reachable(host):
        logger.info(f"OFFLINE {repo.path.name}: Committed. Push skipped.")
        return

    # 3. Push Execution.
    try:
        env = os.environ.copy()
        env["GIT_SSH_COMMAND"] = "ssh -o BatchMode=yes"
        cmd = ["push", remote_name, refspec]

        if interactive:
            with console.status(
                f"[bold blue]Pushing {repo.path.name}...[/bold blue]", spinner="dots"
            ):
                repo._run(cmd, capture=True, env=env)
            console.print(
                f"[bold green]SUCCESS:[/bold green] {repo.path.name}: Pushed."
            )
        else:
            repo._run(cmd, capture=True, env=env)
            logger.info(f"SUCCESS {repo.path.name}: Pushed.")

    except Exception as e:
        if interactive:
            console.print(f"[bold red]PUSH ERROR {repo.path.name}:[/bold red] {e}")
        else:
            logger.error(f"PUSH ERROR {repo.path.name}: {e}")


def _get_ref_timestamp(repo: GitRepo, ref: str) -> int:
    """Gets the commit timestamp of a specific reference.

    Args:
        repo (GitRepo): The repository instance.
        ref (str): The reference to check.

    Returns:
        int: Unix timestamp of the commit, or 0 if ref does not exist.
    """
    try:
        ts = repo._run(["log", "-1", "--format=%ct", ref])
        return int(ts.strip())
    except Exception as e:
        logger.debug(f"Could not get timestamp for {ref}: {e}")
        return 0


def run_backup(original_path_str: str, interactive: bool = False) -> None:
    """Orchestrates the backup workflow for a single repository."""
    repo_path = Path(original_path_str).resolve()

    # Load context-aware config (Global + Local)
    config = Config.load(repo_path)

    # Pass config to _should_skip
    if reason := _should_skip(repo_path, config, interactive):
        if reason == "Path missing":
            prune_registry(original_path_str)
        elif reason == "System under load":
            pass
        else:
            logger.info(f"SKIPPED {repo_path.name}: {reason}")
        return

    # Pass config to has_large_files (re-added safety check)
    if has_large_files(repo_path, config):
        return

    try:
        repo = GitRepo(repo_path)
        current_branch = repo.current_branch()
        if not current_branch:
            return

        # Define Refs
        local_backup_ref = ops.get_backup_ref(current_branch)
        ref_suffix = local_backup_ref.replace("refs/heads/", "")
        remote_backup_ref = f"refs/remotes/{config.core.remote_name}/{ref_suffix}"

        # --- COMMIT PHASE ---
        last_commit_ts = _get_ref_timestamp(repo, local_backup_ref)
        time_since_commit = time.time() - last_commit_ts

        if time_since_commit >= config.daemon.commit_interval:
            with temporary_index(repo_path) as env:
                # Stage current working directory into temp index.
                # Use wrapper method if available, or repo._run(["add", "."], env=env)
                repo.add_all()
                # Note: GitRepo.add_all() in wrapper doesn't accept env.
                # Keeping manual run with env.
                repo._run(["add", "."], env=env)

                # Write Tree.
                tree_oid = repo.write_tree(env=env)

                # Determine Parents (Synthetic Merge).
                parents = []
                if parent_backup := repo.rev_parse(local_backup_ref):
                    parents.append(parent_backup)
                if parent_head := repo.rev_parse("HEAD"):
                    parents.append(parent_head)

                # Check for actual changes
                should_commit = True
                if parent_backup:
                    prev_tree = repo._run(["rev-parse", f"{parent_backup}^{{tree}}"])
                    if prev_tree == tree_oid:
                        should_commit = False

                if should_commit:
                    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                    # Use wrapper method
                    commit_oid = repo.commit_tree(
                        tree=tree_oid,
                        parents=parents,
                        message=f"Shadow backup {timestamp}",
                        env=env,
                    )

                    # Use wrapper method
                    repo.update_ref(local_backup_ref, commit_oid, parent_backup)

                    if interactive:
                        console.print(f"[green]Committed {repo_path.name}[/green]")

        # --- PUSH PHASE ---
        current_local_ts = _get_ref_timestamp(repo, local_backup_ref)
        last_push_ts = _get_ref_timestamp(repo, remote_backup_ref)

        time_since_push = time.time() - last_push_ts
        has_new_data = current_local_ts > last_push_ts

        if has_new_data and (
            time_since_push >= config.daemon.push_interval or interactive
        ):
            refspec = f"{local_backup_ref}:{local_backup_ref}"
            # Pass config to _attempt_push
            _attempt_push(repo, refspec, config, interactive)

    except Exception:
        logger.exception(f"CRITICAL {repo_path.name}: Backup iteration failed")


def setup_logging(interactive: bool) -> None:
    """Configures the logging subsystem."""
    formatter = logging.Formatter(
        "[%(asctime)s] %(levelname)s: %(message)s", "%Y-%m-%d %H:%M:%S"
    )

    stream_handler = logging.StreamHandler(
        sys.stderr if not interactive else sys.stdout
    )
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    if not interactive:
        # Load fresh global config to get log limits
        conf = Config.load()

        file_handler = RotatingFileHandler(
            LOG_FILE,
            maxBytes=conf.limits.max_log_size,
            backupCount=5,
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)


def main(interactive: bool = False) -> None:
    """The main daemon execution loop.

    Processes all registered repositories, handles signals, and enforces timeouts.

    Args:
        interactive (bool, optional): Whether to run a single pass (CLI 'now' command).
                                      Defaults to False.
    """
    setup_logging(interactive)

    repos = [str(p) for p in system.get_registered_repos()]

    if not repos:
        if interactive:
            console.print(
                "[yellow]Registry empty. Run 'git-pulsar' in "
                "a repo to register it.[/yellow]"
            )
        return

    # Set a timeout handler for stalled network mounts.
    def timeout_handler(_signum: int, _frame: FrameType | None) -> None:
        raise TimeoutError("Repo access timed out")

    signal.signal(signal.SIGALRM, timeout_handler)

    # PID File Management.
    if not interactive:
        try:
            with open(PID_FILE, "w") as f:
                f.write(str(os.getpid()))

            # Ensure cleanup on exit.
            atexit.register(lambda: PID_FILE.unlink(missing_ok=True))
        except OSError as e:
            logger.warning(f"Could not write PID file: {e}")

    for repo_str in set(repos):
        try:
            # 5 second timeout per repo to prevent hanging.
            signal.alarm(5)
            run_backup(repo_str, interactive=interactive)
            signal.alarm(0)  # Disable alarm.
        except TimeoutError:
            logger.warning(f"TIMEOUT {repo_str}: Skipped (possible stalled mount).")
        except Exception:
            logger.exception(f"LOOP ERROR {repo_str}")

    # Run maintenance tasks (pruning).
    run_maintenance(repos)


if __name__ == "__main__":
    main()
