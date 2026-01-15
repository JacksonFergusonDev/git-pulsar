import tempfile
from pathlib import Path
from unittest.mock import patch

from hypothesis import given
from hypothesis import strategies as st

from src import daemon

# Strategy: Generate a list of non-empty strings that don't contain ANY line breaks.
# We map strip() to remove leading/trailing whitespace (which includes newlines).
# We filter s.splitlines() to ensure no internal line breaks (like \n, \r, \x1e) remain.
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
        with (
            patch("src.daemon.REGISTRY_FILE", registry_file),
            patch("src.daemon.log"),
            patch("src.daemon.notify"),
        ):
            # 3. Action
            daemon.prune_registry(target)

            # 4. Verification
            if not registry_file.exists():
                # If the file was deleted (e.g. if it became empty), handle that
                new_content: list[str] = []
            else:
                new_content = registry_file.read_text().splitlines()

            assert target not in new_content
            expected_remaining = [p for p in existing_paths if p != target]
            assert new_content == expected_remaining


@given(messages=st.lists(st.text(min_size=1)))
def test_log_rotation_keeps_file_size_bounded(messages: list[str]) -> None:
    """
    Property: No matter how many messages we log, the file size should never
    grow significantly beyond the MAX_LOG_SIZE_BYTES (plus the latest message).
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        log_file = Path(tmp_dir) / "test.log"
        small_limit = 100

        with (
            patch("src.daemon.LOG_FILE", log_file),
            patch("src.daemon.MAX_LOG_SIZE_BYTES", small_limit),
            patch("builtins.print"),
        ):
            for msg in messages:
                daemon.log(msg)

                if log_file.exists():
                    current_size = log_file.stat().st_size
                    # Invariant: Size <= Limit + NewMsg + Buffer
                    max_allowed = small_limit + len(msg.encode("utf-8")) + 100
                    assert current_size <= max_allowed, (
                        f"Log file grew too large! {current_size} > {max_allowed}"
                    )
