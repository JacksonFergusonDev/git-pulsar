import argparse
import subprocess
import sys
from pathlib import Path

from . import daemon, ops, service
from .constants import BACKUP_BRANCH, REGISTRY_FILE
from .git_wrapper import GitRepo


def show_status() -> None:
    # 1. Daemon Health
    print("--- 🩺 System Status ---")
    is_running = False
    if sys.platform == "darwin":
        res = subprocess.run(["launchctl", "list"], capture_output=True, text=True)
        is_running = "com.jacksonferguson.gitpulsar" in res.stdout
    elif sys.platform.startswith("linux"):
        res = subprocess.run(
            ["systemctl", "--user", "is-active", "com.jacksonferguson.gitpulsar.timer"],
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
        print(f"Last Backup: {repo.get_last_commit_time(BACKUP_BRANCH)}")

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

    print(f"🔍 Diff vs {BACKUP_BRANCH}:\n")
    repo = GitRepo(Path.cwd())

    # 1. Standard Diff (tracked files)
    repo.run_diff(BACKUP_BRANCH)

    # 2. Untracked Files
    if untracked := repo.get_untracked_files():
        print("\n🌱 Untracked (New) Files:")
        for line in untracked:
            print(f"   + {line}")


def list_repos() -> None:
    if not REGISTRY_FILE.exists():
        print("📭 Registry is empty.")
        return

    print("📚 Registered Repositories:")
    with open(REGISTRY_FILE, "r") as f:
        for line in f:
            if line.strip():
                print(f"  • {line.strip()}")


def tail_log() -> None:
    log_file = Path.home() / ".git_pulsar_log"
    if not log_file.exists():
        print("❌ No log file found yet.")
        return

    print(f"📜 Tailing {log_file} (Ctrl+C to stop)...")
    try:
        subprocess.run(["tail", "-f", str(log_file)])
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
    defaults = [
        "__pycache__/",
        "*.ipynb_checkpoints",
        "*.pdf",
        "*.aux",
        "*.log",
        ".DS_Store",
    ]

    if not gitignore.exists():
        print("Creating basic .gitignore...")
        with open(gitignore, "w") as f:
            f.write("\n".join(defaults) + "\n")
    else:
        print("Existing .gitignore found. Skipping creation.")

    # 3. Create/Switch to the backup branch
    print(f"Switching to {BACKUP_BRANCH}...")
    try:
        repo.checkout(BACKUP_BRANCH)
    except Exception:
        try:
            # Create orphan if main doesn't exist, or branch off current
            # We use _run directly here for the specific flag "-b"
            repo._run(["checkout", "-b", BACKUP_BRANCH], capture=False)
        except Exception as e:
            print(f"❌ Error switching branches: {e}")
            sys.exit(1)

    # 4. Add to Registry
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

    args = parser.parse_args()

    # 1. Handle Environment Setup (Flag)
    if args.env:
        ops.bootstrap_env()

    # 2. Handle Subcommands
    if args.command == "install-service":
        service.install(interval=args.interval)
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
