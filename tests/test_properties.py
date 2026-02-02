import tempfile
from pathlib import Path
from unittest.mock import patch

from hypothesis import given
from hypothesis import strategies as st

from git_pulsar import daemon

# Strategy: Generate a list of non-empty strings that don't contain ANY line breaks.
# This simulates valid file paths stored in the newline-delimited registry file.
paths_strategy = st.lists(
    st.text(min_size=1).map(str.strip).filter(lambda s: s and len(s.splitlines()) == 1),
    unique=True,
)


@given(existing_paths=paths_strategy, target_index=st.integers())
def test_prune_registry_removes_only_target(
    existing_paths: list[str], target_index: int
) -> None:
    """
    Verifies the property that pruning removes
    only the specified target from the registry.

    This test uses Hypothesis to generate diverse lists of paths and ensures that:
    1. The target path is no longer present in the registry.
    2. All other paths remain in the registry.
    3. The relative order of the remaining paths is preserved.

    Args:
        existing_paths (list[str]): A generated list of unique path strings.
        target_index (int): A generated integer to select a target from the list.
    """
    # Create a fresh temporary directory for this specific test case execution.
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        registry_file = tmp_path / ".registry"

        # 1. Setup: Select a target path to prune.
        if not existing_paths:
            # Handle the edge case of an empty initial registry.
            target = "some/path"
        else:
            # Safely select an index within the bounds of the list.
            target = existing_paths[target_index % len(existing_paths)]

        # Write the initial state to the mock registry file.
        registry_file.write_text("\n".join(existing_paths) + "\n")

        # 2. Apply patches.
        # - Redirect REGISTRY_FILE to our temporary file.
        # - Suppress desktop notifications via SYSTEM.notify.
        with (
            patch("git_pulsar.daemon.REGISTRY_FILE", registry_file),
            patch("git_pulsar.daemon.SYSTEM.notify"),
        ):
            # 3. Action: Prune the target path.
            daemon.prune_registry(target)

            # 4. Verification: Check the file content.
            if not registry_file.exists():
                new_content: list[str] = []
            else:
                new_content = registry_file.read_text().splitlines()

            # Assert the target is gone.
            assert target not in new_content

            # Assert all other paths are preserved in order.
            expected_remaining = [p for p in existing_paths if p != target]
            assert new_content == expected_remaining
