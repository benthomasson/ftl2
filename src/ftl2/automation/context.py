"""Automation context for clean module access.

Provides the AutomationContext class that enables the intuitive
ftl.module_name() syntax for automation scripts.
"""

import asyncio
import os
import time
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Sequence, TYPE_CHECKING

from ftl2.automation.proxy import ModuleProxy

if TYPE_CHECKING:
    from ftl2.state import State
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

    def __init__(self, secret_names: list[str], vault_secrets: dict[str, str] | None = None):
        """Initialize secrets from environment variables and optionally Vault.

        Args:
            secret_names: List of environment variable names to load
            vault_secrets: Optional mapping of {name: "path#field"} to read from
                HashiCorp Vault KV v2. Requires VAULT_ADDR and VAULT_TOKEN env vars.
        """
        self._secrets: dict[str, str | None] = {}
        self._loaded_names: set[str] = set()

        for name in secret_names:
            value = os.environ.get(name)
            self._secrets[name] = value
            if value is not None:
                self._loaded_names.add(name)

        if vault_secrets:
            from ftl2.vault import read_vault_secrets
            resolved = read_vault_secrets(vault_secrets)
            for name, value in resolved.items():
                self._secrets[name] = value
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
        secret_bindings: dict[str, dict[str, str]] | None = None,
        check_mode: bool = False,
        verbose: bool = False,
        quiet: bool = False,
        on_event: EventCallback | None = None,
        fail_fast: bool = False,
        print_summary: bool = True,
        print_errors: bool = True,
        auto_install_deps: bool = False,
        record_deps: bool = False,
        deps_file: str | Path = ".ftl2-deps.txt",
        modules_file: str | Path = ".ftl2-modules.txt",
        gate_modules: list[str] | str | None = None,
        gate_subsystem: bool = False,
        state_file: str | Path | None = ".ftl2-state.json",
        record: str | Path | None = None,
        replay: str | Path | None = None,
        vault_secrets: dict[str, str] | None = None,
        policy: str | Path | None = None,
        environment: str = "",
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
            secret_bindings: Automatic secret injection for modules. Maps module
                patterns to parameter bindings. Secrets are injected automatically
                so scripts never see the actual values. Format:
                {"module.pattern.*": {"param_name": "ENV_VAR_NAME"}}
                Example:
                    secret_bindings={
                        "community.general.slack": {"token": "SLACK_TOKEN"},
                        "amazon.aws.*": {"aws_access_key_id": "AWS_KEY"},
                    }
            vault_secrets: Mapping of secret names to Vault KV v2 references
                in "path#field" format. Secrets are read from Vault at startup
                and accessible via ftl.secrets["NAME"]. Requires VAULT_ADDR
                and VAULT_TOKEN environment variables. Example:
                    vault_secrets={"DB_PW": "myapp#db_password"}
            check_mode: Enable dry-run mode (modules report what would change)
            verbose: Enable verbose output for debugging
            quiet: Suppress all output (overrides verbose)
            on_event: Callback function for structured events. Receives dict
                with keys: event, module, host, timestamp, and event-specific data.
            fail_fast: Stop execution on first error. When True, raises
                AutomationError on first module failure. Default is False
                (continue and collect errors).
            print_summary: Print per-host summary on context exit. Default is True.
                Shows counts of changed/ok/failed tasks per host.
            print_errors: Print error summary on context exit. Default is True.
                Set to False if you want to handle errors manually.
            auto_install_deps: Automatically install missing Python dependencies
                using uv when an Ansible module requires packages that aren't
                installed. Default is False (report error with install instructions).
            record_deps: Record module dependencies during execution and write
                to deps_file on context exit. Also writes module names to
                modules_file for gate building. Use with auto_install_deps for
                development, then use the generated file for production builds.
            deps_file: Path to write recorded dependencies. Default is
                ".ftl2-deps.txt". Only used when record_deps=True.
            modules_file: Path to write recorded module names. Default is
                ".ftl2-modules.txt". Only used when record_deps=True.
            gate_modules: Modules to bake into the gate for remote execution.
                Accepts a list of module names, "auto" to read from
                modules_file (or record on first run), or None for
                per-task module transfer (default).
            state_file: Path to state file for persisting dynamic hosts and
                resources. When enabled, add_host() writes to state file
                immediately, and hosts are loaded from state on context enter.
                This enables crash recovery and idempotent provisioning.
                Default is None (no state persistence).
            record: Path to JSON file for recording all actions as an audit
                trail. Written on context exit with timestamps, durations,
                parameters, and results for every module execution. Secret
                parameters (from secret_bindings) are excluded. Default is
                None (no recording).
            replay: Path to a previous audit recording JSON file. When provided,
                successful actions from the recording are skipped (returning their
                cached output) and execution resumes from the first unmatched or
                failed action. Matching is positional — action 0 in the current
                run corresponds to action 0 in the replay log. Use with record=
                to write a new audit log that includes both replayed and newly
                executed actions. Default is None (no replay).
            policy: Path to a YAML policy file. When provided, every module
                execution is checked against the policy rules before running.
                A matching deny rule raises PolicyDeniedError. Default is None
                (no policy enforcement).
            environment: Environment label for policy matching (e.g., "prod",
                "staging"). Passed to the policy engine for environment-based
                rules. Default is "" (empty string).
        """
        self._enabled_modules = modules
        self._inventory = self._load_inventory(inventory)

        # Initialize state if state_file provided
        self._state: "State | None" = None
        if state_file is not None:
            from ftl2.state import State, merge_state_into_inventory
            self._state = State(state_file)
            merge_state_into_inventory(self._state, self._inventory)
        self._secrets_proxy = SecretsProxy(secrets or [], vault_secrets=vault_secrets)
        self._secret_bindings = secret_bindings or {}
        self._load_bound_secrets()
        self.check_mode = check_mode
        self.verbose = verbose and not quiet
        self.quiet = quiet
        self._on_event = on_event
        self.fail_fast = fail_fast
        self._print_summary = print_summary
        self._print_errors = print_errors
        self.auto_install_deps = auto_install_deps
        self._record_deps = record_deps
        self._deps_file = Path(deps_file)
        self._modules_file = Path(modules_file)
        self._gate_modules_input = gate_modules
        self._gate_modules: list[str] | None = None  # resolved in __aenter__
        self._gate_subsystem = gate_subsystem
        self._recorded_modules: set[str] = set()
        self._record_file = Path(record) if record else None
        self._replay_actions: list[dict] | None = None
        self._replay_index: int = 0
        if replay is not None:
            import json
            replay_path = Path(replay)
            if replay_path.exists():
                data = json.loads(replay_path.read_text())
                self._replay_actions = data.get("actions", [])
        from ftl2.policy import Policy
        self._policy = Policy.from_file(policy) if policy else Policy.empty()
        self._environment = environment
        self._event_handlers: dict[str, dict[str, list]] = {}  # host -> event_type -> [handlers]
        self._proxy = ModuleProxy(self)
        self._results: list[ExecuteResult] = []
        self._hosts_proxy: HostsProxy | None = None
        self._ssh_connections: dict[str, SSHHost] = {}
        self._remote_runner: "RemoteModuleRunner | None" = None
        self._start_time: float | None = None

    def _load_bound_secrets(self) -> None:
        """Load all secrets referenced in secret_bindings from environment or Vault."""
        env_vars_needed: set[str] = set()
        for bindings in self._secret_bindings.values():
            env_vars_needed.update(bindings.values())

        # Load these secrets — check vault-sourced secrets first, then env
        self._bound_secrets: dict[str, str] = {}
        for env_var in env_vars_needed:
            if env_var in self._secrets_proxy:
                self._bound_secrets[env_var] = self._secrets_proxy[env_var]
            else:
                value = os.environ.get(env_var)
                if value is not None:
                    self._bound_secrets[env_var] = value

    def _get_secret_bindings_for_module(self, module_name: str) -> dict[str, str]:
        """Get secret bindings that apply to a module.

        Args:
            module_name: Full module name (e.g., "community.general.slack")

        Returns:
            Dict of {param_name: secret_value} to inject
        """
        import fnmatch

        injections: dict[str, str] = {}

        for pattern, bindings in self._secret_bindings.items():
            # Check if pattern matches module name
            if fnmatch.fnmatch(module_name, pattern) or pattern == module_name:
                for param_name, env_var in bindings.items():
                    if env_var in self._bound_secrets:
                        injections[param_name] = self._bound_secrets[env_var]

        return injections

    def _check_policy(self, module_name: str, params: dict[str, Any], host: str = "localhost") -> None:
        """Check policy before module execution.

        Args:
            module_name: Name of the module to execute
            params: Module parameters
            host: Target host name

        Raises:
            PolicyDeniedError: If a policy rule denies the action
        """
        from ftl2.policy import PolicyDeniedError
        result = self._policy.evaluate(module_name, params, host, self._environment)
        if not result.permitted:
            raise PolicyDeniedError(
                f"Policy denied {module_name} on {host}: {result.reason}",
                rule=result.rule,
            )

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
                return load_inventory(path, require_hosts=False)
            else:
                # File doesn't exist, return localhost
                return load_localhost()

        if isinstance(inventory, dict):
            # Build inventory from dict
            inv = Inventory()
            for group_name, group_data in inventory.items():
                group = HostGroup(name=group_name)
                if isinstance(group_data, dict) and "hosts" in group_data:
                    hosts_data = group_data["hosts"]
                    # Handle empty hosts dict (hosts: {})
                    if hosts_data:
                        for host_name, host_data in hosts_data.items():
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

    def add_host(
        self,
        hostname: str,
        ansible_host: str | None = None,
        ansible_user: str | None = None,
        ansible_port: int = 22,
        groups: list[str] | None = None,
        **vars: Any,
    ) -> HostConfig:
        """Dynamically add a host to the inventory.

        Useful for provisioning workflows where you create a server
        and then want to configure it immediately.

        Args:
            hostname: Name for the host (e.g., "web01")
            ansible_host: IP address or hostname to connect to.
                         Defaults to hostname if not specified.
            ansible_user: SSH username for the connection
            ansible_port: SSH port (default 22)
            groups: List of group names to add this host to.
                   Groups are created if they don't exist.
            **vars: Additional host variables

        Returns:
            The created HostConfig object

        Example:
            # Provision a server and configure it
            server = await ftl.community.general.linode_v4(label="web01", ...)
            ip = server["instance"]["ipv4"][0]

            ftl.add_host(
                hostname="web01",
                ansible_host=ip,
                ansible_user="root",
                groups=["webservers"],
            )

            # Now run_on works for the new host
            await ftl.run_on("web01", "dnf", name="nginx", state="present")
            await ftl.run_on("webservers", "service", name="nginx", state="started")
        """
        # Create the host config
        host = HostConfig(
            name=hostname,
            ansible_host=ansible_host or hostname,
            ansible_port=ansible_port,
            ansible_user=ansible_user or "",
            ansible_connection="ssh",
            vars=vars,
        )

        # Add to specified groups (create groups if needed)
        group_names = groups or ["ungrouped"]
        for group_name in group_names:
            group = self._inventory.get_group(group_name)
            if group is None:
                group = HostGroup(name=group_name)
                self._inventory.add_group(group)
            group.add_host(host)

        # Invalidate the hosts proxy cache so it picks up the new host
        self._hosts_proxy = None

        # Persist to state file if enabled
        if self._state is not None:
            self._state.add_host(
                name=hostname,
                ansible_host=ansible_host,
                ansible_user=ansible_user,
                ansible_port=ansible_port,
                groups=group_names,
                **vars,
            )

        return host

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
    def state(self) -> "State":
        """Access the state manager for persistent host/resource tracking.

        State enables crash recovery and idempotent provisioning by
        persisting dynamically added hosts and resources to a JSON file.

        Returns:
            State object for has/get/add/remove operations

        Raises:
            RuntimeError: If state_file was not provided to automation()

        Example:
            if not ftl.state.has("minecraft-9"):
                server = await ftl.local.community.general.linode_v4(...)
                ftl.state.add("minecraft-9", {"provider": "linode", ...})
                ftl.add_host("minecraft-9", ansible_host=ip)
        """
        if self._state is None:
            raise RuntimeError(
                "State not available. Enable with: automation(state_file='.ftl2-state.json')"
            )
        return self._state

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

    def __getitem__(self, name: str) -> "HostScopedProxy":
        """Return a HostScopedProxy for the given host or group name.

        Supports names with dashes and other characters that aren't valid
        in Python attributes::

            await ftl["ftl2-scale-0"].hostname(name="ftl2-scale-0")

        Args:
            name: Host or group name

        Returns:
            HostScopedProxy for the target

        Raises:
            KeyError: If the name is not a known host or group
        """
        return self._proxy[name]

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
        original_params = params  # preserve pre-injection params for audit

        # Check replay log before executing
        replay_result = self._try_replay(module_name, "localhost", original_params)
        if replay_result is not None:
            replay_result.params = self._redact_params(module_name, original_params)
            self._results.append(replay_result)
            self._emit_event({
                "event": "module_complete",
                "module": module_name,
                "host": "localhost",
                "success": True,
                "changed": replay_result.changed,
                "check_mode": self.check_mode,
                "duration": 0.0,
                "replayed": True,
                "output": replay_result.output,
            })
            if not self.quiet:
                print(f"  ↩ {module_name}: replayed (skipped)")
            return replay_result.output

        # Inject bound secrets (script never sees these values)
        secret_injections = self._get_secret_bindings_for_module(module_name)
        if secret_injections:
            params = {**secret_injections, **params}  # params can override if explicitly set

        # Check policy before execution
        self._check_policy(module_name, params)

        # Emit start event
        self._emit_event({
            "event": "module_start",
            "module": module_name,
            "host": "localhost",
            "check_mode": self.check_mode,
        })

        # Record module for dependency tracking
        if self._record_deps:
            self._recorded_modules.add(module_name)

        # Execute and track result (check_mode and auto_install_deps passed to executor)
        result = await execute(
            module_name, params,
            check_mode=self.check_mode,
            auto_install_deps=self.auto_install_deps,
        )
        duration = time.time() - start_time
        result.params = self._redact_params(module_name, original_params)
        result.timestamp = start_time
        result.duration = duration
        self._results.append(result)

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
            "output": result.output,
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

    # =========================================================================
    # Gate Event Infrastructure
    # =========================================================================

    def _register_event_handler(
        self, target: str, event_type: str, handler: Any
    ) -> None:
        """Register an event handler for a host and event type.

        Args:
            target: Host name or group name
            event_type: Event type string (e.g., "FileChanged")
            handler: Callback function (sync or async)
        """
        if target not in self._event_handlers:
            self._event_handlers[target] = {}
        if event_type not in self._event_handlers[target]:
            self._event_handlers[target][event_type] = []
        self._event_handlers[target][event_type].append(handler)

    async def _dispatch_event(
        self, host_name: str, event_type: str, data: dict[str, Any]
    ) -> None:
        """Dispatch an event to registered handlers.

        Args:
            host_name: Host that emitted the event
            event_type: Event type string
            data: Event data from the gate
        """
        import asyncio as _asyncio

        # Collect handlers registered for this specific host
        handlers = list(self._event_handlers.get(host_name, {}).get(event_type, []))

        # Also collect handlers registered for groups this host belongs in
        for target, type_handlers in self._event_handlers.items():
            if target == host_name:
                continue
            # Check if target is a group containing this host
            group = self._inventory.get_group(target)
            if group is not None:
                host_names = {h.name for h in group.list_hosts()}
                if host_name in host_names:
                    handlers.extend(type_handlers.get(event_type, []))

        for handler in handlers:
            if _asyncio.iscoroutinefunction(handler):
                await handler(data)
            else:
                handler(data)

        # Also emit through the general on_event callback
        self._emit_event({
            "event": event_type,
            "host": host_name,
            **data,
        })

    async def _send_gate_command(
        self, host: "HostConfig", msg_type: str, data: dict[str, Any]
    ) -> tuple[str, Any]:
        """Send a protocol-level command to a host's gate and read the response.

        Handles interleaved event messages — if an event arrives while
        waiting for a response, it is dispatched and reading continues.

        Args:
            host: Target host configuration
            msg_type: Message type to send
            data: Message data

        Returns:
            Tuple of (response_type, response_data)
        """
        from ftl2.message import GateProtocol

        gate = await self._get_or_create_gate(host)

        await self._remote_runner.protocol.send_message(
            gate.gate_process.stdin, msg_type, data
        )

        while True:
            response = await self._remote_runner.protocol.read_message(
                gate.gate_process.stdout
            )
            if response is None:
                raise ConnectionError(f"Gate connection closed for {host.name}")

            resp_type, resp_data = response

            # If this is an event message, dispatch it and keep reading
            if resp_type in GateProtocol.EVENT_TYPES:
                await self._dispatch_event(host.name, resp_type, resp_data)
                continue

            # Cache gate back for reuse
            self._remote_runner.gate_cache[host.name] = gate
            return resp_type, resp_data

    async def listen(self, timeout: float | None = None) -> None:
        """Listen for events from all active gate connections.

        Reads event messages from gates and dispatches them to
        registered handlers. Blocks until timeout expires or all
        gate connections close.

        Args:
            timeout: Maximum time in seconds to listen, or None for
                indefinite (until cancelled or connections close).
        """
        import asyncio as _asyncio
        from ftl2.message import GateProtocol

        if self._remote_runner is None:
            return

        gates = dict(self._remote_runner.gate_cache)
        if not gates:
            return

        async def _listen_one(host_name: str, gate: "Gate") -> None:
            while True:
                msg = await self._remote_runner.protocol.read_message(
                    gate.gate_process.stdout
                )
                if msg is None:
                    break
                msg_type, data = msg
                if msg_type in GateProtocol.EVENT_TYPES:
                    await self._dispatch_event(host_name, msg_type, data)

        tasks = [_listen_one(name, gate) for name, gate in gates.items()]

        try:
            if timeout is not None:
                await _asyncio.wait_for(
                    _asyncio.gather(*tasks), timeout=timeout
                )
            else:
                await _asyncio.gather(*tasks)
        except (_asyncio.TimeoutError, _asyncio.CancelledError, KeyboardInterrupt):
            pass

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
                error_msg = str(result) or f"{type(result).__name__}"
                final_results.append(
                    ExecuteResult.from_error(
                        error_msg,
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
        original_params = params  # preserve pre-injection params for audit

        # Check replay log before executing
        replay_result = self._try_replay(module_name, host.name, original_params)
        if replay_result is not None:
            replay_result.params = self._redact_params(module_name, original_params)
            self._emit_event({
                "event": "module_complete",
                "module": module_name,
                "host": host.name,
                "success": True,
                "changed": replay_result.changed,
                "check_mode": self.check_mode,
                "duration": 0.0,
                "replayed": True,
                "output": replay_result.output,
            })
            if not self.quiet:
                print(f"  ↩ {host.name}:{module_name}: replayed (skipped)")
            return replay_result

        # Record module for dependency tracking
        if self._record_deps:
            self._recorded_modules.add(module_name)

        # Inject bound secrets (script never sees these values)
        secret_injections = self._get_secret_bindings_for_module(module_name)
        if secret_injections:
            params = {**secret_injections, **params}

        # Check policy before execution
        self._check_policy(module_name, params, host.name)

        # Emit start event
        self._emit_event({
            "event": "module_start",
            "module": module_name,
            "host": host.name,
            "check_mode": self.check_mode,
        })

        if host.is_local:
            # Local execution
            result = await execute(
                module_name, params,
                check_mode=self.check_mode,
                auto_install_deps=self.auto_install_deps,
            )
            result.host = host.name
        else:
            # Remote execution via gate
            result = await self._execute_remote_via_gate(host, module_name, params)
            result.host = host.name

        duration = time.time() - start_time
        result.params = self._redact_params(module_name, original_params)
        result.timestamp = start_time
        result.duration = duration

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
            "output": result.output,
        })

        # Log based on output mode
        if self.verbose and not self.quiet:
            self._log_result(f"{host.name}:{module_name}", result, duration)
        elif not self.quiet and not result.success:
            self._log_error(f"{host.name}:{module_name}", result)

        # Note: fail_fast is not applied here because run_on uses asyncio.gather
        # for concurrent execution. Use ftl.failed and ftl.errors after run_on.

        return result

    async def _execute_remote_via_gate(
        self,
        host: HostConfig,
        module_name: str,
        params: dict[str, Any],
    ) -> ExecuteResult:
        """Execute module on remote host via gate process.

        Uses the gate for connection pooling and efficient module execution.
        FTL modules are executed via FTLModule messages (in-process Python),
        while Ansible modules use Module messages (subprocess execution).

        Args:
            host: Target host configuration
            module_name: Module to execute
            params: Module parameters

        Returns:
            ExecuteResult with execution outcome
        """
        from ftl2.ftl_modules.executor import is_ftl_module, get_ftl_module_source, ExecuteResult
        from ftl2.runners import ExecutionContext, Gate
        from ftl2.types import ExecutionConfig, GateConfig
        from getpass import getuser
        import sys

        if self._remote_runner is None:
            raise RuntimeError("RemoteModuleRunner not initialized - use 'async with' context manager")

        ftl_attempted = False
        if is_ftl_module(module_name):
            # FTL module - try name-only first (gate may have it baked in)
            try:
                gate = await self._get_or_create_gate(host)

                # Send name-only FTLModule message
                await self._remote_runner.protocol.send_message(
                    gate.gate_process.stdin,
                    "FTLModule",
                    {
                        "module_name": module_name,
                        "module_args": params,
                    },
                )
                response = await self._remote_runner.protocol.read_message(gate.gate_process.stdout)

                if response is not None and response[0] == "ModuleNotFound":
                    # Not baked in — send source
                    source = get_ftl_module_source(module_name)
                    result_data = await self._remote_runner.run_ftl_module(
                        gate, module_name, source, params
                    )
                elif response is not None and response[0] == "FTLModuleResult":
                    result_data = dict(response[1])
                    # Gate wraps module output in {"result": ...} — unwrap it
                    if "result" in result_data and isinstance(result_data["result"], dict):
                        result_data = result_data["result"]
                elif response is not None and response[0] == "Error":
                    raise Exception(response[1].get("message", "Unknown FTL module error"))
                else:
                    raise Exception(f"Unexpected response: {response}")

                # Cache gate for reuse
                self._remote_runner.gate_cache[host.name] = gate
                ftl_attempted = True
            except Exception as e:
                # FTL module failed (missing deps, etc.) - fall back to Ansible bundle
                error_msg = str(e)
                if "No module named" in error_msg or "ImportError" in error_msg:
                    ftl_attempted = False
                else:
                    raise

        if not ftl_attempted:
            # Ansible module - build bundle and send through gate
            import json

            # Get gate connection
            gate = await self._get_or_create_gate(host)

            # Try name-only first (gate may have module baked in)
            await self._remote_runner.protocol.send_message(
                gate.gate_process.stdin,
                "Module",
                {
                    "module_name": module_name,
                    "module_args": params,
                },
            )

            response = await self._remote_runner.protocol.read_message(gate.gate_process.stdout)

            if response is not None and response[0] == "ModuleNotFound":
                # Module not in gate — build bundle and retry
                from ftl2.module_loading.bundle import build_bundle_from_fqcn
                import base64

                if "." not in module_name:
                    fqcn = f"ansible.builtin.{module_name}"
                else:
                    fqcn = module_name

                bundle = build_bundle_from_fqcn(fqcn)
                bundle_b64 = base64.b64encode(bundle.data).decode()
                await self._remote_runner.protocol.send_message(
                    gate.gate_process.stdin,
                    "Module",
                    {
                        "module": bundle_b64,
                        "module_name": module_name,
                        "module_args": params,
                    },
                )
                response = await self._remote_runner.protocol.read_message(gate.gate_process.stdout)

            if response is None:
                result_data = {"failed": True, "msg": "No response from gate"}
            else:
                msg_type, data = response
                if msg_type == "ModuleResult":
                    # Parse the stdout as JSON (Ansible module output)
                    stdout = data.get("stdout", "")
                    stderr = data.get("stderr", "")
                    try:
                        result_data = json.loads(stdout) if stdout.strip() else {}
                        if stderr:
                            result_data["_stderr"] = stderr
                        if not result_data:
                            result_data = {
                                "failed": True,
                                "msg": f"Empty response from module. stderr: {stderr}",
                            }
                        # Module crashed during import/execution — stderr has
                        # a traceback but stdout has no failure indicator.
                        if stderr and "Traceback" in stderr and not result_data.get("failed"):
                            result_data["failed"] = True
                            result_data["msg"] = f"Module crashed: {stderr.strip().splitlines()[-1]}"
                    except json.JSONDecodeError as e:
                        result_data = {
                            "failed": True,
                            "msg": f"Invalid JSON response: {e}",
                            "stdout": stdout,
                            "stderr": stderr,
                        }
                elif msg_type == "Error":
                    result_data = {"failed": True, "msg": data.get("message", "Unknown error")}
                else:
                    result_data = {"failed": True, "msg": f"Unexpected response: {msg_type}"}

            # Cache gate for reuse
            self._remote_runner.gate_cache[host.name] = gate

        # Convert to ExecuteResult
        failed = result_data.get("failed", False)
        return ExecuteResult(
            success=not failed,
            changed=result_data.get("changed", False),
            output=result_data,
            error=result_data.get("msg", "") if failed else "",
            module=module_name,
            host=host.name,
            used_ftl=is_ftl_module(module_name),
        )

    async def _get_or_create_gate(self, host: HostConfig) -> "Gate":
        """Get or create a gate connection for a host.

        Args:
            host: Target host configuration

        Returns:
            Active Gate connection
        """
        from ftl2.runners import ExecutionContext
        from ftl2.types import ExecutionConfig, GateConfig
        from getpass import getuser
        import sys

        if self._remote_runner is None:
            raise RuntimeError("RemoteModuleRunner not initialized")

        # Check cache first
        if host.name in self._remote_runner.gate_cache:
            gate = self._remote_runner.gate_cache.pop(host.name)
            return gate

        # Create new gate
        ssh_host = host.ansible_host or host.name
        ssh_port = host.ansible_port or 22
        ssh_user = host.ansible_user or getuser()
        ssh_password = host.vars.get("ansible_password")
        ssh_key_file = host.vars.get("ssh_private_key_file")
        # For remote hosts, default to /usr/bin/python3 (not the local
        # interpreter which won't exist on the remote machine).
        connection = getattr(host, "ansible_connection", "ssh")
        if connection == "local":
            interpreter = host.ansible_python_interpreter or sys.executable
        else:
            interpreter = host.ansible_python_interpreter or "/usr/bin/python3"

        context = ExecutionContext(
            execution_config=ExecutionConfig(
                module_name="ping",
                modules=self._gate_modules or [],
                dry_run=self.check_mode,
            ),
            gate_config=GateConfig(),
        )

        return await self._remote_runner._connect_gate(
            ssh_host, ssh_port, ssh_user, ssh_password, ssh_key_file, interpreter, context,
            register_subsystem=self._gate_subsystem,
        )

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
        from ftl2.runners import RemoteModuleRunner
        from ftl2.gate import GateBuilder

        self._remote_runner = RemoteModuleRunner()
        self._remote_runner.gate_builder = GateBuilder()
        self._resolve_gate_modules()
        self._start_time = time.time()
        return self

    def _resolve_gate_modules(self) -> None:
        """Resolve gate_modules parameter into a concrete module list.

        - None: no pre-built gate (per-task transfer, current behavior)
        - list[str]: use these modules for gate building
        - "auto": read from modules_file if it exists, otherwise
          enable recording so the file is written on first run
        """
        if self._gate_modules_input is None:
            self._gate_modules = None
        elif isinstance(self._gate_modules_input, list):
            self._gate_modules = list(self._gate_modules_input)
        elif self._gate_modules_input == "auto":
            if self._modules_file.exists():
                text = self._modules_file.read_text().strip()
                if text:
                    self._gate_modules = text.splitlines()
                    if not self.quiet:
                        print(f"Loaded {len(self._gate_modules)} modules from {self._modules_file}")
                else:
                    self._gate_modules = None
            else:
                # First run — enable recording so the file gets written on exit
                self._record_deps = True
                self._gate_modules = None
                if not self.quiet:
                    print(f"No {self._modules_file} found, recording modules for next run")
        else:
            raise ValueError(
                f"gate_modules must be a list, 'auto', or None, got: {self._gate_modules_input!r}"
            )

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        """Exit the async context manager.

        Performs cleanup including closing SSH connections and
        optionally printing summary and errors.
        """
        # Print summary if enabled and there are results
        if self._print_summary and self._results and not self.quiet:
            self._print_host_summary()

        # Print errors if enabled and any occurred
        if self._print_errors and self.failed and not self.quiet:
            print(f"\nERRORS ({len(self.errors)}):")
            for error in self.errors:
                host = getattr(error, "host", "localhost") or "localhost"
                print(f"  {error.module} on {host}: {error.error}")

        # Write recorded dependencies and module list if enabled
        if self._record_deps and self._recorded_modules:
            self._write_recorded_deps()
            self._write_recorded_modules()

        # Write audit recording if enabled
        if self._record_file and self._results:
            self._write_recording()

        # Close gate connections
        if self._remote_runner:
            await self._remote_runner.close_all()

        await self._close_ssh_connections()

    def _write_recorded_deps(self) -> None:
        """Write recorded module dependencies to file.

        Resolves each executed module to extract its Python package
        requirements from the DOCUMENTATION string, then writes them
        in requirements.txt format.
        """
        import re
        from ftl2.module_loading.fqcn import resolve_fqcn
        from ftl2.module_loading.requirements import get_module_requirements

        deps: dict[str, tuple[str, str]] = {}  # package -> (version, source_module)

        for fqcn in sorted(self._recorded_modules):
            try:
                module_path = resolve_fqcn(fqcn)
                reqs = get_module_requirements(module_path)

                for req in reqs.requirements:
                    package, version = self._parse_requirement(req)
                    if package not in deps:
                        deps[package] = (version, fqcn)
            except Exception:
                continue  # Skip modules we can't resolve

        if deps:
            lines = []
            for package in sorted(deps.keys()):
                version, source = deps[package]
                if version:
                    lines.append(f"{package}{version}  # {source}")
                else:
                    lines.append(f"{package}  # {source}")

            self._deps_file.write_text("\n".join(lines) + "\n")
            if not self.quiet:
                print(f"\nRecorded dependencies saved to {self._deps_file}")

    def _write_recorded_modules(self) -> None:
        """Write recorded module names to file for gate building.

        Writes one module name per line, using FQCNs for collection modules.
        This file can be used by ftl-gate-builder to build a gate with
        all needed modules baked in, eliminating per-task bundle transfers.
        """
        lines = sorted(self._recorded_modules)
        self._modules_file.write_text("\n".join(lines) + "\n")
        if not self.quiet:
            print(f"Recorded modules saved to {self._modules_file}")

    _SENSITIVE_HEADERS = frozenset({
        "authorization",
        "x-api-key",
        "x-auth-token",
        "cookie",
        "proxy-authorization",
    })

    _HTTP_MODULES = frozenset({"uri", "ftl_uri", "get_url", "ftl_get_url"})

    def _redact_params(self, module_name: str, params: dict[str, Any]) -> dict[str, Any]:
        """Redact sensitive values from params before storing in audit results.

        For HTTP modules (uri, get_url), redacts known sensitive header keys
        and the bearer_token parameter.
        """
        # Strip FQCN prefix for matching (e.g. ansible.builtin.uri -> uri)
        short_name = module_name.rsplit(".", 1)[-1] if "." in module_name else module_name
        if short_name not in self._HTTP_MODULES:
            return params

        # Check if there's anything to redact
        has_headers = isinstance(params.get("headers"), dict)
        has_bearer = "bearer_token" in params
        has_password = "url_password" in params
        if not has_headers and not has_bearer and not has_password:
            return params

        redacted = dict(params)

        if has_headers:
            redacted_headers = {}
            for k, v in params["headers"].items():
                if k.lower() in self._SENSITIVE_HEADERS:
                    redacted_headers[k] = "***"
                else:
                    redacted_headers[k] = v
            redacted["headers"] = redacted_headers

        if has_bearer:
            redacted["bearer_token"] = "***"

        if has_password:
            redacted["url_password"] = "***"

        return redacted

    def _try_replay(self, module_name: str, host: str, params: dict) -> ExecuteResult | None:
        """Check if the current action can be satisfied from the replay log.

        Returns an ExecuteResult with cached output if the action matches
        and was successful, or None if the action should execute normally.
        """
        if self._replay_actions is None:
            return None
        if self._replay_index >= len(self._replay_actions):
            return None

        action = self._replay_actions[self._replay_index]

        # Must match module and host
        if action["module"] != module_name or action["host"] != host:
            # Mismatch — stop replaying, execute everything from here
            self._replay_actions = None
            return None

        # Only replay successes — re-execute failures
        if not action.get("success", False):
            self._replay_actions = None
            return None

        # Match — return cached result
        self._replay_index += 1
        return ExecuteResult(
            success=True,
            changed=action.get("changed", False),
            output=action.get("output", {}),
            module=module_name,
            host=host,
            params=params,
            duration=0.0,
            timestamp=time.time(),
            replayed=True,
        )

    def _write_recording(self) -> None:
        """Write JSON audit trail of all actions to file.

        Records every module execution with timestamps, durations,
        parameters, and results. Secret parameters are excluded
        (params are captured before secret injection).
        """
        import json
        from datetime import datetime, timezone

        def epoch_to_iso(epoch: float) -> str:
            return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()

        actions = []
        for r in self._results:
            action = {
                "module": r.module,
                "host": r.host,
                "params": r.params,
                "success": r.success,
                "changed": r.changed,
                "duration": round(r.duration, 3),
                "timestamp": epoch_to_iso(r.timestamp) if r.timestamp else None,
                "output": r.output,
            }
            if not r.success:
                action["error"] = r.error
            if r.replayed:
                action["replayed"] = True
            actions.append(action)

        now = time.time()
        recording = {
            "started": epoch_to_iso(self._start_time) if self._start_time else None,
            "completed": epoch_to_iso(now),
            "check_mode": self.check_mode,
            "success": not self.failed,
            "actions": actions,
            "errors": [
                {"module": e.module, "host": e.host, "error": e.error}
                for e in self.errors
            ],
        }

        self._record_file.write_text(json.dumps(recording, indent=2) + "\n")
        if not self.quiet:
            print(f"Audit recording saved to {self._record_file}")

    @staticmethod
    def _parse_requirement(req: str) -> tuple[str, str]:
        """Parse 'package >= 1.0.0' into ('package', '>=1.0.0')."""
        import re
        match = re.match(r'^([a-zA-Z0-9_-]+)\s*(.*)$', req.strip())
        if match:
            return match.group(1), match.group(2).strip()
        return req.strip(), ""

    def _print_host_summary(self) -> None:
        """Print per-host summary of what was done."""
        from collections import defaultdict

        # Group results by host
        by_host: dict[str, dict[str, int]] = defaultdict(
            lambda: {"changed": 0, "ok": 0, "failed": 0}
        )

        for result in self._results:
            host = getattr(result, "host", "localhost") or "localhost"
            if not result.success:
                by_host[host]["failed"] += 1
            elif result.changed:
                by_host[host]["changed"] += 1
            else:
                by_host[host]["ok"] += 1

        print("\nSUMMARY:")
        for host, counts in by_host.items():
            total = counts["changed"] + counts["ok"] + counts["failed"]
            parts = []
            if counts["changed"]:
                parts.append(f"{counts['changed']} changed")
            if counts["ok"]:
                parts.append(f"{counts['ok']} ok")
            if counts["failed"]:
                parts.append(f"{counts['failed']} failed")
            print(f"  {host}: {total} tasks ({', '.join(parts)})")
