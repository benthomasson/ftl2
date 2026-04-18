"""State file read/write operations.

Handles JSON state file persistence with atomic writes for safety.
"""

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _empty_state() -> dict[str, Any]:
    """Create an empty state structure."""
    now = datetime.now(UTC).isoformat()
    return {
        "version": 1,
        "created_at": now,
        "updated_at": now,
        "hosts": {},
        "resources": {},
    }


def read_state_file(path: Path) -> dict[str, Any]:
    """Read state from a JSON file.

    Creates an empty state if the file doesn't exist.

    Args:
        path: Path to the state file

    Returns:
        State data dictionary
    """
    if not path.exists():
        return _empty_state()

    try:
        content = path.read_text()
        if not content.strip():
            return _empty_state()
        return json.loads(content)
    except (json.JSONDecodeError, OSError) as e:
        # Log warning but return empty state
        import logging
        logging.getLogger(__name__).warning(
            f"Failed to read state file {path}: {e}. Starting with empty state."
        )
        return _empty_state()


def write_state_file(path: Path, data: dict[str, Any]) -> None:
    """Write state to a JSON file atomically.

    Uses atomic write (temp file + rename) for safety. This ensures
    the state file is never in a partial/corrupt state, even if the
    process crashes during write.

    Args:
        path: Path to the state file
        data: State data dictionary
    """
    # Ensure parent directory exists
    path.parent.mkdir(parents=True, exist_ok=True)

    # Pretty print for human readability
    content = json.dumps(data, indent=2, sort_keys=False)

    # Atomic write: write to temp file, then rename
    # This ensures the state file is never partially written
    fd, temp_path = tempfile.mkstemp(
        dir=path.parent,
        prefix=".ftl2-state-",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
            f.write("\n")  # Trailing newline
            f.flush()
            os.fsync(f.fileno())  # Ensure written to disk

        # Atomic rename
        os.rename(temp_path, path)
    except Exception:
        # Clean up temp file on failure
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise
