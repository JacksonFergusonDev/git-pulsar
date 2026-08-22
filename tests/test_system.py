from pathlib import Path
from unittest.mock import MagicMock

import pytest

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
    """Verifies that `configure_identity` does nothing if the Name file already exists.

    Args:
        tmp_path (Path): Pytest fixture for a temporary directory.
        mocker (MagicMock): Pytest fixture for mocking.
    """
    # Mock ID file (Safety check)
    mock_id_file = tmp_path / "machine_id"
    mock_id_file.write_text("existing-id")
    mocker.patch("git_pulsar.system.get_machine_id_file", return_value=mock_id_file)

    mock_name_file = tmp_path / "machine_name"
    mock_name_file.write_text("existing-name")
    mocker.patch("git_pulsar.system.get_machine_name_file", return_value=mock_name_file)

    mock_console = mocker.patch("git_pulsar.system.console")

    system.configure_identity()

    # Should exit early without asking for input
    mock_console.input.assert_not_called()


def test_get_registered_repos_parses_cleanly(tmp_path: Path, mocker: MagicMock) -> None:
    """Verifies that the registry helper strips whitespace and empty lines."""
    reg_file = tmp_path / "registry"
    reg_file.write_text("\n  /path/one  \n\n/path/two\n")

    mocker.patch("git_pulsar.system.REGISTRY_FILE", reg_file)

    repos = system.get_registered_repos()
    assert len(repos) == 2
    assert Path("/path/one") in repos
    assert Path("/path/two") in repos


def test_macos_notify_passes_args_via_argv(mocker: MagicMock) -> None:
    """Verifies that MacOSStrategy passes title and message via argv to prevent script injection."""
    mock_run = mocker.patch("subprocess.run")

    strategy = system.MacOSStrategy()
    strategy.notify('Pulsar "Drift"', 'Message with "quotes" and $symbols')

    mock_run.assert_called_once()
    args, _ = mock_run.call_args
    cmd = args[0]

    assert cmd[0] == "osascript"
    assert cmd[1] == "-e"
    assert "on run argv" in cmd[2]
    assert cmd[3] == 'Pulsar "Drift"'
    assert cmd[4] == 'Message with "quotes" and $symbols'


def test_linux_notify_executes_notify_send(mocker: MagicMock) -> None:
    """Verifies that LinuxStrategy calls notify-send."""
    mock_run = mocker.patch("subprocess.run")

    strategy = system.LinuxStrategy()
    strategy.notify("Test Title", "Test Message")

    mock_run.assert_called_once_with(
        ["notify-send", "Test Title", "Test Message"],
        stderr=mocker.ANY,
    )


def test_xdg_config_home_respected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Verifies that CONFIG_DIR respects the XDG_CONFIG_HOME environment variable."""
    import importlib

    import git_pulsar.constants

    custom_xdg = tmp_path / "custom_config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(custom_xdg))

    importlib.reload(git_pulsar.constants)

    assert custom_xdg / "git-pulsar" == git_pulsar.constants.CONFIG_DIR


def test_is_under_load(mocker: MagicMock) -> None:
    """Verifies load average detection across threshold boundaries."""
    strat = system.SystemStrategy()

    mocker.patch("os.cpu_count", return_value=4)

    # Under load: load avg 12.0 > 4 * 2.5 (10.0)
    mocker.patch("os.getloadavg", return_value=(12.0, 5.0, 3.0))
    assert strat.is_under_load() is True

    # Normal load: load avg 8.0 <= 10.0
    mocker.patch("os.getloadavg", return_value=(8.0, 5.0, 3.0))
    assert strat.is_under_load() is False


def test_macos_battery_parsing(mocker: MagicMock) -> None:
    """Verifies battery percentage and power source parsing from pmset output."""
    strat = system.MacOSStrategy()

    # AC Power, 95%
    mocker.patch(
        "subprocess.check_output",
        return_value="Now drawing from 'AC Power'\n -InternalBattery-0 (id=123) 95%; AC attached; not charging",
    )
    pct, plugged = strat.get_battery()
    assert pct == 95
    assert plugged is True

    # Battery Power, 42%
    mocker.patch(
        "subprocess.check_output",
        return_value="Now drawing from 'Battery Power'\n -InternalBattery-0 (id=123) 42%; discharging; 3:15 remaining",
    )
    pct, plugged = strat.get_battery()
    assert pct == 42
    assert plugged is False

    # Error fallback
    mocker.patch("subprocess.check_output", side_effect=RuntimeError("pmset failed"))
    pct, plugged = strat.get_battery()
    assert pct == 100
    assert plugged is True


def test_linux_battery_parsing(tmp_path: Path, mocker: MagicMock) -> None:
    """Verifies Linux sysfs battery capacity and discharging status parsing."""
    strat = system.LinuxStrategy()

    bat0 = tmp_path / "sys" / "class" / "power_supply" / "BAT0"
    bat0.mkdir(parents=True)
    (bat0 / "capacity").write_text("78\n")
    (bat0 / "status").write_text("Discharging\n")

    mocker.patch(
        "git_pulsar.system.Path",
        side_effect=lambda p: bat0 if "BAT0" in str(p) else Path(p),
    )

    pct, plugged = strat.get_battery()
    assert pct == 78
    assert plugged is False


def test_get_system_factory(mocker: MagicMock) -> None:
    """Verifies that get_system returns the correct platform strategy."""
    mocker.patch("sys.platform", "darwin")
    assert isinstance(system.get_system(), system.MacOSStrategy)

    mocker.patch("sys.platform", "linux")
    assert isinstance(system.get_system(), system.LinuxStrategy)

    mocker.patch("sys.platform", "freebsd")
    assert type(system.get_system()) is system.SystemStrategy


def test_get_registered_repos_missing(tmp_path: Path, mocker: MagicMock) -> None:
    """Verifies get_registered_repos returns empty list if registry file does not exist."""
    mocker.patch("git_pulsar.system.REGISTRY_FILE", tmp_path / "nonexistent")
    assert system.get_registered_repos() == []
