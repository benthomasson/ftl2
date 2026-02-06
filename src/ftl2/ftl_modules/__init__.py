"""FTL Modules - In-process Python functions for 250x faster execution.

FTL modules are Python functions that work like Ansible modules but run
in-process with no subprocess overhead. They use async/await for
concurrent execution without forking.

Usage:
    from ftl2.ftl_modules import get_module, FTL_MODULES

    # Get module by short name
    file_module = get_module("file")

    # Get module by Ansible FQCN
    copy_module = get_module("ansible.builtin.copy")

    # Execute directly
    result = ftl_copy(src="/tmp/foo", dest="/tmp/bar")
"""

from typing import Any, Callable

from ftl2.ftl_modules.exceptions import (
    FTLModuleError,
    FTLModuleCheckModeError,
    FTLModuleNotFoundError,
)
from ftl2.ftl_modules.file import ftl_file, ftl_copy, ftl_template
from ftl2.ftl_modules.http import ftl_uri, ftl_get_url
from ftl2.ftl_modules.command import ftl_command, ftl_shell
from ftl2.ftl_modules.pip import ftl_pip
from ftl2.ftl_modules.aws import ftl_ec2_instance

# Type for module functions
ModuleFunc = Callable[..., dict[str, Any]]

# Registry maps short names to module functions
FTL_MODULES: dict[str, ModuleFunc] = {
    "file": ftl_file,
    "copy": ftl_copy,
    "template": ftl_template,
    "uri": ftl_uri,
    "get_url": ftl_get_url,
    "command": ftl_command,
    "shell": ftl_shell,
    "pip": ftl_pip,
    "ec2_instance": ftl_ec2_instance,
}

# Maps Ansible FQCNs to FTL module functions for compatibility
ANSIBLE_COMPAT: dict[str, ModuleFunc] = {
    # ansible.builtin modules
    "ansible.builtin.file": ftl_file,
    "ansible.builtin.copy": ftl_copy,
    "ansible.builtin.template": ftl_template,
    "ansible.builtin.uri": ftl_uri,
    "ansible.builtin.get_url": ftl_get_url,
    "ansible.builtin.command": ftl_command,
    "ansible.builtin.shell": ftl_shell,
    "ansible.builtin.pip": ftl_pip,
    # AWS modules
    "amazon.aws.ec2_instance": ftl_ec2_instance,
}


def get_module(name: str) -> ModuleFunc | None:
    """Get an FTL module by name or FQCN.

    Checks the short name registry first, then the Ansible FQCN
    compatibility mapping.

    Args:
        name: Module short name (e.g., "file") or FQCN (e.g., "ansible.builtin.file")

    Returns:
        Module function if found, None otherwise

    Example:
        >>> module = get_module("copy")
        >>> module = get_module("ansible.builtin.copy")
    """
    return FTL_MODULES.get(name) or ANSIBLE_COMPAT.get(name)


def has_ftl_module(name: str) -> bool:
    """Check if an FTL module exists for the given name.

    Args:
        name: Module short name or FQCN

    Returns:
        True if an FTL module exists
    """
    return get_module(name) is not None


def list_modules() -> list[str]:
    """List all available FTL module short names.

    Returns:
        List of module short names
    """
    return list(FTL_MODULES.keys())


def list_ansible_compat() -> list[str]:
    """List all Ansible FQCNs with FTL implementations.

    Returns:
        List of Ansible FQCNs
    """
    return list(ANSIBLE_COMPAT.keys())


__all__ = [
    # Exceptions
    "FTLModuleError",
    "FTLModuleCheckModeError",
    "FTLModuleNotFoundError",
    # Registry
    "FTL_MODULES",
    "ANSIBLE_COMPAT",
    "get_module",
    "has_ftl_module",
    "list_modules",
    "list_ansible_compat",
    # File modules
    "ftl_file",
    "ftl_copy",
    "ftl_template",
    # HTTP modules
    "ftl_uri",
    "ftl_get_url",
    # Command modules
    "ftl_command",
    "ftl_shell",
    # Package modules
    "ftl_pip",
    # AWS modules
    "ftl_ec2_instance",
]
