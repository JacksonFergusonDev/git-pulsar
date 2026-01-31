import argparse
import subprocess
import sys
from pathlib import Path

from . import daemon, ops, service
from .constants import (
    APP_LABEL,
    DEFAULT_IGNORES,
    LOG_FILE,
    REGISTRY_FILE,
)
from .git_wrapper import GitRepo


def _get_ref(repo: GitRepo) -> str:
    """Helper to resolve the namespaced backup ref for the current repo state."""
    return ops.get_backup_ref(repo.current_branch())


def show_status() -> None:
    # 1. Daemon Health
    print("--- 🩺 System Status ---")
    is_running = False
    if sys.platform == "darwin":
        res = subprocess.run(["launchctl", "list"], capture_output=True, text=True)
        is_running = APP_LABEL in res.stdout
    elif sys.platform.startswith("linux"):
        # Note: systemd service usually matches the label
        res = subprocess.run(
            ["systemctl", "--user", "is-active", f"{APP_LABEL}.timer"],
            capture_output=True,
            text=True,
        )
        is_running = res.stdout.strip() == "active"

    state_icon = "🟢 Running" if is_running else "🔴 Stopped"
    print(f"Daemon: {state_icon}")

    # 2. Repo Status (if we are in one)
    if Path(".git").exists():
        print("\n--- 📂 Repository Status ---")
        repo = GitRepo(Path.cwd())

        # Last Backup Time
        ref = _get_ref(repo)
        print(f"Last Backup: {repo.get_last_commit_time(ref)}")

        # Pending Changes
        count = len(repo.status_porcelain())
        print(f"Pending:     {count} files changed")

        if (Path(".git") / "pulsar_paused").exists():
            print("Mode:        ⏸️  PAUSED")

    # 3. Global Summary (if not in a repo)
    else:
        if REGISTRY_FILE.exists():
            with open(REGISTRY_FILE) as f:
                count = len([line for line in f if line.strip()])
            print(f"\nwatching {count} repositories.")


def show_diff() -> None:
    if not Path(".git").exists():
        print("❌ Not a git repository.")
        sys.exit(1)

    repo = GitRepo(Path.cwd())

    # 1. Standard Diff (tracked files)
    ref = _get_ref(repo)

    print(f"🔍 Diff vs {ref}:\n")
    repo.run_diff(ref)

    # 2. Untracked Files
    if untracked := repo.get_untracked_files():
        print("\n🌱 Untracked (New) Files:")
        for line in untracked:
            print(f"   + {line}")


def list_repos() -> None:
    if not REGISTRY_FILE.exists():
        print("📭 Registry is empty.")
        return

    print(f"{'Repository':<50} {'Status':<12} {'Last Backup'}")
    print("-" * 80)

    with open(REGISTRY_FILE, "r") as f:
        lines = [line.strip() for line in f if line.strip()]

    for path_str in lines:
        path = Path(path_str)
        display_path = str(path).replace(str(Path.home()), "~")

        # Truncate path for display
        if len(display_path) > 48:
            display_path = "..." + display_path[-45:]

        status = "❓ Unknown"
        last_backup = "-"

        if not path.exists():
            status = "🔴 Missing"
        else:
            if (path / ".git" / "pulsar_paused").exists():
                status = "⏸️  Paused"
            else:
                status = "🟢 Active"

            # Try to read last backup time
            try:
                r = GitRepo(path)
                ref = _get_ref(r)
                last_backup = r.get_last_commit_time(ref)
            except Exception:
                pass

        print(f"{display_path:<50} {status:<12} {last_backup}")


def unregister_repo() -> None:
    cwd = str(Path.cwd())
    if not REGISTRY_FILE.exists():
        print("📭 Registry is empty.")
        return

    with open(REGISTRY_FILE, "r") as f:
        lines = [line.strip() for line in f if line.strip()]

    if cwd not in lines:
        print(f"⚠️  Current path not registered: {cwd}")
        return

    with open(REGISTRY_FILE, "w") as f:
        for line in lines:
            if line != cwd:
                f.write(f"{line}\n")
    print(f"✅ Unregistered: {cwd}")


def run_doctor() -> None:
    print("🚑 Pulsar Doctor\n")

    # 1. Registry Hygiene
    print("1. Checking Registry...")
    if not REGISTRY_FILE.exists():
        print("   • Registry empty.")
    else:
        with open(REGISTRY_FILE, "r") as f:
            lines = [line.strip() for line in f if line.strip()]

        valid_lines = []
        fixed = False
        for line in lines:
            if Path(line).exists():
                valid_lines.append(line)
            else:
                print(f"   ❌ Removing ghost entry: {line}")
                fixed = True

        if fixed:
            with open(REGISTRY_FILE, "w") as f:
                f.write("\n".join(valid_lines) + "\n")
            print("   ✅ Registry cleaned.")
        else:
            print("   ✅ Registry healthy.")

    # 2. Daemon Status
    print("\n2. Checking Daemon...")
    is_running = False
    if sys.platform == "darwin":
        res = subprocess.run(["launchctl", "list"], capture_output=True, text=True)
        is_running = APP_LABEL in res.stdout
    elif sys.platform.startswith("linux"):
        res = subprocess.run(
            ["systemctl", "--user", "is-active", f"{APP_LABEL}.timer"],
            capture_output=True,
            text=True,
        )
        is_running = res.stdout.strip() == "active"

    if is_running:
        print("   ✅ Daemon is active.")
    else:
        print("   🔴 Daemon is STOPPED. Run 'git pulsar install-service'.")

    # 3. Connectivity
    print("\n3. Checking Connectivity...")
    try:
        # Simple ssh hello to github (returns status 1 but prints success message)
        res = subprocess.run(
            ["ssh", "-T", "git@github.com"], capture_output=True, text=True, timeout=5
        )
        if "successfully authenticated" in res.stderr:
            print("   ✅ GitHub SSH connection successful.")
        else:
            print("   ⚠️  GitHub SSH check didn't return standard greeting.")
    except Exception as e:
        print(f"   ❌ SSH Check failed: {e}")


def add_ignore_cli(pattern: str) -> None:
    if not Path(".git").exists():
        print("❌ Not a git repository.")
        return
    ops.add_ignore(pattern)


def tail_log() -> None:
    if not LOG_FILE.exists():
        print(f"❌ No log file found yet at {LOG_FILE}.")
        return

    print(f"📜 Tailing {LOG_FILE} (Ctrl+C to stop)...")
    try:
        subprocess.run(["tail", "-f", str(LOG_FILE)])
    except KeyboardInterrupt:
        print("\nStopped.")


def set_pause_state(paused: bool) -> None:
    if not Path(".git").exists():
        print("❌ Not a git repository.")
        sys.exit(1)

    pause_file = Path(".git/pulsar_paused")
    if paused:
        pause_file.touch()
        print("⏸️  Pulsar paused. Backups suspended for this repo.")
    else:
        if pause_file.exists():
            pause_file.unlink()
        print("▶️  Pulsar resumed. Backups active.")


def setup_repo(registry_path: Path = REGISTRY_FILE) -> None:
    cwd = Path.cwd()
    print(f"🔭 Git Pulsar: activating for {cwd.name}...")

    # 1. Ensure it's a git repo
    if not (cwd / ".git").exists():
        print(f"Initializing git in {cwd}...")
        subprocess.run(["git", "init"], check=True)

    repo = GitRepo(cwd)

    # 2. Check/Create .gitignore
    gitignore = cwd / ".gitignore"

    if not gitignore.exists():
        print("Creating basic .gitignore...")
        with open(gitignore, "w") as f:
            f.write("\n".join(DEFAULT_IGNORES) + "\n")
    else:
        print("Existing .gitignore found. Checking for missing defaults...")
        with open(gitignore, "r") as f:
            existing_content = f.read()

        missing_defaults = [d for d in DEFAULT_IGNORES if d not in existing_content]

        if missing_defaults:
            print(f"Appending {len(missing_defaults)} missing ignores...")
            with open(gitignore, "a") as f:
                f.write("\n" + "\n".join(missing_defaults) + "\n")
        else:
            print("All defaults present.")

    # 3. Add to Registry
    print("Registering path...")
    if not registry_path.exists():
        registry_path.touch()

    with open(registry_path, "r+") as f:
        content = f.read()
        if str(cwd) not in content:
            f.write(f"{cwd}\n")
            print(f"Registered: {cwd}")
        else:
            print("Already registered.")

    print("\n✅ Pulsar Active.")

    try:
        # Check if we can verify credentials (only if remote exists)
        remotes = repo._run(["remote"])
        if remotes:
            print("Verifying git access...")
            repo._run(["push", "--dry-run"], capture=False)
    except Exception:
        print(
            "⚠️  WARNING: Git push failed. Ensure you have SSH keys set up or "
            "credentials cached."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Git Pulsar CLI")

    # Global flags
    parser.add_argument(
        "--env",
        "-e",
        action="store_true",
        help="Bootstrap macOS Python environment (uv, direnv, VS Code)",
    )

    subparsers = parser.add_subparsers(
        dest="command", help="Service management commands"
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
        "finalize", help="Squash wip/pulsar into main and reset backup history"
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

    ignore_parser = subparsers.add_parser("ignore", help="Add pattern to .gitignore")
    ignore_parser.add_argument("pattern", help="File pattern (e.g. '*.log')")

    prune_parser = subparsers.add_parser("prune", help="Clean up old backup refs")
    prune_parser.add_argument(
        "--days", type=int, default=30, help="Age in days (default: 30)"
    )

    args = parser.parse_args()

    # 1. Handle Environment Setup (Flag)
    if args.env:
        ops.bootstrap_env()

    # 2. Handle Subcommands
    if args.command == "install-service":
        service.install(interval=args.interval)
        return
    elif args.command == "help":
        parser.print_help()
        return
    elif args.command == "remove":
        unregister_repo()
        return
    elif args.command == "sync":
        ops.sync_session()
        return
    elif args.command == "doctor":
        run_doctor()
        return
    elif args.command == "ignore":
        add_ignore_cli(args.pattern)
        return
    elif args.command == "prune":
        ops.prune_backups(args.days)
        return
    elif args.command == "uninstall-service":
        service.uninstall()
        return
    elif args.command == "now":
        daemon.main(interactive=True)
        return
    elif args.command == "restore":
        ops.restore_file(args.path, args.force)
        return
    elif args.command == "finalize":
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

    # 3. Default Action (if no subcommand is run, or after --env)
    # We always run setup_repo unless a service command explicitly exited.
    setup_repo()


if __name__ == "__main__":
    main()
