"""Automation context for clean module access.

Provides the AutomationContext class that enables the intuitive
ftl.module_name() syntax for automation scripts.
"""

import asyncio
from pathlib import Path
from typing import Any, Sequence

from ftl2.automation.proxy import ModuleProxy
from ftl2.ftl_modules import list_modules, ExecuteResult
from ftl2.inventory import Inventory, HostGroup, load_inventory, load_localhost
from ftl2.types import HostConfig
from ftl2.ssh import SSHHost


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
    """

    def __init__(
        self,
        modules: list[str] | None = None,
        inventory: str | Path | Inventory | dict[str, Any] | None = None,
        check_mode: bool = False,
        verbose: bool = False,
    ):
        """Initialize the automation context.

        Args:
            modules: List of module names to enable (None = all modules)
            inventory: Inventory source - can be:
                - Path string or Path object to YAML inventory file
                - Inventory object directly
                - Dict with inventory structure
                - None for localhost-only execution
            check_mode: Enable dry-run mode (modules report what would change)
            verbose: Enable verbose output for debugging
        """
        self._enabled_modules = modules
        self._inventory = self._load_inventory(inventory)
        self.check_mode = check_mode
        self.verbose = verbose
        self._proxy = ModuleProxy(self)
        self._results: list[ExecuteResult] = []
        self._hosts_proxy: HostsProxy | None = None
        self._ssh_connections: dict[str, SSHHost] = {}

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

        # Inject check_mode if enabled
        if self.check_mode:
            params = {**params, "_ansible_check_mode": True}

        # Execute and track result
        result = await execute(module_name, params, check_mode=self.check_mode)
        self._results.append(result)

        if self.verbose:
            self._log_result(module_name, result)

        return result.output

    def _log_result(self, module_name: str, result: ExecuteResult) -> None:
        """Log execution result in verbose mode."""
        status = "ok" if result.success else "FAILED"
        changed = " (changed)" if result.changed else ""
        print(f"[{module_name}] {status}{changed}")
        if result.error:
            print(f"  Error: {result.error}")

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

        if host.is_local:
            # Local execution
            result = await execute(module_name, params, check_mode=self.check_mode)
            result.host = host.name
            if self.verbose:
                self._log_result(f"{host.name}:{module_name}", result)
            return result

        # Remote execution via SSH
        ssh_host = await self._get_ssh_connection(host)

        # Use the ftl_modules executor with host
        result = await execute(module_name, params, host=ssh_host, check_mode=self.check_mode)
        result.host = host.name

        if self.verbose:
            self._log_result(f"{host.name}:{module_name}", result)

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
