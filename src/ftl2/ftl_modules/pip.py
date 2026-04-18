"""FTL pip package management module.

This module handles Python package installation via pip.
"""

import subprocess
import sys
from pathlib import Path
from typing import Any

from ftl2.ftl_modules.exceptions import FTLModuleError

__all__ = ["ftl_pip"]


def ftl_pip(
    name: str | list[str] | None = None,
    requirements: str | None = None,
    state: str = "present",
    virtualenv: str | None = None,
    extra_args: str | None = None,
) -> dict[str, Any]:
    """Manage Python packages with pip.

    Args:
        name: Package name(s) to install/remove. Can be a single name or list.
        requirements: Path to requirements file
        state: Desired state - present, absent, latest
        virtualenv: Path to virtualenv (uses its Python interpreter)
        extra_args: Additional arguments to pass to pip

    Returns:
        Result dict with:
        - changed: True if packages were modified
        - name: Package name(s)
        - stdout: pip output
        - stderr: pip error output

    Raises:
        FTLModuleError: If pip operation fails
    """
    if name is None and requirements is None:
        raise FTLModuleError(
            "Either 'name' or 'requirements' must be specified",
        )

    # Determine Python interpreter
    if virtualenv:
        venv_path = Path(virtualenv)
        # Check for venv-style or virtualenv-style layout
        if (venv_path / "bin" / "python").exists():
            python = str(venv_path / "bin" / "python")
        elif (venv_path / "Scripts" / "python.exe").exists():
            # Windows
            python = str(venv_path / "Scripts" / "python.exe")
        else:
            raise FTLModuleError(
                f"Virtualenv not found or invalid: {virtualenv}",
                virtualenv=virtualenv,
            )
    else:
        python = sys.executable

    # Build pip command
    cmd: list[str] = [python, "-m", "pip"]

    if requirements:
        # Install from requirements file
        req_path = Path(requirements)
        if not req_path.exists():
            raise FTLModuleError(
                f"Requirements file not found: {requirements}",
                requirements=requirements,
            )

        if state == "absent":
            raise FTLModuleError(
                "state='absent' is not supported with requirements file",
            )

        cmd.extend(["install", "-r", str(req_path)])
        if state == "latest":
            cmd.append("--upgrade")

    else:
        # Handle single or multiple package names
        packages = [name] if isinstance(name, str) else name

        if state == "present":
            cmd.extend(["install"] + packages)
        elif state == "absent":
            cmd.extend(["uninstall", "-y"] + packages)
        elif state == "latest":
            cmd.extend(["install", "--upgrade"] + packages)
        else:
            raise FTLModuleError(
                f"Invalid state: {state}. Must be present, absent, or latest.",
                state=state,
            )

    # Add extra args
    if extra_args:
        cmd.extend(extra_args.split())

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,  # 5 minute timeout for pip operations
        )

        stdout = result.stdout
        stderr = result.stderr

        # Determine if changes were made
        changed = False
        if state == "present" or state == "latest":
            # Check for installation messages
            if "Successfully installed" in stdout:
                changed = True
            elif "Requirement already satisfied" in stdout and state == "present":
                changed = False
            elif state == "latest" and "Successfully installed" in stdout:
                changed = True
        elif state == "absent" and "Successfully uninstalled" in stdout:
            changed = True

        output: dict[str, Any] = {
            "changed": changed,
            "stdout": stdout,
            "stderr": stderr,
            "rc": result.returncode,
        }

        if name:
            output["name"] = name
        if requirements:
            output["requirements"] = requirements
        if virtualenv:
            output["virtualenv"] = virtualenv

        # Check for errors
        if result.returncode != 0:
            raise FTLModuleError(
                f"pip failed with rc={result.returncode}: {stderr or stdout}",
                **output,
            )

        return output

    except subprocess.TimeoutExpired:
        raise FTLModuleError(
            "pip operation timed out after 300s",
            name=name,
            requirements=requirements,
        ) from None
    except FTLModuleError:
        raise
    except Exception as e:
        raise FTLModuleError(
            f"pip operation failed: {e}",
            name=name,
            requirements=requirements,
        ) from e
