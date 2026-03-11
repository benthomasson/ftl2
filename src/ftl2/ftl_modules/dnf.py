"""FTL dnf module — manage packages with dnf/dnf5.

Async replacement for ansible.builtin.dnf. Uses subprocess calls
to the dnf CLI with the same parameter interface. No python3-dnf
bindings required.
"""

import asyncio
from typing import Any

from ftl2.ftl_modules.exceptions import FTLModuleError

__all__ = ["ftl_dnf"]


async def _run(cmd: str) -> tuple[str, str, int]:
    """Run a shell command and return (stdout, stderr, returncode)."""
    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return stdout.decode().strip(), stderr.decode().strip(), proc.returncode or 0


async def _is_installed(name: str) -> bool:
    """Check if a single package is installed via rpm."""
    _, _, rc = await _run(f"rpm -q {name}")
    return rc == 0


async def ftl_dnf(
    name: str | list[str] | None = None,
    state: str = "present",
    update_cache: bool = False,
    enablerepo: str | None = None,
    disablerepo: str | None = None,
    disable_gpg_check: bool = False,
    installroot: str | None = None,
    allowerasing: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    """Manage packages with dnf.

    Args:
        name: Package name or list of package names
        state: Desired state - present, absent, latest, installed, removed
        update_cache: Run dnf makecache before operations
        enablerepo: Repos to enable for this operation
        disablerepo: Repos to disable for this operation
        disable_gpg_check: Disable GPG signature checking
        installroot: Set install root
        allowerasing: Allow erasing of installed packages to resolve deps
        **kwargs: Additional parameters (ignored for compatibility)

    Returns:
        Result dict with changed and results list
    """
    # Normalize state aliases
    if state == "installed":
        state = "present"
    elif state == "removed":
        state = "absent"

    if state not in ("present", "absent", "latest"):
        raise FTLModuleError(
            f"Invalid state '{state}'. Must be one of: present, absent, latest, installed, removed"
        )

    if name is None and not update_cache:
        raise FTLModuleError("name is required when state is set")

    # Normalize name to a list
    if name is None:
        packages = []
    elif isinstance(name, str):
        packages = [name]
    else:
        packages = list(name)

    results: list[str] = []
    changed = False

    # Build common dnf flags
    flags = " -y"
    if enablerepo:
        flags += f" --enablerepo={enablerepo}"
    if disablerepo:
        flags += f" --disablerepo={disablerepo}"
    if disable_gpg_check:
        flags += " --nogpgcheck"
    if installroot:
        flags += f" --installroot={installroot}"
    if allowerasing:
        flags += " --allowerasing"

    # Update cache if requested
    if update_cache:
        _, stderr, rc = await _run(f"dnf makecache{flags}")
        if rc != 0:
            raise FTLModuleError(f"dnf makecache failed: {stderr}")
        results.append("Cache updated")

    if not packages:
        return {"changed": changed, "results": results, "rc": 0}

    if state in ("present", "latest"):
        # Check which packages need action
        to_install: list[str] = []
        to_update: list[str] = []

        for pkg in packages:
            installed = await _is_installed(pkg)
            if not installed:
                to_install.append(pkg)
            elif state == "latest":
                to_update.append(pkg)

        # Install missing packages
        if to_install:
            pkg_str = " ".join(to_install)
            stdout, stderr, rc = await _run(f"dnf install{flags} {pkg_str}")
            if rc != 0:
                raise FTLModuleError(
                    f"Failed to install {pkg_str}: {stderr}",
                    rc=rc,
                    results=results,
                )
            changed = True
            for pkg in to_install:
                results.append(f"Installed: {pkg}")

        # Update packages to latest (only for state=latest)
        if to_update:
            pkg_str = " ".join(to_update)
            stdout, stderr, rc = await _run(f"dnf upgrade{flags} {pkg_str}")
            if rc != 0:
                raise FTLModuleError(
                    f"Failed to upgrade {pkg_str}: {stderr}",
                    rc=rc,
                    results=results,
                )
            # dnf upgrade returns 0 even if nothing to do,
            # check if anything was actually upgraded
            if "Nothing to do" not in stdout and "already installed" not in stdout.lower():
                changed = True
                for pkg in to_update:
                    results.append(f"Updated: {pkg}")
            else:
                for pkg in to_update:
                    results.append(f"Already latest: {pkg}")

        # Report already-installed packages
        already = set(packages) - set(to_install) - set(to_update)
        for pkg in already:
            results.append(f"Already installed: {pkg}")

    elif state == "absent":
        # Check which packages are actually installed
        to_remove: list[str] = []
        for pkg in packages:
            installed = await _is_installed(pkg)
            if installed:
                to_remove.append(pkg)

        if to_remove:
            pkg_str = " ".join(to_remove)
            stdout, stderr, rc = await _run(f"dnf remove{flags} {pkg_str}")
            if rc != 0:
                raise FTLModuleError(
                    f"Failed to remove {pkg_str}: {stderr}",
                    rc=rc,
                    results=results,
                )
            changed = True
            for pkg in to_remove:
                results.append(f"Removed: {pkg}")

        # Report already-absent packages
        already_absent = set(packages) - set(to_remove)
        for pkg in already_absent:
            results.append(f"Not installed: {pkg}")

    return {"changed": changed, "results": results, "rc": 0}
