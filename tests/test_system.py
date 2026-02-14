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


def test_get_identity_slug_combines_name_and_id(
    tmp_path: Path, mocker: MagicMock
) -> None:
    """
    Verifies that the slug combines the human name and the first 8 chars of the ID.
    """
    # Mock the stable machine ID
    mocker.patch("git_pulsar.system.get_machine_id", return_value="1234567890abcdef")

    # Mock the machine name file
    name_file = tmp_path / "machine_name"
    name_file.write_text("my-macbook")
    mocker.patch("git_pulsar.system.get_machine_name_file", return_value=name_file)

    # Expect: name + double-dash + first 8 chars of ID
    assert system.get_identity_slug() == "my-macbook--12345678"


def test_fetch_remote_identities_parses_slugs(mocker: MagicMock) -> None:
    """Verifies that `_fetch_remote_identities` correctly extracts names from refs."""
    mock_repo = MagicMock()
    # Simulate git ls-remote output
    mock_repo._run.return_value = (
        "sha1 refs/heads/wip/pulsar/macbook--12345678/main\n"
        "sha2 refs/heads/wip/pulsar/desktop--abcdef12/dev\n"
        "sha3 refs/heads/wip/pulsar/weird-ref/main\n"  # Should be ignored (no --)
    )

    identities = system._fetch_remote_identities(mock_repo)

    assert "macbook" in identities
    assert "desktop" in identities
    assert "weird-ref" not in identities
    assert len(identities) == 2


def test_configure_identity_creates_file(tmp_path: Path, mocker: MagicMock) -> None:
    """Verifies that `configure_identity` writes the human-readable name to disk.

    Args:
        tmp_path (Path): Pytest fixture for a temporary directory.
        mocker (MagicMock): Pytest fixture for mocking.
    """
    mock_console = mocker.patch("git_pulsar.system.console")
    mock_console.input.return_value = "my-laptop"

    # 'machine_id' is for the stable UUID (generated automatically)
    # 'machine_name' is for the user input
    mock_id_file = tmp_path / "machine_id"
    mock_name_file = tmp_path / "machine_name"

    mocker.patch("git_pulsar.system.get_machine_id_file", return_value=mock_id_file)
    mocker.patch("git_pulsar.system.get_machine_name_file", return_value=mock_name_file)

    # Mock get_machine_id so it doesn't try to use system calls,
    # ensuring the ID file gets populated with a known value.
    mocker.patch("git_pulsar.system.get_machine_id", return_value="UUID-1234")

    system.configure_identity()

    # Assert that the NAME file contains the input "my-laptop"
    assert mock_name_file.read_text() == "my-laptop"

    # Assert the ID file was also created/preserved
    assert mock_id_file.exists()
    assert mock_id_file.read_text() == "UUID-1234"


def test_configure_identity_skips_existing(tmp_path: Path, mocker: MagicMock) -> None:
    """Verifies that `configure_identity` does nothing if the ID file already exists.

    Args:
        tmp_path (Path): Pytest fixture for a temporary directory.
        mocker (MagicMock): Pytest fixture for mocking.
    """
    mock_id_file = tmp_path / "machine_id"
    mock_id_file.write_text("existing-id")
    mocker.patch("git_pulsar.system.get_machine_id_file", return_value=mock_id_file)

    mock_console = mocker.patch("git_pulsar.system.console")

    system.configure_identity()

    mock_console.input.assert_not_called()
