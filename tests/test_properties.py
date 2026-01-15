from pathlib import Path
from unittest.mock import MagicMock

from hypothesis import given
from hypothesis import strategies as st

from src import daemon

# Strategy: Generate a list of non-empty strings that don't contain newlines
# (simulating valid file paths in the registry)
paths_strategy = st.lists(
    st.text(min_size=1).filter(lambda s: "\n" not in s and s.strip()), unique=True
)


@given(existing_paths=paths_strategy, target_index=st.integers())
def test_prune_registry_removes_only_target(
    tmp_path: Path, mocker: MagicMock, existing_paths: list[str], target_index: int
) -> None:
    """
    Property: Pruning a specific path should result in a registry that contains
    all original paths EXCEPT the target, preserving order and data integrity.
    """
    # 1. Setup: Pick a target from the list (if list is empty, test is trivial)
    if not existing_paths:
        target = "some/path"
    else:
        # modulo to ensure index is valid
        target = existing_paths[target_index % len(existing_paths)]

    # 2. Mock filesystem
    registry_file = tmp_path / ".registry"
    # Write the 'existing' state
    registry_file.write_text("\n".join(existing_paths) + "\n")

    mocker.patch("src.daemon.REGISTRY_FILE", registry_file)
    mocker.patch("src.daemon.log")  # Silence logs
    mocker.patch("src.daemon.notify")  # Silence notifications

    # 3. Action
    daemon.prune_registry(target)

    # 4. Verification
    new_content = registry_file.read_text().splitlines()

    # The target should be gone
    assert target not in new_content

    # All other paths should still be there
    expected_remaining = [p for p in existing_paths if p != target]
    assert new_content == expected_remaining
