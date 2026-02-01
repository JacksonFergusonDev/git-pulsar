import shutil
import subprocess
import sys
from pathlib import Path

from rich.console import Console

from .constants import APP_LABEL, LOG_FILE

console = Console()


def get_executable() -> str:
    """Locates the installed daemon executable."""
    exe = shutil.which("git-pulsar-daemon")
    if not exe:
        console.print(
            "[bold red]❌ Error: Could not find 'git-pulsar-daemon'.[/bold red] "
            "Ensure the package is installed."
        )
        sys.exit(1)
    return exe


def get_paths() -> tuple[Path, Path]:
    """Returns (service_file_path, log_path) based on OS."""
    home = Path.home()
    if sys.platform.startswith("linux"):
        return (
            home / f".config/systemd/user/{APP_LABEL}.service",
            LOG_FILE,
        )

    raise NotImplementedError("Service installation is managed by Homebrew on macOS.")


def install_linux(
    unit_path: Path, log_path: Path, executable: str, interval: int
) -> None:
    base_dir = unit_path.parent
    base_dir.mkdir(parents=True, exist_ok=True)

    service_file = base_dir / f"{APP_LABEL}.service"
    timer_file = base_dir / f"{APP_LABEL}.timer"

    service_content = f"""[Unit]
Description=Git Pulsar Backup Daemon

[Service]
ExecStart={executable}
"""
    timer_content = f"""[Unit]
Description=Run Git Pulsar every {interval} seconds

[Timer]
OnBootSec=5min
OnUnitActiveSec={interval}s
Unit={APP_LABEL}.service

[Install]
WantedBy=timers.target
"""

    with open(service_file, "w") as f:
        f.write(service_content)
    with open(timer_file, "w") as f:
        f.write(timer_content)

    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(
        ["systemctl", "--user", "enable", "--now", f"{APP_LABEL}.timer"], check=True
    )
    print(
        f"✅ Pulsar systemd timer active (Linux).\n"
        f"Check status: systemctl --user status {APP_LABEL}.timer"
    )


def install(interval: int = 900) -> None:
    if sys.platform == "darwin":
        console.print(
            "\n[bold yellow]On macOS, the background service "
            "is managed by Homebrew.[/bold yellow]"
        )
        console.print("To start the service, run:")
        console.print("   [green]brew services start git-pulsar[/green]\n")
        return

    exe = get_executable()
    path, log = get_paths()

    console.print(f"Installing background service (interval: {interval}s)...")
    if sys.platform.startswith("linux"):
        install_linux(path, log, exe, interval)


def uninstall() -> None:
    path, _ = get_paths()
    if sys.platform == "darwin":
        console.print(
            "\n[bold yellow]On macOS, the background service "
            "is managed by Homebrew.[/bold yellow]"
        )
        console.print("To stop the service, run:")
        console.print("   [green]brew services stop git-pulsar[/green]\n")
        return

    elif sys.platform.startswith("linux"):
        timer_name = f"{APP_LABEL}.timer"
        subprocess.run(
            ["systemctl", "--user", "disable", "--now", timer_name],
            stderr=subprocess.DEVNULL,
        )

        # Remove .service and .timer
        timer_path = path.parent / timer_name
        if path.exists():
            path.unlink()
        if timer_path.exists():
            timer_path.unlink()

        subprocess.run(["systemctl", "--user", "daemon-reload"])

    print("✅ Service uninstalled.")
