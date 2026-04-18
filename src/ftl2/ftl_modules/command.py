"""FTL command execution modules.

These modules handle shell and command execution with idempotency
support via creates/removes parameters.
"""

import subprocess
from pathlib import Path
from typing import Any

from ftl2.ftl_modules.exceptions import FTLModuleError

__all__ = ["ftl_command", "ftl_shell"]


def ftl_command(
    cmd: str,
    chdir: str | None = None,
    creates: str | None = None,
    removes: str | None = None,
    timeout: int | None = None,
    check: bool = False,
) -> dict[str, Any]:
    """Run a command.

    Supports idempotency via creates/removes parameters:
    - creates: Skip if this file/directory exists
    - removes: Skip if this file/directory does not exist

    Args:
        cmd: Command to execute (passed to shell)
        chdir: Directory to run command in
        creates: Skip command if this path exists
        removes: Skip command if this path does not exist
        timeout: Command timeout in seconds
        check: If True, raise error on non-zero return code

    Returns:
        Result dict with:
        - changed: True if command was executed
        - rc: Return code
        - stdout: Standard output
        - stderr: Standard error
        - cmd: The command that was run

    Raises:
        FTLModuleError: If command fails and check=True, or on other errors
    """
    # Idempotency checks
    if creates:
        creates_path = Path(creates)
        if creates_path.exists():
            return {
                "changed": False,
                "rc": 0,
                "stdout": "",
                "stderr": "",
                "cmd": cmd,
                "msg": f"Skipped: '{creates}' exists",
            }

    if removes:
        removes_path = Path(removes)
        if not removes_path.exists():
            return {
                "changed": False,
                "rc": 0,
                "stdout": "",
                "stderr": "",
                "cmd": cmd,
                "msg": f"Skipped: '{removes}' does not exist",
            }

    try:
        result = subprocess.run(
            cmd,
            shell=True,
            cwd=chdir,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        output: dict[str, Any] = {
            "changed": True,
            "rc": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "cmd": cmd,
        }

        if chdir:
            output["chdir"] = chdir

        # Check mode: raise on non-zero
        if check and result.returncode != 0:
            raise FTLModuleError(
                f"Command failed with rc={result.returncode}: {result.stderr or result.stdout}",
                **output,
            )

        return output

    except subprocess.TimeoutExpired:
        raise FTLModuleError(
            f"Command timed out after {timeout}s",
            cmd=cmd,
            timeout=timeout,
        ) from None
    except FTLModuleError:
        raise
    except Exception as e:
        raise FTLModuleError(
            f"Command execution failed: {e}",
            cmd=cmd,
        ) from e


def ftl_shell(cmd: str, **kwargs: Any) -> dict[str, Any]:
    """Run a shell command.

    This is an alias for ftl_command. The distinction between
    command and shell in Ansible is about whether a shell is used;
    in FTL, we always use a shell for simplicity.

    Args:
        cmd: Shell command to execute
        **kwargs: Additional arguments passed to ftl_command
            - chdir: Directory to run in
            - creates: Skip if path exists
            - removes: Skip if path doesn't exist
            - timeout: Command timeout
            - check: Raise on non-zero exit

    Returns:
        Result dict with rc, stdout, stderr, changed

    Raises:
        FTLModuleError: If command fails
    """
    return ftl_command(cmd, **kwargs)
