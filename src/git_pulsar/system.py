import os
import plistlib
import socket
import subprocess
import sys
from pathlib import Path

from .constants import MACHINE_ID_FILE


class SystemStrategy:
    """Base class defining the interface for system-level interactions."""

    def get_battery(self) -> tuple[int, bool]:
        """Retrieves the current battery status.

        Returns:
            tuple[int, bool]: A tuple containing the battery percentage (0-100)
            and a boolean indicating if the device is plugged in (AC power).
            Defaults to (100, True) if battery status cannot be determined.
        """
        return 100, True

    def is_under_load(self) -> bool:
        """Determines if the system is currently under heavy load.

        Load is defined as the 1-minute load average exceeding 2.5 times the
        available CPU count.

        Returns:
            bool: True if the system is under load, False otherwise.
        """
        if not hasattr(os, "getloadavg"):
            return False
        try:
            load_1m, _, _ = os.getloadavg()
            cpu_count = os.cpu_count() or 1
            return load_1m > (cpu_count * 2.5)
        except OSError:
            return False

    def notify(self, title: str, message: str) -> None:
        """Sends a desktop notification.

        Args:
            title (str): The notification title.
            message (str): The notification body text.
        """
        pass


class MacOSStrategy(SystemStrategy):
    """System strategy implementation for macOS."""

    def get_battery(self) -> tuple[int, bool]:
        """Retrieves battery status using `pmset`."""
        try:
            out = subprocess.check_output(["pmset", "-g", "batt"], text=True)
            is_plugged = "AC Power" in out
            import re

            match = re.search(r"(\d+)%", out)
            percent = int(match.group(1)) if match else 100
            return percent, is_plugged
        except Exception:
            return 100, True

    def notify(self, title: str, message: str) -> None:
        """Sends a notification using AppleScript."""
        # Sanitize quotes to prevent AppleScript syntax errors.
        clean_msg = message.replace('"', "'")
        script = f'display notification "{clean_msg}" with title "{title}"'
        try:
            subprocess.run(["osascript", "-e", script], stderr=subprocess.DEVNULL)
        except Exception:
            pass


class LinuxStrategy(SystemStrategy):
    """System strategy implementation for Linux."""

    def get_battery(self) -> tuple[int, bool]:
        """Retrieves battery status from sysfs (/sys/class/power_supply)."""
        try:
            bat_path = Path("/sys/class/power_supply/BAT0")
            if not bat_path.exists():
                bat_path = Path("/sys/class/power_supply/BAT1")

            if bat_path.exists():
                with open(bat_path / "capacity", "r") as f:
                    percent = int(f.read().strip())
                with open(bat_path / "status", "r") as f:
                    is_plugged = f.read().strip() != "Discharging"
                return percent, is_plugged
        except Exception:
            pass
        return 100, True

    def notify(self, title: str, message: str) -> None:
        """Sends a notification using `notify-send`."""
        try:
            subprocess.run(["notify-send", title, message], stderr=subprocess.DEVNULL)
        except FileNotFoundError:
            pass


def get_system() -> SystemStrategy:
    """Factory function to retrieve the platform-specific system strategy.

    Returns:
        SystemStrategy: An instance of MacOSStrategy, LinuxStrategy, or the base
        SystemStrategy depending on the operating system.
    """
    if sys.platform == "darwin":
        return MacOSStrategy()
    elif sys.platform.startswith("linux"):
        return LinuxStrategy()
    else:
        return SystemStrategy()


def get_machine_id_file() -> Path:
    """Returns the path to the configured machine ID file."""
    return Path(MACHINE_ID_FILE)


def get_machine_id() -> str:
    """Resolves a unique, persistent identifier for the current machine.

    The resolution order is:
    1. User-configured ID file (~/.config/git-pulsar/machine_id).
    2. Linux system machine-id (/etc/machine-id or dbus).
    3. Linux product UUID (DMI).
    4. macOS Hardware UUID (IOPlatformUUID).
    5. macOS LocalHostName.
    6. Hostname (fallback).

    Returns:
        str: A string identifier for the machine.
    """
    id_file = get_machine_id_file()
    if id_file.exists():
        return id_file.read_text().strip()

    # Linux: systemd/dbus machine-id
    if sys.platform.startswith("linux"):
        for p in (Path("/etc/machine-id"), Path("/var/lib/dbus/machine-id")):
            try:
                if p.exists():
                    mid = p.read_text().strip()
                    if mid:
                        return mid
            except Exception:
                pass

        # Optional extra fallback: product_uuid (common on x86)
        try:
            p = Path("/sys/class/dmi/id/product_uuid")
            if p.exists():
                v = p.read_text().strip()
                if v:
                    return v
        except Exception:
            pass

    # macOS: hardware UUID from IORegistry (IOPlatformUUID)
    if sys.platform == "darwin":
        # Preferred: hardware UUID from IORegistry (IOPlatformUUID)
        try:
            xml = subprocess.check_output(
                ["ioreg", "-c", "IOPlatformExpertDevice", "-d", "1", "-r", "-a"],
                text=False,
                timeout=1,
            )
            data = plistlib.loads(xml)
            uuid = data[0].get("IOPlatformUUID")
            if isinstance(uuid, str) and uuid.strip():
                return uuid.strip()
        except Exception:
            pass

        # Secondary: stable-ish local name
        try:
            res = subprocess.run(
                ["scutil", "--get", "LocalHostName"],
                capture_output=True,
                text=True,
                timeout=1,
            )
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip()
        except Exception:
            pass

    # Generic fallback (not a true machine ID)
    name = socket.gethostname()
    return name.split(".")[0]
