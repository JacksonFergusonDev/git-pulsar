import argparse
import datetime
import logging
import os
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm
from rich.table import Table
from rich.text import Text

from . import daemon, ops, service, system
from .config import CONFIG_FILE, Config
from .constants import (
    APP_NAME,
    BACKUP_NAMESPACE,
    DEFAULT_IGNORES,
    LOG_FILE,
    PID_FILE,
    REGISTRY_FILE,
)
from .git_wrapper import GitRepo

logger = logging.getLogger(APP_NAME)
console = Console()


@dataclass
class DoctorAction:
    """Represents an actionable resolution for an issue detected by the doctor.

    Attributes:
        description (str): A brief summary of the issue to be resolved.
        prompt (str): The yes/no question presented to the user.
        action_callable (Callable[[], bool]): The function to execute if the user
            confirms. Must return True if successful, False otherwise.
    """

    description: str
    prompt: str
    action_callable: Callable[[], bool]


def _get_ref(repo: GitRepo) -> str:
    """Resolves the namespaced backup reference for the current repository state.

    Args:
        repo (GitRepo): The repository instance to analyze.

    Returns:
        str: The fully qualified backup reference string based on the current branch.
    """
    return ops.get_backup_ref(repo.current_branch())


def _analyze_logs(seconds: int = 86400) -> list[str]:
    """
    Scans the daemon log for error messages that occurred within a recent time window.

    Args:
        seconds (int, optional): The number of seconds to look back. Defaults to 86400 (24h).

    Returns:
        list[str]: A list of error or critical log lines found within the time window.
    """
    if not LOG_FILE.exists():
        return []

    errors = []
    threshold = datetime.datetime.now() - datetime.timedelta(seconds=seconds)

    try:
        # Read the last 50KB of the log file
        # to capture recent context without parsing the whole file.
        file_size = LOG_FILE.stat().st_size
        read_size = min(file_size, 50 * 1024)

        with open(LOG_FILE) as f:
            if file_size > read_size:
                f.seek(file_size - read_size)
            lines = f.readlines()

        for line in lines:
            if "ERROR" in line or "CRITICAL" in line:
                # Attempt to parse the timestamp [YYYY-MM-DD HH:MM:SS].
                # If parsing fails,
                # assume it is a related traceback line and include it.
                try:
                    if line.startswith("["):
                        ts_str = line[1:20]
                        line_dt = datetime.datetime.strptime(
                            ts_str, "%Y-%m-%d %H:%M:%S"
                        )
                        if line_dt < threshold:
                            continue  # Skip errors older than the threshold.
                    errors.append(line.strip())
                except ValueError:
                    pass
    except Exception as e:
        return [f"Error reading log file: {e}"]

    return errors


def _check_repo_health(path: Path, config: Config) -> str | None:
    """Evaluates the health of a repository, checking for stale backups or stalled states.

    Args:
        path (Path): The file system path to the repository.
        config (Config): The configuration instance for the repository.

    Returns:
        str | None: A warning message if an issue is detected,
                    or None if the repository is healthy.
    """
    try:
        repo = GitRepo(path)
        # Check if the repository is explicitly paused.
        if (path / ".git" / "pulsar_paused").exists():
            return None

        # If the working directory is clean, no backup is required.
        if not repo.status_porcelain():
            return None

        # Verify the freshness of the last backup.
        ref = _get_ref(repo)
        try:
            # Retrieve the raw Unix timestamp of the backup reference.
            ts_str = repo._run(["log", "-1", "--format=%ct", ref])
            last_backup_ts = int(ts_str.strip())
        except Exception as e:
            logger.debug(f"Failed to retrieve backup timestamp for {path.name}: {e}")
            return f"Has changes, but NO backup found. (Error: {e})"

        # Check against the dynamic stale threshold (2x commit interval).
        # If changes are pending and no backup has occurred recently,
        # the daemon may be stalled.
        stale_threshold = config.daemon.commit_interval * 2
        if time.time() - last_backup_ts > stale_threshold:
            return f"Stalled: Changes pending > {stale_threshold // 60} mins."

    except Exception as e:
        return f"Unable to verify git status: {e}"

    return None


def open_config() -> None:
    """Opens the global configuration file in the system default editor."""
    if not CONFIG_FILE.exists():
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_FILE, "w") as f:
            f.write(
                "# Git Pulsar Configuration\n\n"
                "[daemon]\n"
                "# Options: paranoid, aggressive, balanced, lazy\n"
                '# preset = "balanced"\n'
            )

    editor = os.environ.get("EDITOR")
    if not editor:
        if sys.platform == "darwin":
            editor = "open"
        else:
            editor = "nano"

    console.print(f"Opening [cyan]{CONFIG_FILE}[/cyan]...")

    try:
        if editor == "open":
            subprocess.run(["open", str(CONFIG_FILE)])
        else:
            subprocess.run([editor, str(CONFIG_FILE)])
    except Exception as e:
        console.print(f"[red]Could not open editor: {e}[/red]")


def show_status() -> None:
    """Displays the current status of the daemon and the active repository."""
    # Check daemon process status.
    pid_running = False
    if PID_FILE.exists():
        try:
            with open(PID_FILE) as f:
                pid = int(f.read().strip())
            os.kill(pid, 0)
            pid_running = True
        except (ValueError, OSError):
            pid_running = False

    # Check if the system service is scheduled/enabled.
    service_enabled = service.is_service_enabled()

    if pid_running:
        status_text = "Active (Running)"
        status_style = "bold green"
    elif service_enabled:
        status_text = "Active (Idle)"
        status_style = "green"
    else:
        status_text = "Stopped"
        status_style = "bold red"

    system_content = Text()
    system_content.append("Daemon: ", style="bold")
    system_content.append(status_text + "\n", style=status_style)

    # --- Power Telemetry Integration ---
    conf = Config.load()
    sys_strat = system.get_system()
    pct, plugged = sys_strat.get_battery()

    system_content.append("Power:  ", style="bold")
    if plugged:
        system_content.append("AC (Unrestricted)", style="green")
    elif pct < conf.daemon.min_battery_percent:
        system_content.append(
            f"Critical {pct}% (All Backups Suspended)", style="bold red"
        )
    elif pct < conf.daemon.eco_mode_percent:
        system_content.append(
            f"Eco-Mode {pct}% (Pushes Suspended)", style="bold yellow"
        )
    else:
        system_content.append(f"Battery {pct}% (Normal)", style="green")

    console.print(Panel(system_content, title="System Status", expand=False))

    # Display status for the current repository, if applicable.
    if Path(".git").exists():
        cwd = Path.cwd()

        # Check registration status.
        is_registered = False
        # Use the system helper (returns list[Path])
        if cwd in system.get_registered_repos():
            is_registered = True

        if not is_registered:
            console.print(
                Panel(
                    "This repository is not tracked by Git Pulsar.\n"
                    "Run [bold cyan]git pulsar[/bold cyan] to enable backups.",
                    title="Repository Status",
                    expand=False,
                    border_style="yellow",
                )
            )
            return

        repo = GitRepo(cwd)
        conf = Config.load(cwd)

        # Resolve Refs
        ref = _get_ref(repo)
        ref_name = ref.replace("refs/heads/", "")
        remote_ref = f"refs/remotes/{conf.core.remote_name}/{ref_name}"

        # Get Commit Time
        try:
            commit_ts = repo._run(["log", "-1", "--format=%ct", ref]).strip()
            last_commit_time = datetime.datetime.fromtimestamp(int(commit_ts))
            commit_str = last_commit_time.strftime("%Y-%m-%d %H:%M")
        except Exception as e:
            logger.debug(f"Failed to retrieve last commit time for {ref}: {e}")
            commit_str = "Never"

        # Get Push Time
        try:
            push_ts = repo._run(["log", "-1", "--format=%ct", remote_ref]).strip()
            last_push_time = datetime.datetime.fromtimestamp(int(push_ts))
            push_str = last_push_time.strftime("%Y-%m-%d %H:%M")
        except Exception as e:
            logger.debug(f"Failed to retrieve last push time for {remote_ref}: {e}")
            push_str = "Never"

        count = len(repo.status_porcelain())
        is_paused = (cwd / ".git" / "pulsar_paused").exists()

        repo_content = Text()
        repo_content.append(f"Last Commit: {commit_str}\n")
        repo_content.append(f"Last Push:   {push_str}\n", style="dim")
        repo_content.append(f"Pending:     {count} files changed\n")

        if is_paused:
            repo_content.append("Mode:        PAUSED", style="bold yellow")
        else:
            repo_content.append("Mode:        Active", style="green")

        # --- 1. Health Integration ---
        health_warning = None
        if ops.has_large_files(cwd, conf):
            limit_mb = int(conf.limits.large_file_threshold / (1024 * 1024))
            health_warning = (
                f"Daemon stalled.\n  File >{limit_mb}MB detected. "
                "Run 'git pulsar doctor' for details."
            )
        else:
            health_warning = _check_repo_health(cwd, conf)

        if health_warning:
            repo_content.append("\n\n⚠ WARNING: ", style="bold yellow")
            repo_content.append(health_warning, style="yellow")

        console.print(Panel(repo_content, title="Repository Status", expand=False))

        # --- 2. Roaming Radar Integration (Cached) ---
        _, warned_ts = ops.get_drift_state(cwd)

        # Ensure we only warn if the cached remote timestamp is strictly newer
        # than our local backup reference.
        if warned_ts > 0 and commit_str != "Never" and warned_ts > int(commit_ts):
            minutes_ago = int((time.time() - warned_ts) / 60)
            drift_content = Text()
            drift_content.append(
                "⚠ A remote machine pushed a newer session\n", style="bold yellow"
            )
            drift_content.append(
                f"  ~{minutes_ago} mins ago. Run 'git pulsar sync'", style="yellow"
            )
            console.print(
                Panel(
                    drift_content,
                    title="Session Drift",
                    border_style="yellow",
                    expand=False,
                )
            )

    # Display global repository count if not currently in a repository.
    elif REGISTRY_FILE.exists():
        count = len(system.get_registered_repos())
        console.print(f"[dim]Watching {count} repositories.[/dim]")


def show_diff() -> None:
    """Displays the diff between the working directory and the last backup."""
    if not Path(".git").exists():
        console.print("[bold red]Not a git repository.[/bold red]")
        sys.exit(1)

    repo = GitRepo(Path.cwd())

    # Display standard diff for tracked files.
    ref = _get_ref(repo)

    console.print(f"[bold]Diff vs {ref}:[/bold]\n")
    repo.run_diff(ref)

    # List untracked files.
    if untracked := repo.get_untracked_files():
        console.print("\n[bold green]Untracked (New) Files:[/bold green]")
        for line in untracked:
            console.print(f"   + {line}", style="green")


def list_repos() -> None:
    """Lists all repositories currently registered with Git Pulsar and their status."""
    if not REGISTRY_FILE.exists():
        console.print("[yellow]Registry is empty.[/yellow]")
        return

    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Repository", style="cyan")
    table.add_column("Status")
    table.add_column("Last Backup", justify="right", style="dim")

    repos = system.get_registered_repos()

    for path in repos:
        display_path = str(path).replace(str(Path.home()), "~")

        status_text = "Unknown"
        status_style = "white"
        last_backup = "-"

        if not path.exists():
            status_text = "Missing"
            status_style = "red"
        else:
            if (path / ".git" / "pulsar_paused").exists():
                status_text = "Paused"
                status_style = "yellow"
            else:
                status_text = "Active"
                status_style = "green"

            try:
                r = GitRepo(path)
                ref = _get_ref(r)
                last_backup = r.get_last_commit_time(ref)
            except Exception as e:
                logger.debug(f"Failed to retrieve backup info for {path}: {e}")
                if status_text == "Active":
                    try:
                        GitRepo(path)
                    except Exception as inner_e:
                        logger.debug(f"Repo instantiation failed for {path}: {inner_e}")
                        status_text = "Error"
                        status_style = "bold red"

        table.add_row(
            display_path, f"[{status_style}]{status_text}[/{status_style}]", last_backup
        )

    console.print(table)


def unregister_repo() -> None:
    """Removes the current working directory from the Git Pulsar registry."""
    cwd = str(Path.cwd())
    if not REGISTRY_FILE.exists():
        console.print("Registry is empty.", style="yellow")
        return

    current_paths = [str(p) for p in system.get_registered_repos()]
    if cwd not in current_paths:
        console.print(
            f"Current path not registered: [cyan]{cwd}[/cyan]", style="yellow"
        )
        return

    with open(REGISTRY_FILE, "w") as f:
        for path in current_paths:
            if path != cwd:
                f.write(f"{path}\n")
    console.print(f"✔ Unregistered: [cyan]{cwd}[/cyan]", style="green")


def _check_systemd_linger() -> str | None:
    """Checks if systemd linger is enabled for the current Linux user.

    Returns:
        str | None: A warning message if linger is disabled, or None if
                    enabled, or if the system is not Linux.
    """
    if not sys.platform.startswith("linux"):
        return None

    user = os.environ.get("USER")
    if not user:
        return None

    try:
        res = subprocess.run(
            ["loginctl", "show-user", user, "-p", "Linger"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if "Linger=yes" not in res.stdout:
            return (
                "systemd 'linger' is disabled. Daemon will die when you log out. "
                "Run 'loginctl enable-linger' to fix."
            )
    except Exception as e:
        logger.debug(f"Failed to check systemd linger status: {e}")

    return None


def _check_git_hooks(repo_path: Path) -> list[str]:
    """Scans the repository for executable git hooks that might block the daemon.

    Args:
        repo_path (Path): Path to the local git repository.

    Returns:
        list[str]: A list of warning messages regarding potentially blocking hooks.
    """
    warnings: list[str] = []
    hooks_dir = repo_path / ".git" / "hooks"

    if not hooks_dir.exists():
        return warnings

    for hook in ["pre-commit", "pre-push"]:
        hook_path = hooks_dir / hook
        if hook_path.exists() and os.access(hook_path, os.X_OK):
            try:
                content = hook_path.read_text(errors="ignore")
                if "pulsar" not in content.lower():
                    warnings.append(
                        f"Strict '{hook}' hook detected.\n"
                        f"     [dim]Action required: Append this line near the top of your hook to bypass it for backups:\n"
                        f'     if [[ $GIT_REFLOG_ACTION == *"{BACKUP_NAMESPACE}"* ]]; then exit 0; fi[/dim]'
                    )
            except Exception as e:
                logger.debug(f"Failed to read {hook} hook for {repo_path.name}: {e}")

    return warnings


def run_doctor() -> None:
    """
    Diagnoses system health, cleans the registry, and checks connectivity and logs.
    Includes an interactive resolution queue for safe auto-fixes.
    """
    console.print("[bold]Pulsar Doctor[/bold]\n")

    actions: list[DoctorAction] = []

    # Verify and clean the registry.
    with console.status("[bold blue]Checking Registry...", spinner="dots"):
        repos = system.get_registered_repos()
        if not repos and not REGISTRY_FILE.exists():
            console.print("   [green]✔ Registry empty/clean.[/green]")
        else:
            valid_lines = []
            missing_paths = []
            for p in repos:
                if p.exists():
                    valid_lines.append(str(p))
                else:
                    missing_paths.append(str(p))

            if missing_paths:
                console.print(
                    f"   [yellow]⚠ Found {len(missing_paths)} missing registry entries.[/yellow]"
                )

                def clean_registry() -> bool:
                    try:
                        with open(REGISTRY_FILE, "w") as f:
                            f.write("\n".join(valid_lines) + "\n")
                        return True
                    except Exception as e:
                        logger.error(f"Registry cleanup failed: {e}")
                        return False

                actions.append(
                    DoctorAction(
                        description=f"Remove {len(missing_paths)} ghost entries from registry",
                        prompt=f"Found {len(missing_paths)} missing paths. Remove from registry?",
                        action_callable=clean_registry,
                    )
                )
            else:
                console.print("   [green]✔ Registry healthy.[/green]")

    # Check daemon status.
    with console.status("[bold blue]Checking Daemon...", spinner="dots"):
        if service.is_service_enabled():
            console.print("   [green]✔ Daemon is active.[/green]")

            # Sub-check: Systemd Linger on Linux
            if linger_warning := _check_systemd_linger():
                console.print(f"   [yellow]⚠ {linger_warning}[/yellow]")

                def enable_linger() -> bool:
                    try:
                        user = os.environ.get("USER")
                        if not user:
                            return False
                        subprocess.run(["loginctl", "enable-linger", user], check=True)
                        return True
                    except Exception as e:
                        logger.error(f"Failed to enable linger: {e}")
                        return False

                actions.append(
                    DoctorAction(
                        description="Enable systemd user linger",
                        prompt="Enable background lingering? (Runs: loginctl enable-linger $USER)",
                        action_callable=enable_linger,
                    )
                )
        else:
            console.print("   [red]✘ Daemon is STOPPED.[/red]")

            def install_daemon() -> bool:
                try:
                    service.install(interval=900)
                    return True
                except Exception as e:
                    logger.error(f"Failed to install daemon: {e}")
                    return False

            actions.append(
                DoctorAction(
                    description="Install and start the background daemon",
                    prompt="Daemon is stopped. Install the background service?",
                    action_callable=install_daemon,
                )
            )

    # Check network/SSH connectivity.
    with console.status("[bold blue]Checking Connectivity...", spinner="dots"):
        try:
            res = subprocess.run(
                ["ssh", "-T", "git@github.com"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if "successfully authenticated" in res.stderr:
                console.print("   [green]✔ GitHub SSH connection successful.[/green]")
            else:
                console.print(
                    "   [yellow]⚠ GitHub SSH check returned "
                    "unexpected response.[/yellow]\n"
                    "     [dim]Action required: Verify SSH key configuration or remote permissions.[/dim]"
                )

        except Exception as e:
            console.print(f"   [red]✘ SSH Check failed: {e}[/red]")
            console.print(
                "     [dim]Action required: Check your network connection or run 'ssh-add' to load your keys.[/dim]"
            )

    # Check for remote session drift (if currently in a registered repository).
    cwd = Path.cwd()
    if (cwd / ".git").exists() and cwd in system.get_registered_repos():
        with console.status(
            "[bold blue]Checking Remote Session Drift...", spinner="dots"
        ):
            drift_detected, _, _, drift_warning = ops.get_remote_drift_state(cwd)
            if drift_detected:
                console.print(f"   [yellow]⚠ {drift_warning}[/yellow]")

                def sync_drift() -> bool:
                    try:
                        # ops.sync_session handles its own UI and uses sys.exit
                        ops.sync_session()
                        return True
                    except SystemExit as e:
                        return e.code == 0
                    except Exception as e:
                        logger.error(f"Failed to sync session: {e}")
                        return False

                actions.append(
                    DoctorAction(
                        description="Sync local session with remote",
                        prompt="Run 'git pulsar sync' to reconcile remote session?",
                        action_callable=sync_drift,
                    )
                )
            else:
                console.print(
                    "   [green]✔ Local session is up-to-date with remote.[/green]"
                )

    # Perform diagnostics on logs and repository freshness.
    console.print("\n[bold]Diagnostics[/bold]")

    # 1. Check the health of registered repositories (State Check + Hook Interference).
    is_healthy = True
    with console.status("[bold blue]Checking Repository Health...", spinner="dots"):
        if REGISTRY_FILE.exists():
            with open(REGISTRY_FILE) as f:
                paths = [Path(line.strip()) for line in f if line.strip()]

            issues = []
            for p in paths:
                if p.exists():
                    repo_config = Config.load(p)

                    # 1a. Check for paused state
                    pause_file = p / ".git" / "pulsar_paused"
                    if pause_file.exists():
                        issues.append(f"{p.name}: Repository is explicitly paused.")

                        def resume_repo(path_to_unpause: Path = pause_file) -> bool:
                            try:
                                path_to_unpause.unlink(missing_ok=True)
                                return True
                            except OSError as e:
                                logger.error(f"Failed to resume {path_to_unpause}: {e}")
                                return False

                        actions.append(
                            DoctorAction(
                                description=f"Resume backups for {p.name}",
                                prompt=f"Resume backups for {p.name}?",
                                action_callable=resume_repo,
                            )
                        )

                    # 1b. Check for stale index lock
                    lock_file = p / ".git" / "index.lock"
                    if lock_file.exists():
                        try:
                            mtime = lock_file.stat().st_mtime
                            age_hours = (time.time() - mtime) / 3600
                            if age_hours > 2:
                                issues.append(
                                    f"{p.name}: Stale index lock found ({age_hours:.1f}h old)."
                                )

                                def remove_lock(path_to_lock: Path = lock_file) -> bool:
                                    try:
                                        path_to_lock.unlink(missing_ok=True)
                                        return True
                                    except OSError as e:
                                        logger.error(
                                            f"Failed to remove lock at {path_to_lock}: {e}"
                                        )
                                        return False

                                actions.append(
                                    DoctorAction(
                                        description=f"Remove stale index lock in {p.name}",
                                        prompt=f"Stale index lock found in {p.name} ({age_hours:.1f}h old). Remove it?",
                                        action_callable=remove_lock,
                                    )
                                )
                        except OSError:
                            pass  # Lock file vanished during read (race resolved)

                    # 1c. Large file check (Manual Intervention)
                    if ops.has_large_files(p, repo_config):
                        limit_mb = int(
                            repo_config.limits.large_file_threshold / (1024 * 1024)
                        )
                        issues.append(
                            f"{p.name}: File >{limit_mb}MB detected, blocking backups.\n"
                            f"     [dim]Action required: Untrack the file or run 'git pulsar ignore <filename>'[/dim]"
                        )

                    # 1d. Standard health check
                    if problem := _check_repo_health(p, repo_config):
                        issues.append(f"{p.name}: {problem}")

                    for hook_warning in _check_git_hooks(p):
                        issues.append(f"{p.name} (Hook): {hook_warning}")

            if issues:
                is_healthy = False
                console.print(
                    f"   [yellow]⚠ Found {len(issues)} repository issue(s):[/yellow]"
                )
                for issue in issues:
                    console.print(f"     - {issue}")
                console.print(
                    "     [dim](Ensure daemon is running and review required actions above)[/dim]"
                )
            else:
                console.print(
                    "   [green]✔ All repositories are healthy "
                    "(clean or backed up).[/green]"
                )

    # 2. Check logs for recent errors using dynamic window (Event Check).
    conf = Config.load()
    lookback_secs = conf.daemon.push_interval * 3
    recent_errors = _analyze_logs(seconds=lookback_secs)

    # 3. Correlate State and Events
    if recent_errors:
        lookback_hours = lookback_secs // 3600
        time_str = f"{lookback_hours}h" if lookback_hours > 0 else f"{lookback_secs}s"

        if is_healthy:
            console.print(
                f"   [dim]ℹ {len(recent_errors)} transient error(s) logged in the last "
                f"{time_str}, but system automatically recovered.[/dim]"
            )
        else:
            console.print(
                f"   [red]✘ Found {len(recent_errors)} active error(s) in the last "
                f"{time_str}:[/red]"
            )
            for err in recent_errors[-3:]:  # Show last 3
                console.print(f"     [dim]{err}[/dim]")
            if len(recent_errors) > 3:
                console.print("     ... (run 'git pulsar log' to see full history)")
    else:
        console.print("   [green]✔ Recent logs are clean.[/green]")

    # --- Interactive Resolution Phase ---
    if actions:
        console.print("\n[bold]Interactive Resolutions[/bold]")
        for action in actions:
            if Confirm.ask(f"   {action.prompt}"):
                try:
                    if action.action_callable():
                        console.print(
                            f"   [green]✔ Resolved:[/green] {action.description}"
                        )
                    else:
                        console.print(
                            f"   [red]✘ Failed to resolve:[/red] {action.description}"
                        )
                except Exception as e:
                    console.print(
                        f"   [red]✘ Error resolving {action.description}:[/red] {e}"
                    )
            else:
                console.print("   [dim]Skipped.[/dim]")

    # 2. Check logs for recent errors using dynamic window (Event Check).
    conf = Config.load()
    lookback_secs = conf.daemon.push_interval * 3
    recent_errors = _analyze_logs(seconds=lookback_secs)

    # 3. Correlate State and Events
    if recent_errors:
        lookback_hours = lookback_secs // 3600
        time_str = f"{lookback_hours}h" if lookback_hours > 0 else f"{lookback_secs}s"

        if is_healthy:
            console.print(
                f"   [dim]ℹ {len(recent_errors)} transient error(s) logged in the last "
                f"{time_str}, but system automatically recovered.[/dim]"
            )
        else:
            console.print(
                f"   [red]✘ Found {len(recent_errors)} active error(s) in the last "
                f"{time_str}:[/red]"
            )
            for err in recent_errors[-3:]:  # Show last 3
                console.print(f"     [dim]{err}[/dim]")
            if len(recent_errors) > 3:
                console.print("     ... (run 'git pulsar log' to see full history)")
    else:
        console.print("   [green]✔ Recent logs are clean.[/green]")


def add_ignore_cli(pattern: str) -> None:
    """Adds a file pattern to the repository's ignore list.

    Args:
        pattern (str): The file pattern to ignore (e.g., '*.log').
    """
    if not Path(".git").exists():
        console.print("[bold red]Not a git repository.[/bold red]")
        return
    ops.add_ignore(pattern)


def tail_log() -> None:
    """Follows the daemon log file in real-time."""
    if not LOG_FILE.exists():
        console.print(f"[red]No log file found yet at {LOG_FILE}.[/red]")
        return

    console.print(f"Tailing [bold cyan]{LOG_FILE}[/bold cyan] (Ctrl+C to stop)...")
    try:
        subprocess.run(["tail", "-n", "1000", "-f", str(LOG_FILE)])
    except KeyboardInterrupt:
        console.print("\nStopped.", style="dim")


def set_pause_state(paused: bool) -> None:
    """Toggles the backup state for the current repository.

    Args:
        paused (bool): True to pause backups, False to resume them.
    """
    if not Path(".git").exists():
        console.print("[bold red]Not a git repository.[/bold red]")
        sys.exit(1)

    pause_file = Path(".git/pulsar_paused")
    if paused:
        pause_file.touch()
        console.print(
            "Pulsar paused. Backups suspended for this repo.", style="bold yellow"
        )
    else:
        if pause_file.exists():
            pause_file.unlink()
        console.print("Pulsar resumed. Backups active.", style="bold green")


def setup_repo(registry_path: Path = REGISTRY_FILE) -> None:
    """Initializes Git Pulsar for the current repository and registers it.

    This ensures the directory is a git repository, sets up a default .gitignore,
    and adds the path to the global registry.

    Args:
        registry_path (Path, optional): Path to the registry file.
                                        Defaults to REGISTRY_FILE.
    """
    cwd = Path.cwd()
    config = Config.load(cwd)

    # Ensure the directory is a git repository.
    if not (cwd / ".git").exists():
        console.print(
            f"[bold blue]Git Pulsar:[/bold blue] activating "
            f"for [cyan]{cwd.name}[/cyan]..."
        )
        subprocess.run(["git", "init"], check=True)

    repo = GitRepo(cwd)

    # Trigger Identity Configuration (with Sync)
    system.configure_identity(repo)

    # Ensure a .gitignore file exists and contains default patterns.
    if config.files.manage_gitignore:
        gitignore = cwd / ".gitignore"

        if not gitignore.exists():
            console.print("[dim]Creating basic .gitignore...[/dim]")
            with open(gitignore, "w") as f:
                f.write("\n".join(DEFAULT_IGNORES) + "\n")
        else:
            console.print(
                "Existing .gitignore found. Checking for missing defaults...",
                style="dim",
            )
            with open(gitignore) as f:
                existing_content = f.read()

            missing_defaults = [d for d in DEFAULT_IGNORES if d not in existing_content]

            if missing_defaults:
                console.print(
                    f"Appending {len(missing_defaults)} missing ignores...", style="dim"
                )
                with open(gitignore, "a") as f:
                    f.write("\n" + "\n".join(missing_defaults) + "\n")
            else:
                console.print("All defaults present.", style="dim")
    else:
        console.print(
            "Skipping .gitignore management (manage_gitignore=false).", style="dim"
        )

    # Register the repository path.
    console.print("Registering path...", style="dim")
    if not registry_path.exists():
        registry_path.touch()

    with open(registry_path, "r+") as f:
        content = f.read()
        if str(cwd) not in content:
            f.write(f"{cwd}\n")
            console.print(f"Registered: [cyan]{cwd}[/cyan]", style="green")
        else:
            console.print("Already registered.", style="dim")

    console.print("\n[bold green]✔ Pulsar Active.[/bold green]")

    try:
        remotes = repo._run(["remote"])
        if remotes:
            console.print("Verifying git access...", style="dim")
            repo._run(["push", "--dry-run"], capture=False)
    except Exception as e:
        logger.debug(f"Dry-run push verification failed: {e}")
        console.print(
            f"⚠ WARNING: Git push failed. Ensure you have "
            f"SSH keys set up or credentials cached.\n"
            f"[dim]Diagnostic info: {e}[/dim]",
            style="bold yellow",
        )


class PulsarHelpFormatter(argparse.HelpFormatter):
    """Custom help formatter to streamline the CLI help output.

    This formatter intercepts the subparser action, strips the default
    metavar block, and groups the subcommands into logical categories
    with custom headers.
    """

    def _format_action(self, action: argparse.Action) -> str:
        if isinstance(action, argparse._SubParsersAction):
            parts = []

            # Define logical command clusters based on the project structure
            groups = {
                "Backup Management": ["now", "sync", "restore", "diff", "finalize"],
                "Repository Control": [
                    "status",
                    "config",
                    "list",
                    "pause",
                    "resume",
                    "remove",
                    "ignore",
                ],
                "Maintenance": ["doctor", "prune", "log"],
                "Service": ["install-service", "uninstall-service"],
                "General": ["help"],
            }

            subactions = list(self._iter_indented_subactions(action))

            for group_name, commands in groups.items():
                # Filter the standard argparse subactions into our defined groups
                group_actions = [a for a in subactions if a.dest in commands]
                if not group_actions:
                    continue

                # Inject the group header with standard argparse indentation
                parts.append(f"\n  {group_name}:\n")

                # Render the commands belonging to this group
                self._indent()
                for subaction in group_actions:
                    parts.append(self._format_action(subaction))
                self._dedent()

            return self._join_parts(parts)

        return super()._format_action(action)


def show_config_reference() -> None:
    """Displays a formatted table of all available configuration options."""
    from rich.table import Table

    table = Table(title="Git Pulsar Configuration Schema", show_lines=True)
    table.add_column("Section", style="cyan", justify="right")
    table.add_column("Key", style="green")
    table.add_column("Type", style="dim")
    table.add_column("Default", style="yellow")
    table.add_column("Description")

    # Core Settings
    table.add_row(
        "core",
        "backup_branch",
        "str",
        '"wip/pulsar"',
        "The Git namespace used for shadow commits.",
    )
    table.add_row(
        "", "remote_name", "str", '"origin"', "The remote target for pushing backups."
    )

    # Daemon Settings
    table.add_row(
        "daemon",
        "preset",
        "str",
        "None",
        "Interval preset: 'paranoid', 'aggressive', 'balanced', or 'lazy'.",
    )
    table.add_row(
        "",
        "commit_interval",
        "int | str",
        '"10m"',
        "Time between local state captures (e.g., '10m', '1hr', 600).",
    )
    table.add_row(
        "",
        "push_interval",
        "int | str",
        '"1hr"',
        "Time between remote pushes (e.g., '1hr', '30m', 3600).",
    )
    table.add_row(
        "",
        "min_battery_percent",
        "int",
        "10",
        "Stops all daemon activity if battery drops below this.",
    )
    table.add_row(
        "",
        "eco_mode_percent",
        "int",
        "20",
        "Suspends remote pushes if battery drops below this.",
    )

    # Files Settings
    table.add_row(
        "files", "ignore", "list", "[]", "Extra glob patterns to append to .gitignore."
    )
    table.add_row(
        "",
        "manage_gitignore",
        "bool",
        "true",
        "Allow daemon to automatically add rules to .gitignore.",
    )

    # Limits Settings
    table.add_row(
        "limits",
        "max_log_size",
        "int | str",
        '"5mb"',
        "Max size for log files before rotation (e.g., '5mb', '1gb').",
    )
    table.add_row(
        "",
        "large_file_threshold",
        "int | str",
        '"100mb"',
        "Max file size before aborting a backup (e.g., '100mb', '2gb').",
    )

    console.print(table)


def main() -> None:
    """Main entry point for the Git Pulsar CLI."""
    parser = argparse.ArgumentParser(
        usage=argparse.SUPPRESS,
        formatter_class=PulsarHelpFormatter,
        add_help=False,  # Disable the default help injection
    )

    # Manually re-add the help flags but suppress them from the visual output
    parser.add_argument(
        "-h",
        "--help",
        action="help",
        default=argparse.SUPPRESS,
        help=argparse.SUPPRESS,
    )

    # Global flags

    subparsers = parser.add_subparsers(
        dest="command",
    )

    # Subcommands
    install_parser = subparsers.add_parser(
        "install-service", help="Install the background daemon"
    )
    install_parser.add_argument(
        "--interval",
        type=int,
        default=900,
        help="Backup interval in seconds (default: 900)",
    )
    subparsers.add_parser("uninstall-service", help="Uninstall the background daemon")
    subparsers.add_parser("now", help="Run backup immediately (one-off)")

    # Restore Command
    restore_parser = subparsers.add_parser(
        "restore", help="Restore a file from the backup branch"
    )
    restore_parser.add_argument("path", help="Path to the file to restore")
    restore_parser.add_argument(
        "--force", "-f", action="store_true", help="Overwrite local changes"
    )

    subparsers.add_parser(
        "finalize", help="Squash backup stream into main and reset history"
    )

    subparsers.add_parser("pause", help="Suspend backups for current repo")
    subparsers.add_parser("resume", help="Resume backups for current repo")
    subparsers.add_parser("status", help="Show daemon and repo status")
    subparsers.add_parser("diff", help="Show changes between working dir and backup")
    subparsers.add_parser("list", help="List registered repositories")
    subparsers.add_parser("log", help="Tail the daemon log file")

    subparsers.add_parser("help", help="Show this help message")
    subparsers.add_parser("remove", help="Stop tracking current repo")
    subparsers.add_parser("sync", help="Sync with latest session")
    subparsers.add_parser("doctor", help="Clean registry and check health")

    config_parser = subparsers.add_parser(
        "config", help="Open global config file or view options"
    )
    config_parser.add_argument(
        "--list",
        "-l",
        action="store_true",
        help="List all available configuration options and their descriptions",
    )

    ignore_parser = subparsers.add_parser("ignore", help="Add pattern to .gitignore")
    ignore_parser.add_argument("pattern", help="File pattern (e.g. '*.log')")

    prune_parser = subparsers.add_parser("prune", help="Clean up old backup refs")
    prune_parser.add_argument(
        "--days", type=int, default=30, help="Age in days (default: 30)"
    )

    args = parser.parse_args()

    # Handle Subcommands
    if args.command == "install-service":
        with console.status("Installing background service...", spinner="dots"):
            service.install(interval=args.interval)
        console.print("[bold green]✔ Service installed.[/bold green]")
        return
    elif args.command == "help":
        parser.print_help()
        return
    elif args.command == "remove":
        unregister_repo()
        return
    elif args.command == "sync":
        with console.status("Syncing with latest session...", spinner="dots"):
            ops.sync_session()
        console.print("[bold green]✔ Sync complete.[/bold green]")
        return
    elif args.command == "doctor":
        run_doctor()
        return
    elif args.command == "ignore":
        add_ignore_cli(args.pattern)
        return
    elif args.command == "prune":
        with console.status("Pruning old backup refs...", spinner="dots"):
            ops.prune_backups(args.days)
        return
    elif args.command == "uninstall-service":
        with console.status("Uninstalling service...", spinner="dots"):
            service.uninstall()
        console.print("[bold green]✔ Service uninstalled.[/bold green]")
        return
    elif args.command == "now":
        daemon.main(interactive=True)
        return
    elif args.command == "restore":
        ops.restore_file(args.path, args.force)
        return
    elif args.command == "finalize":
        with console.status("Finalizing work (squashing backups)...", spinner="dots"):
            ops.finalize_work()
        return
    elif args.command == "pause":
        set_pause_state(True)
        return
    elif args.command == "resume":
        set_pause_state(False)
        return
    elif args.command == "status":
        show_status()
        return
    elif args.command == "diff":
        show_diff()
        return
    elif args.command == "list":
        list_repos()
        return
    elif args.command == "log":
        tail_log()
        return
    elif args.command == "config":
        if getattr(args, "list", False):
            show_config_reference()
        else:
            open_config()
        return

    # Default Action (if no subcommand is run)
    setup_repo()


if __name__ == "__main__":
    main()
