"""Utility functions for FTL2 automation framework.

Provides helper functions for module discovery, file operations, and
result processing.
"""

from collections.abc import Generator
from pathlib import Path
from typing import TypeVar

from .exceptions import ModuleNotFound

T = TypeVar("T")


def find_module(module_dirs: list[Path], module_name: str) -> Path | None:
    """Find a module file by searching through directories.

    Searches for a module in the provided directories, looking first for
    Python files (module_name.py) and then for binary modules (module_name).

    Args:
        module_dirs: List of directory paths to search
        module_name: Name of the module to find (without .py extension)

    Returns:
        Path to the found module file, or None if not found

    Example:
        >>> from pathlib import Path
        >>> dirs = [Path("/usr/lib/ftl/modules"), Path("/opt/modules")]
        >>> module = find_module(dirs, "ping")
        >>> module
        PosixPath('/usr/lib/ftl/modules/ping.py')
    """
    module_path: Path | None = None

    # Find Python module in module_dirs
    for directory in module_dirs:
        if not directory:
            continue

        # Try .py extension
        candidate = directory / f"{module_name}.py"
        if candidate.exists():
            module_path = candidate
            break

    # Look for binary module if Python module not found
    if module_path is None:
        for directory in module_dirs:
            if not directory:
                continue

            # Try without extension
            candidate = directory / module_name
            if candidate.exists():
                module_path = candidate
                break

    return module_path


def read_module(module_dirs: list[Path], module_name: str) -> bytes:
    """Read the contents of a module file as bytes.

    Locates a module using find_module() and reads its entire contents
    in binary mode.

    Args:
        module_dirs: List of directory paths to search
        module_name: Name of the module to read

    Returns:
        The complete file contents as bytes

    Raises:
        ModuleNotFound: If the module cannot be found

    Example:
        >>> dirs = [Path("/usr/lib/ftl/modules")]
        >>> content = read_module(dirs, "ping")
        >>> len(content) > 0
        True
    """
    module_path = find_module(module_dirs, module_name)

    if module_path:
        return module_path.read_bytes()
    else:
        raise ModuleNotFound(f"Cannot find {module_name} in {module_dirs}")


def chunk(lst: list[T], n: int) -> Generator[list[T]]:
    """Split a list into chunks of maximum size n.

    Yields successive chunks from the input list, where each chunk contains
    at most n elements.

    Args:
        lst: List to be chunked
        n: Maximum size of each chunk

    Yields:
        Successive chunks of the input list

    Raises:
        ValueError: If n is less than or equal to 0.

    Example:
        >>> list(chunk([1, 2, 3, 4, 5], 2))
        [[1, 2], [3, 4], [5]]

        >>> list(chunk(['a', 'b', 'c'], 10))
        [['a', 'b', 'c']]
    """
    if n <= 0:
        raise ValueError(f"Chunk size must be positive, got {n}")
    for i in range(0, len(lst), n):
        yield lst[i : i + n]


def ensure_directory(path: Path) -> Path:
    """Ensure a directory exists, creating it if necessary.

    Expands user home directory (~) and creates the directory structure
    if it doesn't exist.

    Args:
        path: Directory path to ensure exists

    Returns:
        The absolute path to the ensured directory

    Example:
        >>> from pathlib import Path
        >>> ensure_directory(Path("~/.ftl2"))
        PosixPath('/home/user/.ftl2')
    """
    expanded = path.expanduser().resolve()
    expanded.mkdir(parents=True, exist_ok=True)
    return expanded


def is_binary_module(module_path: Path) -> bool:
    """Detect if a module file is a binary executable.

    Attempts to read the module file as text to determine if it contains
    binary data.

    Args:
        module_path: Path to the module file

    Returns:
        True if the module is binary, False if text-based

    Example:
        >>> is_binary_module(Path("modules/setup.py"))
        False

        >>> is_binary_module(Path("modules/ping"))
        True
    """
    try:
        module_path.read_text(encoding="utf-8")
        return False
    except UnicodeDecodeError:
        return True


def module_wants_json(module_path: Path) -> bool:
    """Check if a module expects JSON input via file.

    Scans the module file for the WANT_JSON marker that indicates
    the module expects to receive arguments via a JSON file.

    Args:
        module_path: Path to the module file

    Returns:
        True if module contains WANT_JSON marker

    Example:
        >>> module_wants_json(Path("modules/new_style.py"))
        True
    """
    try:
        content = module_path.read_text(encoding="utf-8")
        return "WANT_JSON" in content
    except UnicodeDecodeError:
        return False
