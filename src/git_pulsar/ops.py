import contextlib
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from . import system
from .config import Config
from .constants import APP_NAME, BACKUP_NAMESPACE
from .git_wrapper import GitRepo, get_git_dir
from .types import (
    BackupRefInfo,
    BranchName,
    DriftState,
    GitRef,
    MachineName,
    MachineSlug,
    RemoteDriftResult,
)

console = Console()
logger = logging.getLogger(APP_NAME)


def parse_backup_ref(ref: GitRef | str) -> BackupRefInfo | None:
    """Parses a fully qualified backup reference into its components.

    Args:
        ref (GitRef | str): The git reference (e.g., 'refs/heads/wip/pulsar/mac--123/main').

    Returns:
        BackupRefInfo | None: The parsed components, or None if the ref does not match the namespace.
    """
    prefix = f"refs/heads/{BACKUP_NAMESPACE}/"
    if not ref.startswith(prefix):
        return None
    remainder = ref[len(prefix) :]
    if "/" not in remainder:
        return None
    slug, branch = remainder.split("/", 1)
    machine_name = slug.split("--", 1)[0] if "--" in slug else slug
    return BackupRefInfo(
        ref=GitRef(str(ref)),
        slug=MachineSlug(slug),
        machine_name=MachineName(machine_name),
        branch=BranchName(branch),
    )


def get_backup_ref(branch: BranchName | str) -> GitRef:
    """Constructs the fully qualified backup reference for the current machine and branch.

    Args:
        branch (BranchName | str): The name of the branch to back up.

    Returns:
        GitRef: The namespaced ref string (e.g., refs/heads/wip/pulsar/slug/branch).
    """
    slug = system.get_identity_slug()
    return GitRef(f"refs/heads/{BACKUP_NAMESPACE}/{slug}/{branch}")


def get_remote_backup_ref(
    branch: BranchName | str, remote_name: str = "origin"
) -> GitRef:
    """Constructs the remote tracking reference for the local backup branch.

    Args:
        branch (BranchName | str): The branch name.
        remote_name (str): The remote name (defaults to 'origin').

    Returns:
        GitRef: The remote ref (e.g., 'refs/remotes/origin/wip/pulsar/slug/branch').
    """
    local_ref = get_backup_ref(branch)
    suffix = local_ref.replace("refs/heads/", "", 1)
    return GitRef(f"refs/remotes/{remote_name}/{suffix}")


def fetch_backup_refs(
    repo: GitRepo, branch: BranchName | str | None = None, remote_name: str = "origin"
) -> bool:
    """Fetches backup references from the remote.

    Args:
        repo (GitRepo): The repository instance.
        branch (BranchName | str | None): If specified, fetches only backups for that branch.
        remote_name (str): The remote name (defaults to 'origin').

    Returns:
        bool: True if fetch succeeded, False if it failed (e.g. offline).
    """
    refspec_pattern = f"refs/heads/{BACKUP_NAMESPACE}/*"
    if branch:
        refspec = f"{refspec_pattern}/{branch}:{refspec_pattern}/{branch}"
    else:
        refspec = f"{refspec_pattern}:{refspec_pattern}"
    try:
        repo._run(["fetch", remote_name, refspec], capture=True)
        return True
    except Exception as e:
        logger.debug(f"Fetch failed for backup refs (branch={branch}): {e}")
        return False


def get_remote_drift_state(repo_path: Path) -> RemoteDriftResult:
    """Checks if another machine has a newer backup session for the current branch.

    Args:
        repo_path (Path): Path to the local git repository.

    Returns:
        RemoteDriftResult: A NamedTuple containing:
            - drift_detected (bool): True if divergence/drift is detected, False otherwise.
            - newest_ts (int): The Unix timestamp of the newest remote session (0 if none/error).
            - newest_machine (MachineSlug | str): The machine slug that pushed the newest session (empty if none).
            - warning (str): A human-readable warning message (empty if no drift).
    """
    try:
        repo = GitRepo(repo_path)
        current_branch = repo.current_branch()
        if not current_branch:
            return RemoteDriftResult(False, 0, MachineSlug(""), "")

        # Lightweight fetch of backup refs for the current branch
        fetch_backup_refs(repo, branch=current_branch)

        candidates = repo.list_refs(f"refs/heads/{BACKUP_NAMESPACE}/*/{current_branch}")
        if not candidates:
            return RemoteDriftResult(False, 0, MachineSlug(""), "")

        my_slug = system.get_identity_slug()
        my_backup_ref = get_backup_ref(current_branch)

        # Determine our local latest timestamp (backup ref or HEAD)
        if my_backup_ref in candidates:
            local_ts = repo.get_commit_timestamp(my_backup_ref) or 0
        else:
            local_ts = repo.get_commit_timestamp("HEAD") or 0

        newest_ts = 0
        newest_machine: MachineSlug | str = ""

        for ref in candidates:
            ts = repo.get_commit_timestamp(ref)
            if ts is not None and ts > newest_ts:
                info = parse_backup_ref(ref)
                if info:
                    newest_ts = ts
                    newest_machine = info.slug

        if newest_ts > local_ts and newest_machine and newest_machine != my_slug:
            minutes_ago = int((time.time() - newest_ts) / 60)
            warning = (
                f"Divergence Risk: '{newest_machine}' pushed a newer session "
                f"~{minutes_ago} mins ago. Consider running 'git pulsar sync'."
            )
            return RemoteDriftResult(True, newest_ts, newest_machine, warning)

    except Exception as e:
        logger.debug(f"Drift check failed: {e}")

    return RemoteDriftResult(False, 0, MachineSlug(""), "")


def is_repo_paused(repo_path: Path) -> bool:
    """Checks if the repository has backups paused by the user.

    Args:
        repo_path (Path): Path to the repository.

    Returns:
        bool: True if backups are paused, False otherwise.
    """
    return (get_git_dir(repo_path) / "pulsar_paused").exists()


def set_repo_paused(repo_path: Path, paused: bool) -> None:
    """Sets the paused state for a repository.

    Args:
        repo_path (Path): Path to the repository.
        paused (bool): True to pause backups, False to resume them.
    """
    pause_file = get_git_dir(repo_path) / "pulsar_paused"
    if paused:
        pause_file.touch()
    else:
        pause_file.unlink(missing_ok=True)


def get_drift_state(repo_path: Path) -> DriftState:
    """Retrieves the cached state for remote drift detection.

    Args:
        repo_path (Path): The path to the repository.

    Returns:
        DriftState: A NamedTuple containing:
            - last_check_ts (float): The Unix timestamp of the last time a drift check was performed.
            - warned_remote_ts (int): The Unix timestamp of the newest remote session the user was warned about.
    """
    state_file = get_git_dir(repo_path) / "pulsar_drift_state"
    if not state_file.exists():
        return DriftState(0.0, 0)

    try:
        content = state_file.read_text().strip()
        if not content:
            return DriftState(0.0, 0)

        data = json.loads(content)
        return DriftState(
            float(data.get("last_check_ts", 0.0)),
            int(data.get("warned_remote_ts", 0)),
        )
    except (OSError, ValueError, json.JSONDecodeError) as e:
        logger.debug(f"Failed to read drift state: {e}")
        return DriftState(0.0, 0)


def set_drift_state(repo_path: Path, state: DriftState) -> None:
    """Persists the drift detection state to disk atomically.

    Args:
        repo_path (Path): The path to the repository.
        state (DriftState): The drift state containing last check and warned timestamps.
    """
    state_file = get_git_dir(repo_path) / "pulsar_drift_state"
    tmp_file = state_file.with_suffix(".tmp")

    data = {
        "last_check_ts": state.last_check_ts,
        "warned_remote_ts": state.warned_remote_ts,
    }

    try:
        with open(tmp_file, "w") as f:
            json.dump(data, f)
            f.flush()
            os.fsync(f.fileno())  # Force hardware write

        # Atomic pointer swap at the filesystem level
        os.replace(tmp_file, state_file)
    except OSError as e:
        logger.debug(f"Failed to write drift state: {e}")
        if tmp_file.exists():
            with contextlib.suppress(OSError):
                tmp_file.unlink()


def restore_file(path_str: str, force: bool = False) -> None:
    """Restores a specific file from the latest backup of the current branch.

    Args:
        path_str (str): The relative path to the file to restore.
        force (bool): If True, overwrites uncommitted local changes. Defaults to False.
    """
    repo = GitRepo(Path.cwd())
    path = Path(path_str)

    current_branch = repo.current_branch()
    backup_ref = get_backup_ref(current_branch)

    # 1. Safety Check: Verify if the file is dirty.
    if not force and path.exists() and repo.status_porcelain(path_str):
        console.print(
            f"[bold yellow]WARNING:[/bold yellow] '{path_str}' has uncommitted changes."
        )

        while True:
            choice = Prompt.ask(
                "   [O]verwrite / [V]iew Diff / [C]ancel",
                choices=["o", "v", "c"],
                default="c",
            )

            if choice == "v":
                repo.run_diff(backup_ref, file=path_str)
                continue
            if choice == "c":
                console.print("[bold red]ABORTED.[/bold red]")
                sys.exit(0)
            elif choice == "o":
                break

    # 2. Restore file from backup ref.
    console.print(
        f"[bold blue]RESTORING:[/bold blue] '{path_str}' from {backup_ref}..."
    )
    try:
        repo.checkout(backup_ref, file=path_str)
        console.print("[bold green]SUCCESS:[/bold green] Restore complete.")
    except Exception as e:
        logger.error(f"Failed to restore {path_str}: {e}")
        console.print(f"[bold red]ERROR:[/bold red] Failed to restore: {e}")
        sys.exit(1)


def sync_session() -> None:
    """Synchronizes the local workspace with the latest available backup session.

    This function scans backups from all machines for the current branch, identifies
    the most recent one, and (after confirmation) resets the local working directory
    to match it. This facilitates "Smart Handoff" between devices.
    """
    config = Config.load(Path.cwd())
    if not config.daemon.sync_enabled:
        console.print(
            "[bold red]ERROR:[/bold red] Sync is disabled. Enable `sync_enabled = true` in your configuration."
        )
        sys.exit(1)

    repo = GitRepo(Path.cwd())
    current_branch = repo.current_branch()

    # 1. Fetch backups from all sources.
    with console.status(
        f"[bold blue]Scanning for session on '{current_branch}'...[/bold blue]",
        spinner="dots",
    ):
        if not fetch_backup_refs(repo, branch=current_branch):
            console.print(
                "[yellow][bold]WARNING:[/bold] Fetch warning: network might be down "
                "(checking local cache).[/yellow]"
            )

    # 2. Find candidate refs (refs/heads/{namespace}/{machine}/{branch}).
    candidates = repo.list_refs(f"refs/heads/{BACKUP_NAMESPACE}/*/{current_branch}")

    if not candidates:
        console.print("[bold red]ERROR:[/bold red] No backups found anywhere.")
        return

    # 3. Sort candidates by commit timestamp (newest first).
    latest_ref = None
    latest_time = 0

    for ref in candidates:
        ts = repo.get_commit_timestamp(ref)
        if ts is not None and ts > latest_time:
            latest_time = ts
            latest_ref = ref

    if not latest_ref:
        console.print("[bold red]ERROR:[/bold red] Could not determine latest backup.")
        return

    # 4. Compare with local state.
    parsed = parse_backup_ref(latest_ref)
    machine_name = parsed.machine_name if parsed else latest_ref
    human_time = repo.get_last_commit_time(latest_ref)

    console.print(
        Panel(
            f"[bold]Source:[/bold] {machine_name}\n[bold]Time:[/bold]   {human_time}",
            title="Latest Session Found",
            border_style="green",
            expand=False,
        )
    )

    # Check if the local tree already matches the remote tree.
    local_tree = repo.write_tree()
    remote_tree = repo._run(["rev-parse", f"{latest_ref}^{{tree}}"])

    if local_tree == remote_tree:
        console.print("[bold green]SUCCESS:[/bold green] You are already up to date.")
        return

    # 5. Confirm overwrite.
    console.print(
        "\n[bold yellow]WARNING:[/bold yellow] This will overwrite your local "
        "changes to match the backup."
    )
    confirm = console.input("   Proceed with sync? [y/N] ").lower()
    if confirm != "y":
        console.print("[bold red]ABORTED.[/bold red]")
        sys.exit(0)

    # 6. Execute sync.
    try:
        # Checkout the contents of the backup ref to the worktree without moving HEAD.
        repo._run(["checkout", latest_ref, "--", "."])
        console.print(
            "[bold green]SUCCESS:[/bold green] Session synced. You may resume work."
        )
    except Exception as e:
        logger.warning(f"Sync failed: {e}")
        console.print(f"[bold red]ERROR:[/bold red] Sync failed: {e}")
        sys.exit(1)


def finalize_work() -> None:
    """Consolidates backup streams into the main branch.

    This performs an 'Octopus Squash' merge of all backup streams for the current
    branch into the main/master branch, effectively finalizing the work session
    and updating the primary project history. Includes a pre-flight dry-run checklist.
    """
    console.print("[bold blue]FINALIZING:[/bold blue] Finalizing work...")
    repo = GitRepo(Path.cwd())

    # 1. Ensure working directory is clean.
    if repo.status_porcelain():
        console.print(
            "[bold yellow]WARNING:[/bold yellow] You have uncommitted changes."
        )
        console.print("   Please commit or stash them before finalizing.")
        sys.exit(1)

    working_branch = repo.current_branch()

    try:
        # 2. Sync with Remote.
        config = Config.load(Path.cwd())
        if config.daemon.sync_enabled:
            with console.status(
                "[bold blue]Syncing with origin...[/bold blue]", spinner="dots"
            ):
                try:
                    repo._run(["fetch", config.core.remote_name, "main"], capture=True)
                    fetch_backup_refs(repo, remote_name=config.core.remote_name)
                except Exception as e:
                    console.print(
                        f"[yellow][bold]WARNING:[/bold] Fetch warning: {e}[/yellow]"
                    )

        # 3. Identify Backup Candidates for the current branch.
        candidates = repo.list_refs(f"refs/heads/{BACKUP_NAMESPACE}/*/{working_branch}")

        if not candidates:
            console.print(
                "[bold red]ERROR:[/bold red] No backups found for this branch."
            )
            sys.exit(1)

        # 4. Resolve Target Branch.
        target = "main"
        if not repo.rev_parse("main") and repo.rev_parse("master"):
            target = "master"

        # 5. Pre-Flight Checklist.
        console.print(f"\n[bold]Pre-Flight Checklist (Target: {target})[/bold]")
        table = Table(
            show_header=True, header_style="bold magenta", border_style="blue"
        )
        table.add_column("Machine", style="cyan")
        table.add_column("Last Backup", style="dim")
        table.add_column("Files", justify="right")
        table.add_column("+", style="green", justify="right")
        table.add_column("-", style="red", justify="right")

        for c in candidates:
            parsed = parse_backup_ref(c)
            machine = parsed.machine_name if parsed else "unknown"
            try:
                rel_time = repo.get_last_commit_time(c)
            except Exception:
                rel_time = "Unknown"

            files, ins, dels = repo.diff_shortstat(target, c)
            table.add_row(machine, rel_time, str(files), str(ins), str(dels))

        console.print(table)

        if not Confirm.ask(
            f"\nSquash these {len(candidates)} streams into '{target}'?"
        ):
            console.print("[bold red]ABORTED.[/bold red] Working directory unchanged.")
            sys.exit(0)

        # 6. Switch to the target branch (main/master).
        console.print(f"-> Switching to {target}...")
        repo.checkout(target)

        # 7. Perform Octopus Squash Merge.
        with console.status(
            f"[bold blue]Collapsing {len(candidates)} backup streams...[/bold blue]",
            spinner="dots",
        ):
            try:
                repo.merge_squash(*candidates)
            except RuntimeError:
                console.print(
                    "[bold red]CONFLICT:[/bold red] Merge conflicts detected. "
                    "Please resolve them, then commit."
                )
                sys.exit(1)

        # 8. Interactive Commit.
        console.print("-> Committing (opens editor)...")
        repo.commit_interactive()

        console.print("\n[bold green]SUCCESS:[/bold green] Work finalized!")
        console.print(f"   Your backup history remains in refs/{BACKUP_NAMESPACE}/...")

    except Exception as e:
        logger.error(f"Finalize failed: {e}")
        console.print(f"\n[bold red]ERROR:[/bold red] Error during finalize: {e}")
        sys.exit(1)


def prune_backups(days: int, repo_path: Path | None = None) -> None:
    """Garbage collects backup references older than the specified retention period.

    Args:
        days (int): The retention period in days.
        repo_path (Path | None, optional): The path to the repository. Defaults to CWD.
    """
    repo = GitRepo(repo_path or Path.cwd())
    cutoff = time.time() - (days * 86400)

    console.print(
        f"[bold blue]MAINTENANCE:[/bold blue] "
        f"Scanning for backups older than {days} days..."
    )

    refs = repo.list_refs(f"refs/heads/{BACKUP_NAMESPACE}/")
    deleted_count = 0

    for ref in refs:
        try:
            ts = repo.get_commit_timestamp(ref)
            if ts is not None and ts < cutoff:
                age_days = (time.time() - ts) / 86400
                console.print(f"   Deleting {ref} (Age: {age_days:.1f} days)")
                repo._run(["update-ref", "-d", ref], capture=False)
                deleted_count += 1
        except Exception as e:
            logger.warning(f"Failed to process old backup ref '{ref}': {e}")
            continue

    if deleted_count == 0:
        console.print("[dim]No stale backups found.[/dim]")
    else:
        console.print(f"[bold red]Dropped {deleted_count} stale refs.[/bold red]")
        with console.status(
            "[bold blue]Running garbage collection (git gc)...[/bold blue]",
            spinner="dots",
        ):
            repo._run(["gc", "--auto"], capture=True)


def add_ignore(pattern: str) -> None:
    """Adds a file pattern to .gitignore and removes matching files from the index.

    If files matching the pattern are currently tracked, the user is prompted to
    stop tracking them (while keeping the files on disk).
    Respects the config.files.manage_gitignore flag.

    Args:
        pattern (str): The file pattern to ignore (e.g., '*.log').
    """
    cwd = Path.cwd()
    config = Config.load(cwd)
    gitignore = cwd / ".gitignore"

    # 1. Append to .gitignore if not present (and allowed by config).
    if config.files.manage_gitignore:
        content = ""
        if gitignore.exists():
            with open(gitignore) as f:
                content = f.read()

        existing_lines = {line.strip() for line in content.splitlines()}
        if pattern.strip() in existing_lines:
            console.print(f"[blue]INFO:[/blue] '{pattern}' is already in .gitignore.")
        else:
            with open(gitignore, "a") as f:
                prefix = "\n" if content and not content.endswith("\n") else ""
                f.write(f"{prefix}{pattern}\n")
            console.print(
                f"[bold green]SUCCESS:[/bold green] Added '{pattern}' to .gitignore."
            )
    else:
        console.print(
            f"[dim]INFO: Skipping .gitignore update for '{pattern}' (manage_gitignore=false).[/dim]"
        )

    # 2. Check if currently tracked and offer to remove from index.
    repo = GitRepo(cwd)
    try:
        tracked = repo._run(["ls-files", pattern])
        if tracked:
            console.print(
                f"[bold yellow]WARNING:[/bold yellow] "
                f"Files matching '{pattern}' are currently tracked by git."
            )
            confirm = console.input(
                "   Stop tracking them (keep local file)? [y/N] "
            ).lower()
            if confirm == "y":
                repo._run(["rm", "--cached", pattern], capture=False)
                console.print("   Removed from index (file preserved on disk).")
    except Exception as e:
        logger.warning(f"Failed to remove tracked files: {e}")
        pass


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
                system.get_system().notify(
                    "Backup Aborted", f"File >{limit_mb}MB detected: {name}"
                )
                return True
        except OSError as e:
            logger.warning(f"Failed to check size of file {name}: {e}")
            continue

    return False
