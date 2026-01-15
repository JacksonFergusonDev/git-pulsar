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


@given(messages=st.lists(st.text(min_size=1)))
def test_log_rotation_keeps_file_size_bounded(
    tmp_path: Path, mocker: MagicMock, messages: list[str]
) -> None:
    """
    Property: No matter how many messages we log, the file size should never
    grow significantly beyond the MAX_LOG_SIZE_BYTES (plus the latest message).
    """
    log_file = tmp_path / "test.log"

    # Set a tiny limit (e.g., 100 bytes) to force frequent rotation
    small_limit = 100

    mocker.patch("src.daemon.LOG_FILE", log_file)
    mocker.patch("src.daemon.MAX_LOG_SIZE_BYTES", small_limit)

    # Silence stderr printing
    mocker.patch("builtins.print")

    for msg in messages:
        daemon.log(msg)

        # Invariant check:
        # The file might be slightly larger than the limit immediately after a write,
        # but it should have been rotated BEFORE the write if it was already too big.
        # So the size should roughly be Limit  Length of current line.
        if log_file.exists():
            current_size = log_file.stat().st_size
            # Allow some buffer for timestamps/formatting
            max_allowed = small_limit + len(msg.encode("utf-8")) + 100
            assert (
                current_size <= max_allowed
            ), f"Log file grew too large! {current_size} > {max_allowed}"
