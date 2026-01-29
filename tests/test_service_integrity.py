import plistlib
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from git_pulsar import service


@pytest.mark.skipif(sys.platform != "darwin", reason="Requires macOS plutil")
def test_macos_plist_generation_is_valid_xml(tmp_path: Path, mocker: MagicMock) -> None:
    """
    Real-world test: Generate the actual plist to a temp file
    and try to parse it with the standard library.
    """
    # 1. Setup paths in the temp directory
    fake_plist = tmp_path / "com.test.plist"
    fake_log = tmp_path / "daemon.log"
    fake_exe = "/usr/local/bin/git-pulsar-daemon"

    # 2. Mock only the side effects (launchctl calls), NOT the file writing
    mocker.patch("subprocess.run")

    # 3. Call the real function
    service.install_macos(fake_plist, fake_log, fake_exe, interval=300)

    # 4. Verify Artifact
    assert fake_plist.exists()

    with open(fake_plist, "rb") as f:
        data = plistlib.load(f)

    assert data["Label"] == "com.jacksonferguson.gitpulsar"
    assert data["StartInterval"] == 300
    assert data["ProgramArguments"] == [fake_exe]
