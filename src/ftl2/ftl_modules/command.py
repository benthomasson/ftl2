"""FTL command execution modules.

These modules handle shell and command execution with idempotency
support via creates/removes parameters.
"""

from typing import Any

from ftl2.ftl_modules.exceptions import FTLModuleError

__all__ = ["ftl_command", "ftl_shell"]


def ftl_command(
    cmd: str,
    chdir: str | None = None,
    creates: str | None = None,
    removes: str | None = None,
) -> dict[str, Any]:
    """Run a command.

    Args:
        cmd: Command to execute
        chdir: Directory to run command in
        creates: Skip if this file exists
        removes: Skip if this file does not exist

    Returns:
        Result dict with rc, stdout, stderr
    """
    # Placeholder - will be implemented in Phase 2
    raise NotImplementedError("ftl_command will be implemented in Phase 2")


def ftl_shell(cmd: str, **kwargs: Any) -> dict[str, Any]:
    """Run a shell command.

    This is an alias for ftl_command.

    Args:
        cmd: Shell command to execute
        **kwargs: Additional arguments passed to ftl_command

    Returns:
        Result dict with rc, stdout, stderr
    """
    # Placeholder - will be implemented in Phase 2
    raise NotImplementedError("ftl_shell will be implemented in Phase 2")
