import shutil
import subprocess
import sys
from pathlib import Path

from rich.console import Console

from .constants import APP_LABEL, LOG_FILE

console = Console()


def get_executable() -> str:
    """Locates the installed daemon executable in the system path.

    Returns:
        str: The absolute path to the 'git-pulsar-daemon' executable.

    Raises:
        SystemExit: If the executable is not found in the PATH.
    """
    exe = shutil.which("git-pulsar-daemon")
    if not exe:
        console.print(
            "[bold red]ERROR:[/bold red] Could not find 'git-pulsar-daemon'. "
            "Ensure the package is installed."
        )
        sys.exit(1)
    return exe


def get_paths() -> tuple[Path, Path]:
    """Resolves the service configuration and log file paths for the current OS.

    Returns:
        tuple[Path, Path]: A tuple containing (service_unit_path, log_file_path).

    Raises:
        NotImplementedError: If called on macOS, as Homebrew manages services there.
    """
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
    """Configures and enables a systemd user timer for Linux.

    Creates the .service and .timer unit files in the user's systemd configuration
    directory, reloads the daemon, and enables the timer.

    Args:
        unit_path (Path): The target path for the .service file.
        log_path (Path): The path to the log file.
        executable (str): The path to the daemon executable.
        interval (int): The backup interval in seconds.
    """
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
    console.print(
        f"[bold green]SUCCESS:[/bold green] Pulsar systemd timer active (Linux).\n"
        f"Check status: systemctl --user status {APP_LABEL}.timer"
    )


def install(interval: int = 900) -> None:
    """Installs the background daemon service.

    On Linux, this generates systemd units. On macOS, it instructs the user to use
    Homebrew services.

    Args:
        interval (int, optional):   The interval between backup runs in seconds.
                                    Defaults to 900.
    """
    if sys.platform == "darwin":
        console.print(
            "\n[bold yellow]NOTE:[/bold yellow] On macOS, the background service "
            "is managed by Homebrew."
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
    """Removes the background daemon service.

    On Linux, this disables the systemd units and removes the files. On macOS,
    it instructs the user to use Homebrew services.
    """
    path, _ = get_paths()
    if sys.platform == "darwin":
        console.print(
            "\n[bold yellow]NOTE:[/bold yellow] On macOS, the background service "
            "is managed by Homebrew."
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

        # Remove .service and .timer files.
        timer_path = path.parent / timer_name
        if path.exists():
            path.unlink()
        if timer_path.exists():
            timer_path.unlink()

        subprocess.run(["systemctl", "--user", "daemon-reload"])

    console.print("[bold green]SUCCESS:[/bold green] Service uninstalled.")
