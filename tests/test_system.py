from pathlib import Path
from unittest.mock import MagicMock

from git_pulsar import system


def test_get_machine_id_darwin_uuid(mocker: MagicMock) -> None:
    """Verifies that `get_machine_id` prioritizes the hardware UUID on macOS.

    It mocks `ioreg` output to ensure the IOPlatformUUID is parsed correctly.

    Args:
        mocker (MagicMock): Pytest fixture for mocking.
    """
    mocker.patch("sys.platform", "darwin")
    mocker.patch("git_pulsar.system.get_machine_id_file", return_value=Path("/no/file"))

    # Simulate `ioreg` XML output containing a valid UUID.
    plist_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
    <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
    <plist version="1.0">
    <array>
        <dict>
            <key>IOPlatformUUID</key>
            <string>0000-0000-UUID-0000</string>
        </dict>
    </array>
    </plist>
    """
    mocker.patch("subprocess.check_output", return_value=plist_xml)

    assert system.get_machine_id() == "0000-0000-UUID-0000"


def test_get_machine_id_darwin_fallback(mocker: MagicMock) -> None:
    """Verifies that `get_machine_id` falls back to `scutil` hostname if `ioreg` fails.

    Args:
        mocker (MagicMock): Pytest fixture for mocking.
    """
    mocker.patch("sys.platform", "darwin")
    mocker.patch("git_pulsar.system.get_machine_id_file", return_value=Path("/no/file"))

    # Simulate `ioreg` command failure.
    mocker.patch("subprocess.check_output", side_effect=Exception)

    # Simulate successful `scutil` execution.
    mock_run = mocker.patch("subprocess.run")
    mock_run.return_value = MagicMock(returncode=0, stdout="MyMac\n")

    assert system.get_machine_id() == "MyMac"


def test_get_machine_id_linux(mocker: MagicMock) -> None:
    """Verifies that `get_machine_id` correctly reads from `/etc/machine-id` on Linux.

    Args:
        mocker (MagicMock): Pytest fixture for mocking.
    """
    mocker.patch("sys.platform", "linux")
    mocker.patch("git_pulsar.system.get_machine_id_file", return_value=Path("/no/file"))

    mock_path_cls = mocker.patch("git_pulsar.system.Path")

    def side_effect(path_arg: str) -> MagicMock:
        mock_obj = MagicMock()
        # Mock file existence only for the standard Linux machine-id path.
        if str(path_arg) == "/etc/machine-id":
            mock_obj.exists.return_value = True
            mock_obj.read_text.return_value = "linux-id-123"
        else:
            mock_obj.exists.return_value = False
        return mock_obj

    mock_path_cls.side_effect = side_effect

    assert system.get_machine_id() == "linux-id-123"


def test_get_machine_id_hostname_fallback(mocker: MagicMock) -> None:
    """
    Verifies that `get_machine_id` falls back
    to the short hostname on unknown platforms.

    Args:
        mocker (MagicMock): Pytest fixture for mocking.
    """
    mocker.patch("sys.platform", "unknown")
    mocker.patch("git_pulsar.system.get_machine_id_file", return_value=Path("/no/file"))
    mocker.patch("socket.gethostname", return_value="host.domain.com")

    # Expect only the short hostname (first component).
    assert system.get_machine_id() == "host"
