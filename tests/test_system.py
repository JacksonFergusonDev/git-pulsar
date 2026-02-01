from pathlib import Path
from unittest.mock import MagicMock

from git_pulsar import system


def test_get_machine_id_darwin_uuid(mocker: MagicMock) -> None:
    """Should prefer IOPlatformUUID on macOS."""
    mocker.patch("sys.platform", "darwin")
    mocker.patch("git_pulsar.system.get_machine_id_file", return_value=Path("/no/file"))

    # Mock ioreg output
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
    """Should fallback to scutil LocalHostName if ioreg fails."""
    mocker.patch("sys.platform", "darwin")
    mocker.patch("git_pulsar.system.get_machine_id_file", return_value=Path("/no/file"))

    # ioreg fails
    mocker.patch("subprocess.check_output", side_effect=Exception)

    # scutil succeeds
    mock_run = mocker.patch("subprocess.run")
    mock_run.return_value = MagicMock(returncode=0, stdout="MyMac\n")

    assert system.get_machine_id() == "MyMac"


def test_get_machine_id_linux(tmp_path: Path, mocker: MagicMock) -> None:
    """Should read from /etc/machine-id on Linux."""
    mocker.patch("sys.platform", "linux")
    mocker.patch("git_pulsar.system.get_machine_id_file", return_value=Path("/no/file"))

    real_path = Path

    def side_effect_path(args: str) -> Path:
        if str(args) == "/etc/machine-id":
            f = tmp_path / "machine-id"
            f.write_text("linux-id-123")
            return f
        return real_path(args)

    mocker.patch(
        "pathlib.Path.exists", side_effect=lambda self: str(self) == "/etc/machine-id"
    )
    mocker.patch(
        "pathlib.Path.read_text",
        side_effect=lambda self: "linux-id-123"
        if str(self) == "/etc/machine-id"
        else "",
    )

    assert system.get_machine_id() == "linux-id-123"


def test_get_machine_id_hostname_fallback(mocker: MagicMock) -> None:
    """Should fallback to short hostname if all else fails."""
    mocker.patch("sys.platform", "unknown")
    mocker.patch("git_pulsar.system.get_machine_id_file", return_value=Path("/no/file"))
    mocker.patch("socket.gethostname", return_value="host.domain.com")

    assert system.get_machine_id() == "host"
