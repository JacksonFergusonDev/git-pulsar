"""Tests for the configuration management subsystem."""

from pathlib import Path
from unittest.mock import MagicMock

from git_pulsar.config import Config


def test_config_defaults() -> None:
    """Verifies that the configuration initializes with sensible defaults."""
    conf = Config()
    assert conf.core.remote_name == "origin"
    assert conf.daemon.commit_interval == 600  # Default 10 mins
    assert conf.daemon.push_interval == 3600  # Default 1 hour
    assert conf.files.ignore == []


def test_config_presets() -> None:
    """Verifies that applying a preset updates the daemon intervals correctly."""
    conf = Config()

    # Test 'paranoid' preset
    conf.daemon.preset = "paranoid"
    conf.daemon.apply_preset()
    assert conf.daemon.commit_interval == 300
    assert conf.daemon.push_interval == 300

    # Test 'lazy' preset
    conf.daemon.preset = "lazy"
    conf.daemon.apply_preset()
    assert conf.daemon.commit_interval == 3600
    assert conf.daemon.push_interval == 14400


def test_config_load_merges_layers(tmp_path: Path, mocker: MagicMock) -> None:
    """Verifies the cascading merge logic (Defaults -> Global -> Local).

    Args:
        tmp_path (Path): Pytest fixture for a temporary directory.
        mocker (MagicMock): Pytest fixture for mocking.
    """
    # 1. Setup Global Config (Real File in tmp_path)
    global_config_path = tmp_path / "global_config.toml"
    global_config_path.write_text(
        '[core]\nremote_name = "upstream"\n'
        "[daemon]\ncommit_interval = 50\n"
        '[files]\nignore = ["*.log"]\n'
    )

    # 2. Setup Local Config (Real file in tmp_path)
    local_toml = tmp_path / "pulsar.toml"
    local_toml.write_text(
        '[daemon]\ncommit_interval = 10\n[files]\nignore = ["*.tmp"]\n'  # Should append
    )

    # Point the global CONFIG_FILE constant to our real temporary file
    mocker.patch("git_pulsar.config.CONFIG_FILE", global_config_path)

    # 3. Load Config (specifying tmp_path as the repo root)
    conf = Config.load(repo_path=tmp_path)

    # 4. Assertions
    assert conf.core.remote_name == "upstream"  # From Global
    assert conf.daemon.commit_interval == 10  # Local overrides Global
    assert "*.log" in conf.files.ignore  # From Global
    assert "*.tmp" in conf.files.ignore  # From Local (Appended)


def test_config_load_from_pyproject(tmp_path: Path) -> None:
    """Verifies that configuration can be loaded from pyproject.toml."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[tool.pulsar.core]\nremote_name = "backup"\n'
        '[tool.pulsar.daemon]\npreset = "paranoid"\n'
    )

    conf = Config.load(repo_path=tmp_path)

    assert conf.core.remote_name == "backup"
    assert conf.daemon.preset == "paranoid"
    assert conf.daemon.commit_interval == 300
