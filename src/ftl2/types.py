"""Type definitions for FTL2 automation framework.

This module defines the core data types used throughout FTL2, replacing
dictionary-based configurations with strongly-typed dataclasses. These types
provide type safety, validation, and clear interfaces that are portable to Go.
"""

from dataclasses import dataclass, field
from getpass import getuser
from pathlib import Path
from typing import Any


@dataclass
class HostConfig:
    """Configuration for a single host in the automation inventory.

    Represents all connection and configuration details needed to execute
    automation tasks on a specific host. Follows Ansible inventory conventions
    while providing type safety and validation.

    Attributes:
        name: Unique identifier for the host (e.g., "web01", "db-primary")
        ansible_host: Target hostname or IP address for SSH connection
        ansible_port: SSH port number (default: 22)
        ansible_user: Username for SSH authentication (default: current user)
        ansible_connection: Connection type - "ssh" for remote, "local" for localhost
        ansible_python_interpreter: Path to Python interpreter on target host
        vars: Additional host-specific variables as key-value pairs

    Example:
        >>> host = HostConfig(
        ...     name="web01",
        ...     ansible_host="192.168.1.10",
        ...     ansible_user="admin"
        ... )
        >>> host.ansible_port
        22
        >>> host.is_local
        False

        >>> localhost = HostConfig(
        ...     name="localhost",
        ...     ansible_host="127.0.0.1",
        ...     ansible_connection="local"
        ... )
        >>> localhost.is_local
        True
    """

    name: str
    ansible_host: str
    ansible_port: int = 22
    ansible_user: str = field(default_factory=getuser)
    ansible_connection: str = "ssh"
    ansible_python_interpreter: str = "python3"
    vars: dict[str, Any] = field(default_factory=dict)

    @property
    def is_local(self) -> bool:
        """Check if this host uses local execution (no SSH)."""
        return self.ansible_connection == "local"

    @property
    def is_remote(self) -> bool:
        """Check if this host uses remote execution (SSH)."""
        return not self.is_local

    def get_var(self, key: str, default: Any = None) -> Any:
        """Get a host variable by key with optional default.

        Args:
            key: Variable name to retrieve
            default: Default value if key not found

        Returns:
            Variable value or default
        """
        return self.vars.get(key, default)

    def set_var(self, key: str, value: Any) -> None:
        """Set a host variable.

        Args:
            key: Variable name
            value: Variable value
        """
        self.vars[key] = value


@dataclass
class ExecutionConfig:
    """Configuration for module execution operations.

    Defines what module to run, where to find it, what arguments to pass,
    and any dependencies required.

    Attributes:
        module_name: Name of the module to execute (e.g., "ping", "setup")
        module_dirs: Directories to search for the module
        module_args: Arguments to pass to the module (supports Ref objects)
        host_args: Host-specific argument overrides (higher precedence than module_args)
        modules: Additional modules to include in gate builds
        dependencies: Python packages required by the module
        dry_run: If True, show what would happen without executing

    Example:
        >>> config = ExecutionConfig(
        ...     module_name="setup",
        ...     module_dirs=[Path("/usr/lib/ftl/modules")],
        ...     module_args={"gather_subset": "all"},
        ...     host_args={"web1": {"gather_subset": "minimal"}}
        ... )
    """

    module_name: str
    module_dirs: list[Path] = field(default_factory=list)
    module_args: dict[str, Any] = field(default_factory=dict)
    host_args: dict[str, dict[str, Any]] = field(default_factory=dict)
    modules: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    dry_run: bool = False

    def __post_init__(self) -> None:
        """Validate and normalize configuration after initialization."""
        # Ensure module_name is in modules list
        if self.module_name not in self.modules:
            self.modules.append(self.module_name)

        # Convert string paths to Path objects
        self.module_dirs = [Path(d) if isinstance(d, str) else d for d in self.module_dirs]


@dataclass
class GateConfig:
    """Configuration for FTL gate management.

    Gates are self-contained Python executables deployed to remote hosts
    for efficient module execution. This configuration controls gate
    building and caching.

    Attributes:
        interpreter: Python interpreter path on remote host
        local_interpreter: Python interpreter path for building gates
        cache_dir: Directory for caching built gates
        use_cache: Whether to use cached gates when available

    Example:
        >>> config = GateConfig(
        ...     interpreter="/usr/bin/python3",
        ...     cache_dir=Path.home() / ".ftl2" / "gates"
        ... )
    """

    interpreter: str = "python3"
    local_interpreter: str = "python3"
    cache_dir: Path | None = None
    use_cache: bool = True

    def __post_init__(self) -> None:
        """Set up default cache directory if not specified."""
        if self.cache_dir is None:
            self.cache_dir = Path.home() / ".ftl2" / "gates"

        # Convert string to Path if needed
        if isinstance(self.cache_dir, str):
            self.cache_dir = Path(self.cache_dir)

        # Ensure cache directory exists
        if self.use_cache and self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)


@dataclass
class ModuleResult:
    """Result from executing a module on a host.

    Attributes:
        host_name: Name of the host where module was executed
        success: Whether the execution succeeded
        changed: Whether the module made changes
        output: Module output data
        error: Error message if execution failed

    Example:
        >>> result = ModuleResult(
        ...     host_name="web01",
        ...     success=True,
        ...     changed=False,
        ...     output={"ping": "pong"}
        ... )
        >>> result.is_success
        True
    """

    host_name: str
    success: bool
    changed: bool = False
    output: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    @property
    def is_success(self) -> bool:
        """Check if execution was successful."""
        return self.success

    @property
    def is_failure(self) -> bool:
        """Check if execution failed."""
        return not self.success

    @classmethod
    def success_result(
        cls, host_name: str, output: dict[str, Any], changed: bool = False
    ) -> "ModuleResult":
        """Create a successful result.

        Args:
            host_name: Host name
            output: Module output
            changed: Whether changes were made

        Returns:
            ModuleResult indicating success
        """
        return cls(host_name=host_name, success=True, changed=changed, output=output)

    @classmethod
    def error_result(cls, host_name: str, error: str) -> "ModuleResult":
        """Create an error result.

        Args:
            host_name: Host name
            error: Error message

        Returns:
            ModuleResult indicating failure
        """
        return cls(
            host_name=host_name,
            success=False,
            output={"error": True, "msg": error},
            error=error,
        )
