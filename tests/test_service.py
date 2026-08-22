from pathlib import Path
from unittest.mock import MagicMock

import pytest

from git_pulsar import service
from git_pulsar.constants import APP_LABEL
from git_pulsar.types import ServiceUnitConfig


def test_install_linux_creates_service_and_timer_files(
    tmp_path: Path, mocker: MagicMock
) -> None:
    """Verifies that install_linux writes systemd service and timer files and enables timer."""
    mock_run = mocker.patch("subprocess.run")
    mocker.patch("git_pulsar.service.console")

    unit_path = tmp_path / "user" / f"{APP_LABEL}.service"
    config = ServiceUnitConfig(
        unit_path=unit_path,
        executable="/usr/local/bin/git-pulsar-daemon",
        interval=600,
        log_path=tmp_path / "log" / "pulsar.log",
    )

    service.install_linux(config)

    service_file = tmp_path / "user" / f"{APP_LABEL}.service"
    timer_file = tmp_path / "user" / f"{APP_LABEL}.timer"

    assert service_file.exists()
    assert "/usr/local/bin/git-pulsar-daemon" in service_file.read_text()
    assert timer_file.exists()
    assert "OnUnitActiveSec=600s" in timer_file.read_text()

    mock_run.assert_any_call(["systemctl", "--user", "daemon-reload"], check=True)
    mock_run.assert_any_call(
        ["systemctl", "--user", "enable", "--now", f"{APP_LABEL}.timer"], check=True
    )


def test_is_service_enabled_darwin(mocker: MagicMock) -> None:
    """Verifies is_service_enabled queries launchctl on macOS."""
    mocker.patch("sys.platform", "darwin")
    mocker.patch(
        "subprocess.run",
        return_value=MagicMock(stdout="homebrew.mxcl.git-pulsar\n"),
    )
    assert service.is_service_enabled() is True


def test_is_service_enabled_linux(mocker: MagicMock) -> None:
    """Verifies is_service_enabled queries systemctl on Linux."""
    mocker.patch("sys.platform", "linux")
    mocker.patch(
        "subprocess.run",
        return_value=MagicMock(stdout="active\n"),
    )
    assert service.is_service_enabled() is True


def test_get_paths_darwin_raises(mocker: MagicMock) -> None:
    """Verifies get_paths raises NotImplementedError on macOS."""
    mocker.patch("sys.platform", "darwin")
    with pytest.raises(NotImplementedError):
        service.get_paths()


def test_get_paths_linux(mocker: MagicMock) -> None:
    """Verifies get_paths returns systemd and log paths on Linux."""
    mocker.patch("sys.platform", "linux")
    service_path, log_path = service.get_paths()
    assert service_path.name == f"{APP_LABEL}.service"
    assert "systemd" in str(service_path)
    assert log_path.name == "daemon.log"
