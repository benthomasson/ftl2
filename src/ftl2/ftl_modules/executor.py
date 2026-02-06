"""FTL Module Executor.

Provides async execution of modules with automatic path selection:
- FTL modules: Called directly in-process (250x faster)
- Ansible modules: Fall back to module_loading executor
- Remote hosts: Async SSH execution

This is the main entry point for running modules in FTL2.
"""

import asyncio
import inspect
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

from ftl2.ftl_modules.exceptions import FTLModuleError

logger = logging.getLogger(__name__)


@dataclass
class ExecuteResult:
    """Result of module execution.

    Attributes:
        success: Whether execution succeeded
        changed: Whether the module made changes
        output: The module's output dictionary
        error: Error message if failed
        module: Module name that was executed
        host: Host name (if remote execution)
        used_ftl: Whether FTL module was used (vs Ansible fallback)
    """

    success: bool
    changed: bool = False
    output: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    module: str = ""
    host: str = "localhost"
    used_ftl: bool = True

    @classmethod
    def from_module_output(
        cls,
        output: dict[str, Any],
        module: str,
        host: str = "localhost",
        used_ftl: bool = True,
    ) -> "ExecuteResult":
        """Create result from module output dict."""
        failed = output.get("failed", False)
        return cls(
            success=not failed,
            changed=output.get("changed", False),
            output=output,
            error=output.get("msg", "") if failed else "",
            module=module,
            host=host,
            used_ftl=used_ftl,
        )

    @classmethod
    def from_error(
        cls,
        error: str,
        module: str,
        host: str = "localhost",
    ) -> "ExecuteResult":
        """Create a failure result from an error."""
        return cls(
            success=False,
            changed=False,
            output={"failed": True, "msg": error},
            error=error,
            module=module,
            host=host,
            used_ftl=False,
        )


class RemoteHost(Protocol):
    """Protocol for remote host execution."""

    @property
    def name(self) -> str:
        """Host name."""
        ...

    @property
    def is_local(self) -> bool:
        """Whether this is localhost."""
        ...

    async def run(
        self,
        command: str,
        stdin: str = "",
        timeout: int = 300,
    ) -> tuple[str, str, int]:
        """Run a command on the remote host.

        Returns:
            Tuple of (stdout, stderr, return_code)
        """
        ...


@dataclass
class LocalHost:
    """Represents localhost for execution."""

    name: str = "localhost"
    is_local: bool = True


def _get_module(name: str) -> Any:
    """Get module by name, avoiding circular import.

    This imports from the registry at runtime to avoid
    circular import issues with the main __init__.py.
    """
    # Import here to avoid circular import
    from ftl2.ftl_modules.file import ftl_file, ftl_copy, ftl_template
    from ftl2.ftl_modules.http import ftl_uri, ftl_get_url
    from ftl2.ftl_modules.command import ftl_command, ftl_shell
    from ftl2.ftl_modules.pip import ftl_pip
    from ftl2.ftl_modules.aws.ec2 import ftl_ec2_instance

    # Local registry to avoid circular import
    modules = {
        "file": ftl_file,
        "copy": ftl_copy,
        "template": ftl_template,
        "uri": ftl_uri,
        "get_url": ftl_get_url,
        "command": ftl_command,
        "shell": ftl_shell,
        "pip": ftl_pip,
        "ec2_instance": ftl_ec2_instance,
        # FQCN mappings
        "ansible.builtin.file": ftl_file,
        "ansible.builtin.copy": ftl_copy,
        "ansible.builtin.template": ftl_template,
        "ansible.builtin.uri": ftl_uri,
        "ansible.builtin.get_url": ftl_get_url,
        "ansible.builtin.command": ftl_command,
        "ansible.builtin.shell": ftl_shell,
        "ansible.builtin.pip": ftl_pip,
        "amazon.aws.ec2_instance": ftl_ec2_instance,
    }
    return modules.get(name)


async def execute(
    module_name: str,
    params: dict[str, Any],
    host: RemoteHost | LocalHost | None = None,
    check_mode: bool = False,
) -> ExecuteResult:
    """Execute a module with automatic path selection.

    This is the main entry point for module execution. It automatically
    chooses the fastest execution path:

    1. If FTL module exists and host is local: call directly (250x faster)
    2. If no FTL module and host is local: fall back to Ansible module
    3. If host is remote: use async SSH execution

    Args:
        module_name: Module short name or FQCN (e.g., "file" or "ansible.builtin.file")
        params: Module parameters
        host: Target host (None or LocalHost for localhost)
        check_mode: Whether to run in check mode

    Returns:
        ExecuteResult with success status, output, and metadata

    Example:
        # Local execution with FTL module
        result = await execute("file", {"path": "/tmp/test", "state": "touch"})

        # Remote execution
        result = await execute("command", {"cmd": "ls"}, host=remote_host)
    """
    # Default to localhost
    if host is None:
        host = LocalHost()

    host_name = host.name
    is_local = host.is_local

    # Check if FTL module exists
    ftl_module = _get_module(module_name)

    try:
        if is_local and ftl_module is not None:
            # Fast path: FTL module, local execution
            logger.debug(f"Executing FTL module '{module_name}' locally")
            output = await _execute_ftl_module(ftl_module, params, check_mode)
            return ExecuteResult.from_module_output(
                output, module_name, host_name, used_ftl=True
            )

        elif is_local and ftl_module is None:
            # Fallback: Ansible module, local execution
            logger.debug(f"Falling back to Ansible module '{module_name}' locally")
            output = await _execute_ansible_module_local(module_name, params, check_mode)
            return ExecuteResult.from_module_output(
                output, module_name, host_name, used_ftl=False
            )

        else:
            # Remote execution via SSH
            logger.debug(f"Executing module '{module_name}' on remote host '{host_name}'")
            output = await _execute_remote(host, module_name, params, check_mode)
            return ExecuteResult.from_module_output(
                output, module_name, host_name, used_ftl=False
            )

    except FTLModuleError as e:
        logger.error(f"Module '{module_name}' failed: {e}")
        return ExecuteResult(
            success=False,
            changed=e.result.get("changed", False),
            output=e.result,
            error=str(e),
            module=module_name,
            host=host_name,
            used_ftl=ftl_module is not None,
        )
    except Exception as e:
        logger.error(f"Module '{module_name}' failed with unexpected error: {e}")
        return ExecuteResult.from_error(str(e), module_name, host_name)


async def execute_on_hosts(
    hosts: list[RemoteHost | LocalHost],
    module_name: str,
    params: dict[str, Any],
    check_mode: bool = False,
) -> list[ExecuteResult]:
    """Execute a module on multiple hosts concurrently.

    Uses asyncio.gather() for concurrent execution - no forking required.
    This is much more efficient than Ansible's fork-based approach.

    Args:
        hosts: List of target hosts
        module_name: Module short name or FQCN
        params: Module parameters (same for all hosts)
        check_mode: Whether to run in check mode

    Returns:
        List of ExecuteResult, one per host, in same order as hosts

    Example:
        hosts = [host1, host2, host3]
        results = await execute_on_hosts(hosts, "command", {"cmd": "uptime"})
        for result in results:
            print(f"{result.host}: {result.output.get('stdout', '')}")
    """
    tasks = [
        execute(module_name, params, host, check_mode)
        for host in hosts
    ]
    return await asyncio.gather(*tasks)


async def execute_batch(
    tasks_list: list[tuple[str, dict[str, Any], RemoteHost | LocalHost | None]],
    check_mode: bool = False,
) -> list[ExecuteResult]:
    """Execute multiple different modules concurrently.

    Useful for running different modules in parallel, e.g., installing
    packages on one host while configuring files on another.

    Args:
        tasks_list: List of (module_name, params, host) tuples
        check_mode: Whether to run in check mode

    Returns:
        List of ExecuteResult, one per task, in same order

    Example:
        tasks = [
            ("file", {"path": "/tmp/a", "state": "touch"}, None),
            ("command", {"cmd": "echo hello"}, None),
        ]
        results = await execute_batch(tasks)
    """
    coroutines = [
        execute(module_name, params, host, check_mode)
        for module_name, params, host in tasks_list
    ]
    return await asyncio.gather(*coroutines)


async def _execute_ftl_module(
    module_func: Any,
    params: dict[str, Any],
    check_mode: bool = False,
) -> dict[str, Any]:
    """Execute an FTL module function.

    Handles both sync and async module functions.
    FTL modules may optionally accept a check_mode parameter.
    """
    # Get function signature to check if it accepts check_mode
    sig = inspect.signature(module_func)
    accepts_check_mode = "check_mode" in sig.parameters

    # Prepare params - pass check_mode if the function accepts it
    if accepts_check_mode:
        params = {**params, "check_mode": check_mode}

    # Check if module is async
    if inspect.iscoroutinefunction(module_func):
        result = await module_func(**params)
    else:
        # Sync function - run in thread pool to avoid blocking
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, lambda: module_func(**params))

    # If check_mode is enabled but module doesn't support it,
    # add a note to the result
    if check_mode and not accepts_check_mode and isinstance(result, dict):
        result = {**result, "_check_mode_unsupported": True}

    return result


async def _execute_ansible_module_local(
    module_name: str,
    params: dict[str, Any],
    check_mode: bool = False,
) -> dict[str, Any]:
    """Execute an Ansible module locally via module_loading.

    Falls back to the module_loading executor for modules without
    FTL implementations.
    """
    try:
        from ftl2.module_loading.executor import execute_local_fqcn

        # Normalize to FQCN if needed
        if "." not in module_name:
            # Try ansible.builtin first
            fqcn = f"ansible.builtin.{module_name}"
        else:
            fqcn = module_name

        result = execute_local_fqcn(fqcn, params, check_mode=check_mode)

        if result.success:
            return result.output
        else:
            return {
                "failed": True,
                "msg": result.error,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }

    except ImportError:
        return {
            "failed": True,
            "msg": f"Module '{module_name}' not found and module_loading not available",
        }
    except Exception as e:
        return {
            "failed": True,
            "msg": f"Ansible module execution failed: {e}",
        }


async def _execute_remote(
    host: RemoteHost,
    module_name: str,
    params: dict[str, Any],
    check_mode: bool = False,
) -> dict[str, Any]:
    """Execute a module on a remote host via SSH.

    Uses the module_loading bundle system for remote execution.
    """
    try:
        from ftl2.module_loading.executor import execute_remote_with_staging
        from ftl2.module_loading.bundle import build_bundle_from_fqcn

        # Normalize to FQCN
        if "." not in module_name:
            fqcn = f"ansible.builtin.{module_name}"
        else:
            fqcn = module_name

        # Build bundle
        bundle = build_bundle_from_fqcn(fqcn)

        # Execute on remote
        result = await execute_remote_with_staging(
            host, bundle, params, check_mode=check_mode
        )

        if result.success:
            return result.output
        else:
            return {
                "failed": True,
                "msg": result.error,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }

    except ImportError:
        return {
            "failed": True,
            "msg": "Remote execution requires module_loading package",
        }
    except Exception as e:
        return {
            "failed": True,
            "msg": f"Remote execution failed: {e}",
        }


# Convenience functions for common patterns


async def run(module_name: str, **params: Any) -> ExecuteResult:
    """Convenience function for local execution.

    Example:
        result = await run("file", path="/tmp/test", state="touch")
    """
    return await execute(module_name, params)


async def run_on(
    host: RemoteHost | LocalHost,
    module_name: str,
    **params: Any,
) -> ExecuteResult:
    """Convenience function for execution on specific host.

    Example:
        result = await run_on(my_host, "command", cmd="uptime")
    """
    return await execute(module_name, params, host)
