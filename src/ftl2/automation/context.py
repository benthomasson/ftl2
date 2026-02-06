"""Automation context for clean module access.

Provides the AutomationContext class that enables the intuitive
ftl.module_name() syntax for automation scripts.
"""

import asyncio
import os
import time
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Sequence

from ftl2.automation.proxy import ModuleProxy
from ftl2.ftl_modules import list_modules, ExecuteResult
from ftl2.inventory import Inventory, HostGroup, load_inventory, load_localhost
from ftl2.types import HostConfig
from ftl2.ssh import SSHHost


class OutputMode(Enum):
    """Output modes for automation context.

    Attributes:
        QUIET: Suppress all output
        NORMAL: Show errors only
        VERBOSE: Show all execution details
        EVENTS: Emit structured events (for programmatic consumption)
    """

    QUIET = "quiet"
    NORMAL = "normal"
    VERBOSE = "verbose"
    EVENTS = "events"


# Type alias for event callbacks
EventCallback = Callable[[dict[str, Any]], None]


class AutomationError(Exception):
    """Error raised when automation fails with fail_fast=True.

    Attributes:
        result: The ExecuteResult that caused the failure
        message: Error message
    """

    def __init__(self, message: str, result: "ExecuteResult | None" = None):
        super().__init__(message)
        self.result = result
        self.message = message

    def __str__(self) -> str:
        if self.result:
            return f"{self.message} (module: {self.result.module}, host: {self.result.host})"
        return self.message


class SecretsProxy:
    """Proxy for secure access to secrets.

    Provides dictionary-like access to secrets loaded from environment
    variables. Secrets are never logged or exposed in string representations.

    Example:
        ftl.secrets["AWS_ACCESS_KEY_ID"]  # Get secret value
        "API_KEY" in ftl.secrets          # Check if secret exists
        ftl.secrets.keys()                # List secret names (not values)
    """

    def __init__(self, secret_names: list[str]):
        """Initialize secrets from environment variables.

        Args:
            secret_names: List of environment variable names to load
        """
        self._secrets: dict[str, str | None] = {}
        self._loaded_names: set[str] = set()

        for name in secret_names:
            value = os.environ.get(name)
            self._secrets[name] = value
            if value is not None:
                self._loaded_names.add(name)

    def __getitem__(self, key: str) -> str:
        """Get a secret value.

        Args:
            key: Secret name

        Returns:
            Secret value

        Raises:
            KeyError: If secret not found or not set
        """
        if key not in self._secrets:
            raise KeyError(f"Secret '{key}' was not requested in automation(secrets=[...])")

        value = self._secrets[key]
        if value is None:
            raise KeyError(f"Secret '{key}' is not set in environment")

        return value

    def get(self, key: str, default: str | None = None) -> str | None:
        """Get a secret value with optional default.

        Args:
            key: Secret name
            default: Default value if not found

        Returns:
            Secret value or default
        """
        try:
            return self[key]
        except KeyError:
            return default

    def __contains__(self, key: str) -> bool:
        """Check if a secret exists and is set."""
        return key in self._loaded_names

    def keys(self) -> list[str]:
        """Get list of requested secret names (not values)."""
        return list(self._secrets.keys())

    def loaded_keys(self) -> list[str]:
        """Get list of secrets that were successfully loaded."""
        return list(self._loaded_names)

    def __len__(self) -> int:
        """Number of loaded secrets."""
        return len(self._loaded_names)

    def __repr__(self) -> str:
        """Safe representation that doesn't expose values."""
        loaded = list(self._loaded_names)
        missing = [k for k in self._secrets if k not in self._loaded_names]
        return f"SecretsProxy(loaded={loaded}, missing={missing})"

    def __str__(self) -> str:
        """Safe string that doesn't expose values."""
        return f"<SecretsProxy: {len(self._loaded_names)} secrets loaded>"


class HostsProxy:
    """Proxy for accessing hosts and groups from inventory.

    Enables dictionary-like access to hosts and groups:

        ftl.hosts["web01"]           # Get specific host
        ftl.hosts["webservers"]      # Get all hosts in group
        ftl.hosts.all                # Get all hosts
        ftl.hosts.groups             # Get group names
    """

    def __init__(self, inventory: Inventory):
        self._inventory = inventory

    def __getitem__(self, key: str) -> list[HostConfig]:
        """Get host(s) by name or group name.

        Args:
            key: Host name or group name

        Returns:
            List of HostConfig objects

        Raises:
            KeyError: If host/group not found
        """
        # Check if it's a group
        group = self._inventory.get_group(key)
        if group is not None:
            return group.list_hosts()

        # Check if it's a host
        all_hosts = self._inventory.get_all_hosts()
        if key in all_hosts:
            return [all_hosts[key]]

        raise KeyError(f"Host or group '{key}' not found in inventory")

    def __contains__(self, key: str) -> bool:
        """Check if host or group exists."""
        if self._inventory.get_group(key) is not None:
            return True
        return key in self._inventory.get_all_hosts()

    @property
    def all(self) -> list[HostConfig]:
        """Get all hosts in inventory."""
        return list(self._inventory.get_all_hosts().values())

    @property
    def groups(self) -> list[str]:
        """Get all group names."""
        return [g.name for g in self._inventory.list_groups()]

    def keys(self) -> list[str]:
        """Get all host names."""
        return list(self._inventory.get_all_hosts().keys())

    def __iter__(self):
        """Iterate over host names."""
        return iter(self._inventory.get_all_hosts().keys())

    def __len__(self) -> int:
        """Number of hosts."""
        return len(self._inventory.get_all_hosts())


class AutomationContext:
    """Context for automation scripts with clean module access.

    Provides an intuitive interface for executing FTL modules:

        async with AutomationContext() as ftl:
            await ftl.file(path="/tmp/test", state="touch")
            await ftl.copy(src="config.yml", dest="/etc/app/")

    The context manager handles setup and teardown, while the proxy
    pattern enables the clean ftl.module_name() syntax.

    Attributes:
        modules: List of enabled module names (None = all)
        check_mode: Whether to run in dry-run mode
        verbose: Whether to enable verbose output
        quiet: Whether to suppress all output
        output_mode: Output mode (quiet, normal, verbose, events)
    """

    def __init__(
        self,
        modules: list[str] | None = None,
        inventory: str | Path | Inventory | dict[str, Any] | None = None,
        secrets: list[str] | None = None,
        check_mode: bool = False,
        verbose: bool = False,
        quiet: bool = False,
        on_event: EventCallback | None = None,
        fail_fast: bool = False,
    ):
        """Initialize the automation context.

        Args:
            modules: List of module names to enable (None = all modules)
            inventory: Inventory source - can be:
                - Path string or Path object to YAML inventory file
                - Inventory object directly
                - Dict with inventory structure
                - None for localhost-only execution
            secrets: List of environment variable names to load as secrets.
                Secrets are accessed via ftl.secrets["NAME"] and are never
                logged or exposed in string representations.
            check_mode: Enable dry-run mode (modules report what would change)
            verbose: Enable verbose output for debugging
            quiet: Suppress all output (overrides verbose)
            on_event: Callback function for structured events. Receives dict
                with keys: event, module, host, timestamp, and event-specific data.
            fail_fast: Stop execution on first error. When True, raises
                AutomationError on first module failure. Default is False
                (continue and collect errors).
        """
        self._enabled_modules = modules
        self._inventory = self._load_inventory(inventory)
        self._secrets_proxy = SecretsProxy(secrets or [])
        self.check_mode = check_mode
        self.verbose = verbose and not quiet
        self.quiet = quiet
        self._on_event = on_event
        self.fail_fast = fail_fast
        self._proxy = ModuleProxy(self)
        self._results: list[ExecuteResult] = []
        self._hosts_proxy: HostsProxy | None = None
        self._ssh_connections: dict[str, SSHHost] = {}
        self._start_time: float | None = None

    @property
    def output_mode(self) -> OutputMode:
        """Get the current output mode."""
        if self.quiet:
            return OutputMode.QUIET
        if self._on_event is not None:
            return OutputMode.EVENTS
        if self.verbose:
            return OutputMode.VERBOSE
        return OutputMode.NORMAL

    @property
    def failed(self) -> bool:
        """Check if any module execution has failed.

        Returns:
            True if any result has success=False

        Example:
            async with automation() as ftl:
                await ftl.file(path="/nonexistent", state="touch")
                if ftl.failed:
                    print("Something went wrong!")
        """
        return any(not r.success for r in self._results)

    @property
    def errors(self) -> list[ExecuteResult]:
        """Get all failed execution results.

        Returns:
            List of ExecuteResult objects where success=False

        Example:
            async with automation() as ftl:
                await ftl.file(path="/nonexistent", state="touch")
                for error in ftl.errors:
                    print(f"{error.module} on {error.host}: {error.error}")
        """
        return [r for r in self._results if not r.success]

    @property
    def error_messages(self) -> list[str]:
        """Get error messages from all failed executions.

        Returns:
            List of error message strings

        Example:
            if ftl.failed:
                for msg in ftl.error_messages:
                    print(f"Error: {msg}")
        """
        return [r.error for r in self._results if not r.success and r.error]

    def _load_inventory(
        self, inventory: str | Path | Inventory | dict[str, Any] | None
    ) -> Inventory:
        """Load inventory from various sources.

        Args:
            inventory: Inventory source

        Returns:
            Loaded Inventory object
        """
        if inventory is None:
            # Default to localhost-only
            return load_localhost()

        if isinstance(inventory, Inventory):
            return inventory

        if isinstance(inventory, (str, Path)):
            path = Path(inventory)
            if path.exists():
                return load_inventory(path)
            else:
                # File doesn't exist, return localhost
                return load_localhost()

        if isinstance(inventory, dict):
            # Build inventory from dict
            inv = Inventory()
            for group_name, group_data in inventory.items():
                group = HostGroup(name=group_name)
                if isinstance(group_data, dict) and "hosts" in group_data:
                    for host_name, host_data in group_data["hosts"].items():
                        host_data = host_data or {}
                        host = HostConfig(
                            name=host_name,
                            ansible_host=host_data.get("ansible_host", host_name),
                            ansible_port=host_data.get("ansible_port", 22),
                            ansible_user=host_data.get("ansible_user", ""),
                            ansible_connection=host_data.get("ansible_connection", "ssh"),
                        )
                        group.add_host(host)
                inv.add_group(group)
            return inv

        return load_localhost()

    @property
    def hosts(self) -> HostsProxy:
        """Access hosts from the inventory.

        Returns:
            HostsProxy for dictionary-like host access

        Example:
            ftl.hosts["web01"]       # Get specific host
            ftl.hosts["webservers"]  # Get all hosts in group
            ftl.hosts.all            # Get all hosts
        """
        if self._hosts_proxy is None:
            self._hosts_proxy = HostsProxy(self._inventory)
        return self._hosts_proxy

    @property
    def secrets(self) -> SecretsProxy:
        """Access secrets loaded from environment variables.

        Secrets are loaded from environment variables specified in the
        automation(secrets=[...]) parameter. Values are never logged or
        exposed in string representations.

        Returns:
            SecretsProxy for dictionary-like secret access

        Example:
            ftl.secrets["AWS_ACCESS_KEY_ID"]  # Get secret value
            "API_KEY" in ftl.secrets          # Check if secret exists
            ftl.secrets.get("KEY", "default") # Get with default
            ftl.secrets.keys()                # List requested secret names
            ftl.secrets.loaded_keys()         # List successfully loaded secrets
        """
        return self._secrets_proxy

    @property
    def available_modules(self) -> list[str]:
        """List of available module names."""
        all_modules = list_modules()
        if self._enabled_modules is not None:
            return [m for m in self._enabled_modules if m in all_modules]
        return all_modules

    @property
    def results(self) -> list[ExecuteResult]:
        """List of all execution results from this context."""
        return self._results.copy()

    def __getattr__(self, name: str) -> Any:
        """Delegate attribute access to the module proxy.

        This enables the ftl.module_name() syntax by forwarding
        unknown attribute access to the ModuleProxy.

        Args:
            name: Attribute name (module name)

        Returns:
            Async wrapper function for the module
        """
        # Don't intercept private attributes or known attributes
        if name.startswith("_"):
            raise AttributeError(name)

        # Check if it's an enabled module
        if self._enabled_modules is not None and name not in self._enabled_modules:
            if name in list_modules():
                raise AttributeError(
                    f"Module '{name}' is not enabled. "
                    f"Enabled modules: {', '.join(self._enabled_modules)}"
                )

        return getattr(self._proxy, name)

    async def execute(self, module_name: str, params: dict[str, Any]) -> dict[str, Any]:
        """Execute a module with the given parameters.

        This is the internal method called by the proxy. It handles
        check_mode injection and result tracking.

        Args:
            module_name: Name of the module to execute
            params: Module parameters

        Returns:
            Module output dictionary
        """
        from ftl2.ftl_modules import execute

        start_time = time.time()

        # Emit start event
        self._emit_event({
            "event": "module_start",
            "module": module_name,
            "host": "localhost",
            "check_mode": self.check_mode,
        })

        # Execute and track result (check_mode passed to executor)
        result = await execute(module_name, params, check_mode=self.check_mode)
        self._results.append(result)

        duration = time.time() - start_time

        # Emit complete event
        self._emit_event({
            "event": "module_complete",
            "module": module_name,
            "host": "localhost",
            "success": result.success,
            "changed": result.changed,
            "check_mode": self.check_mode,
            "duration": duration,
            "error": result.error,
        })

        # Log in verbose mode (not quiet)
        if self.verbose and not self.quiet:
            self._log_result(module_name, result, duration)
        elif not self.quiet and not result.success:
            # In normal mode, show errors
            self._log_error(module_name, result)

        # Fail fast if enabled and module failed
        if self.fail_fast and not result.success:
            raise AutomationError(
                f"Module '{module_name}' failed: {result.error}",
                result=result,
            )

        return result.output

    def _emit_event(self, event: dict[str, Any]) -> None:
        """Emit an event to the callback if registered."""
        if self._on_event is not None:
            event["timestamp"] = time.time()
            self._on_event(event)

    def _log_result(
        self, module_name: str, result: ExecuteResult, duration: float | None = None
    ) -> None:
        """Log execution result in verbose mode."""
        status = "ok" if result.success else "FAILED"
        changed = " (changed)" if result.changed else ""
        check = " [CHECK MODE]" if self.check_mode else ""
        timing = f" ({duration:.2f}s)" if duration is not None else ""
        print(f"[{module_name}] {status}{changed}{check}{timing}")
        if result.error:
            print(f"  Error: {result.error}")

    def _log_error(self, module_name: str, result: ExecuteResult) -> None:
        """Log error in normal mode."""
        print(f"[{module_name}] FAILED: {result.error}")

    async def run_on(
        self,
        hosts: str | HostConfig | Sequence[HostConfig],
        module_name: str,
        **params: Any,
    ) -> list[ExecuteResult]:
        """Execute a module on remote host(s).

        Args:
            hosts: Target host(s) - can be:
                - String (host name or group name)
                - Single HostConfig
                - List of HostConfig objects
            module_name: Name of the module to execute
            **params: Module parameters as keyword arguments

        Returns:
            List of ExecuteResult objects, one per host

        Example:
            # Run on specific hosts
            results = await ftl.run_on("webservers", "file", path="/var/www", state="directory")

            # Run on host list
            results = await ftl.run_on(ftl.hosts["db-servers"], "command", cmd="pg_dump mydb")
        """
        # Resolve hosts
        if isinstance(hosts, str):
            host_list = self.hosts[hosts]
        elif isinstance(hosts, HostConfig):
            host_list = [hosts]
        else:
            host_list = list(hosts)

        # Execute on all hosts concurrently
        tasks = [
            self._execute_on_host(host, module_name, params)
            for host in host_list
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Convert exceptions to error results
        final_results: list[ExecuteResult] = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                final_results.append(
                    ExecuteResult.from_error(
                        str(result),
                        module_name,
                        host_list[i].name,
                    )
                )
            else:
                final_results.append(result)

        self._results.extend(final_results)
        return final_results

    async def _execute_on_host(
        self,
        host: HostConfig,
        module_name: str,
        params: dict[str, Any],
    ) -> ExecuteResult:
        """Execute module on a single host."""
        from ftl2.ftl_modules import execute

        start_time = time.time()

        # Emit start event
        self._emit_event({
            "event": "module_start",
            "module": module_name,
            "host": host.name,
            "check_mode": self.check_mode,
        })

        if host.is_local:
            # Local execution
            result = await execute(module_name, params, check_mode=self.check_mode)
            result.host = host.name
        else:
            # Remote execution via SSH
            ssh_host = await self._get_ssh_connection(host)
            result = await execute(module_name, params, host=ssh_host, check_mode=self.check_mode)
            result.host = host.name

        duration = time.time() - start_time

        # Emit complete event
        self._emit_event({
            "event": "module_complete",
            "module": module_name,
            "host": host.name,
            "success": result.success,
            "changed": result.changed,
            "check_mode": self.check_mode,
            "duration": duration,
            "error": result.error,
        })

        # Log based on output mode
        if self.verbose and not self.quiet:
            self._log_result(f"{host.name}:{module_name}", result, duration)
        elif not self.quiet and not result.success:
            self._log_error(f"{host.name}:{module_name}", result)

        # Note: fail_fast is not applied here because run_on uses asyncio.gather
        # for concurrent execution. Use ftl.failed and ftl.errors after run_on.

        return result

    async def _get_ssh_connection(self, host: HostConfig) -> SSHHost:
        """Get or create SSH connection for a host."""
        if host.name not in self._ssh_connections:
            # Get password from host vars if available
            password = host.vars.get("ansible_password") or host.vars.get("ansible_ssh_pass")

            ssh_host = SSHHost(
                hostname=host.ansible_host,
                port=host.ansible_port,
                username=host.ansible_user or None,
                password=password,
                known_hosts=None,  # Disable for automation
            )
            await ssh_host.connect()
            self._ssh_connections[host.name] = ssh_host

        return self._ssh_connections[host.name]

    async def _close_ssh_connections(self) -> None:
        """Close all SSH connections."""
        for ssh_host in self._ssh_connections.values():
            await ssh_host.disconnect()
        self._ssh_connections.clear()

    async def __aenter__(self) -> "AutomationContext":
        """Enter the async context manager."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        """Exit the async context manager.

        Performs cleanup including closing SSH connections.
        """
        await self._close_ssh_connections()
