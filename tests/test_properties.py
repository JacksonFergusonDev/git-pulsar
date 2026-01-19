import tempfile
from pathlib import Path
from unittest.mock import patch

from hypothesis import given
from hypothesis import strategies as st

from git_pulsar import daemon

# Strategy: Generate a list of non-empty strings that don't contain ANY line breaks.
paths_strategy = st.lists(
    st.text(min_size=1).map(str.strip).filter(lambda s: s and len(s.splitlines()) == 1),
    unique=True,
)


@given(existing_paths=paths_strategy, target_index=st.integers())
def test_prune_registry_removes_only_target(
    existing_paths: list[str], target_index: int
) -> None:
    """
    Property: Pruning a specific path should result in a registry that contains
    all original paths EXCEPT the target, preserving order and data integrity.
    """
    # Create a fresh temp dir for THIS example only
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        registry_file = tmp_path / ".registry"

        # 1. Setup: Pick target
        if not existing_paths:
            target = "some/path"
        else:
            target = existing_paths[target_index % len(existing_paths)]

        # Write the 'existing' state
        registry_file.write_text("\n".join(existing_paths) + "\n")

        # 2. Apply patches locally
        # We patch REGISTRY_FILE to point to our temp file
        # We patch SYSTEM.notify to suppress desktop notifications
        with (
            patch("git_pulsar.daemon.REGISTRY_FILE", registry_file),
            patch("git_pulsar.daemon.SYSTEM.notify"),
        ):
            # 3. Action
            daemon.prune_registry(target)

            # 4. Verification
            if not registry_file.exists():
                new_content: list[str] = []
            else:
                new_content = registry_file.read_text().splitlines()

            assert target not in new_content
            expected_remaining = [p for p in existing_paths if p != target]
            assert new_content == expected_remaining
