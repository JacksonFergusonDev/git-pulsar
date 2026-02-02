import atexit
import datetime
import logging
import os
import signal
import socket
import subprocess
import sys
import time
import tomllib
from contextlib import contextmanager
from dataclasses import dataclass, field
from logging.handlers import RotatingFileHandler
from pathlib import Path
from types import FrameType
from typing import Iterator

from rich.console import Console

from . import ops
from .constants import (
    APP_NAME,
    BACKUP_NAMESPACE,
    CONFIG_FILE,
    GIT_LOCK_FILES,
    LOG_FILE,
    PID_FILE,
    REGISTRY_FILE,
)
from .git_wrapper import GitRepo
from .system import get_machine_id, get_system

SYSTEM = get_system()

logger = logging.getLogger(APP_NAME)
logger.setLevel(logging.INFO)

console = Console()
err_console = Console(stderr=True)


@dataclass
class CoreConfig:
    """Core application settings.

    Attributes:
        backup_branch (str): The namespace used for backup refs.
        remote_name (str): The default git remote to push backups to.
    """

    backup_branch: str = BACKUP_NAMESPACE
    remote_name: str = "origin"


@dataclass
class LimitsConfig:
    """Resource limitation settings.

    Attributes:
        max_log_size (int): Max bytes for log files before rotation.
        large_file_threshold (int): Max bytes for a file before triggering a warning.
    """

    max_log_size: int = 5 * 1024 * 1024
    large_file_threshold: int = 100 * 1024 * 1024


@dataclass
class DaemonConfig:
    """Daemon operational settings.

    Attributes:
        min_battery_percent (int): Battery level below which backups pause.
        eco_mode_percent (int): Battery level below which network pushes are skipped.
    """

    min_battery_percent: int = 10
    eco_mode_percent: int = 20


@dataclass
class Config:
    """Global configuration aggregator.

    Attributes:
        core (CoreConfig): Core settings.
        limits (LimitsConfig): Resource limits.
        daemon (DaemonConfig): Daemon behavior settings.
    """

    core: CoreConfig = field(default_factory=CoreConfig)
    limits: LimitsConfig = field(default_factory=LimitsConfig)
    daemon: DaemonConfig = field(default_factory=DaemonConfig)

    @classmethod
    def load(cls) -> "Config":
        """Loads configuration from disk, applying defaults where necessary.

        Returns:
            Config: The populated configuration object.
        """
        instance = cls()

        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "rb") as f:
                    data = tomllib.load(f)

                # Selective update of config sections.
                if "core" in data:
                    instance.core = CoreConfig(**data["core"])
                if "limits" in data:
                    instance.limits = LimitsConfig(**data["limits"])
                if "daemon" in data:
                    instance.daemon = DaemonConfig(**data["daemon"])

            except tomllib.TOMLDecodeError as e:
                err_console.print(
                    f"[bold red]FATAL:[/bold red] Config syntax "
                    f"error in {CONFIG_FILE}:\n   {e}"
                )
                sys.exit(1)
            except Exception as e:
                err_console.print(f"[bold red]Config Error:[/bold red] {e}")
                # Assume partial failures are recoverable; continue with defaults.

        return instance


CONFIG = Config.load()


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

        # B. Wait-and-see (Micro-retry) to handle transient operations.
        time.sleep(1.0)
        if lock_file.exists():
            return True

    return False


def has_large_files(repo_path: Path) -> bool:
    """Scans untracked or modified files for sizes exceeding the limit.

    Args:
        repo_path (Path): The path to the repository.

    Returns:
        bool: True if a large file is found, False otherwise.
    """
    limit = CONFIG.limits.large_file_threshold

    # Only scan files git knows about or sees as untracked.
    try:
        cmd = ["git", "ls-files", "--others", "--modified", "--exclude-standard"]
        candidates = subprocess.check_output(cmd, cwd=repo_path, text=True).splitlines()
    except subprocess.CalledProcessError:
        return False

    for name in candidates:
        file_path = repo_path / name
        try:
            if file_path.stat().st_size > limit:
                logger.warning(
                    f"WARNING {repo_path.name}: Large file detected ({name}). "
                    "Backup aborted."
                )
                SYSTEM.notify("Backup Aborted", f"File >100MB detected: {name}")
                return True
        except OSError:
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


def _should_skip(repo_path: Path, interactive: bool) -> str | None:
    """Determines if the backup for a given repository should be skipped.

    Args:
        repo_path (Path): The repository path.
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
        if not plugged and pct < 10:
            return "Battery critical"

    return None


def _attempt_push(repo: GitRepo, refspec: str, interactive: bool) -> None:
    """Attempts to push the backup reference to the remote.

    Respects eco-mode settings and network availability.

    Args:
        repo (GitRepo): The repository instance.
        refspec (str): The refspec to push (e.g., 'ref:ref').
        interactive (bool): Whether to output status to the console.
    """
    # 1. Eco Mode Check.
    percent, plugged = SYSTEM.get_battery()
    if not plugged and percent < CONFIG.daemon.eco_mode_percent:
        logger.info(f"ECO MODE {repo.path.name}: Committed. Push skipped.")
        return

    # 2. Network Connectivity Check.
    remote_name = CONFIG.core.remote_name
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
                # capture=True suppresses verbose "Enumerating objects..." output.
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


def run_backup(original_path_str: str, interactive: bool = False) -> None:
    """Orchestrates the backup workflow for a single repository.

    Steps:
    1. Checks skipping conditions (load, pause, missing path).
    2. Creates a 'shadow commit' using a temporary index.
    3. Pushes the shadow commit to the remote.

    Args:
        original_path_str (str): The repository path.
        interactive (bool, optional):   Whether to run in interactive mode.
                                        Defaults to False.
    """
    repo_path = Path(original_path_str).resolve()

    # 1. Guard Clauses.
    if reason := _should_skip(repo_path, interactive):
        if reason == "Path missing":
            prune_registry(original_path_str)
        elif reason == "System under load":
            pass  # Silent skip.
        else:
            logger.info(f"SKIPPED {repo_path.name}: {reason}")
        return

    # 2. Shadow Commit Logic.
    try:
        repo = GitRepo(repo_path)
        current_branch = repo.current_branch()
        if not current_branch:
            return

        machine_id = get_machine_id()
        namespace = CONFIG.core.backup_branch
        backup_ref = f"refs/heads/{namespace}/{machine_id}/{current_branch}"

        # 3. Isolation: Use a temporary index.
        with temporary_index(repo_path) as env:
            # Stage current working directory into temp index.
            repo._run(["add", "."], env=env)

            # Write Tree.
            tree_oid = repo.write_tree(env=env)

            # Determine Parents (Synthetic Merge).
            # Parent 1: Previous backup (linear history).
            # Parent 2: Current HEAD (links to project history).
            parents = []
            if parent_backup := repo.rev_parse(backup_ref):
                parents.append(parent_backup)
            if parent_head := repo.rev_parse("HEAD"):
                parents.append(parent_head)

            # Optimization: Check if changes exist relative to the last backup.
            if parent_backup:
                prev_tree = repo._run(["rev-parse", f"{parent_backup}^{{tree}}"])
                if prev_tree == tree_oid:
                    # No changes since last backup.
                    return

            # Commit Tree.
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            commit_oid = repo.commit_tree(
                tree_oid, parents, f"Shadow backup {timestamp}", env=env
            )

            # Update Ref.
            repo.update_ref(backup_ref, commit_oid, parent_backup)

            # 4. Push.
            _attempt_push(repo, f"{backup_ref}:{backup_ref}", interactive)

    except Exception as e:
        logger.critical(f"CRITICAL {repo_path.name}: {e}")


def setup_logging(interactive: bool) -> None:
    """Configures the logging subsystem.

    Args:
        interactive (bool): If True, logs to stdout. If False, logs to file/stderr
                            with rotation enabled.
    """
    formatter = logging.Formatter(
        "[%(asctime)s] %(levelname)s: %(message)s", "%Y-%m-%d %H:%M:%S"
    )

    # Always log to stderr (captured by systemd/launchd).
    stream_handler = logging.StreamHandler(
        sys.stderr if not interactive else sys.stdout
    )
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    if not interactive:
        # In daemon mode, rotate logs to file.
        file_handler = RotatingFileHandler(
            LOG_FILE,
            maxBytes=CONFIG.limits.max_log_size,
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

    if not REGISTRY_FILE.exists():
        if interactive:
            console.print(
                "[yellow]Registry empty. Run 'git-pulsar' in "
                "a repo to register it.[/yellow]"
            )
        return

    with open(REGISTRY_FILE, "r") as f:
        repos = [line.strip() for line in f if line.strip()]

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
