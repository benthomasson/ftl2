"""Module runner interfaces and implementations for FTL2.

This module defines the strategy pattern for module execution, providing
pluggable runners for local and remote execution with a common interface.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from .types import ExecutionConfig, GateConfig, HostConfig, ModuleResult


@dataclass
class ExecutionContext:
    """Context for module execution operations.

    Bundles all configuration needed for executing modules across an
    inventory, reducing function parameters from 11 to 1.

    Attributes:
        execution_config: Module execution configuration
        gate_config: Gate building and caching configuration
        module_dirs_override: Optional override for module directories

    Example:
        >>> from pathlib import Path
        >>> exec_config = ExecutionConfig(
        ...     module_name="ping",
        ...     module_dirs=[Path("/usr/lib/ftl/modules")]
        ... )
        >>> gate_config = GateConfig()
        >>> context = ExecutionContext(
        ...     execution_config=exec_config,
        ...     gate_config=gate_config
        ... )
    """

    execution_config: ExecutionConfig
    gate_config: GateConfig
    module_dirs_override: list[str] = field(default_factory=list)

    @property
    def module_name(self) -> str:
        """Get the module name from execution config."""
        return self.execution_config.module_name

    @property
    def module_args(self) -> dict[str, Any]:
        """Get module arguments from execution config."""
        return self.execution_config.module_args


class ModuleRunner(ABC):
    """Abstract base class for module execution strategies.

    Defines the interface for executing modules on hosts, enabling
    pluggable execution strategies (local vs remote) with a common API.

    This follows the Strategy pattern, allowing runtime selection of
    execution method based on host configuration.
    """

    @abstractmethod
    async def run(
        self,
        host: HostConfig,
        context: ExecutionContext,
    ) -> ModuleResult:
        """Execute a module on a single host.

        Args:
            host: Host configuration for execution target
            context: Execution context with module and gate config

        Returns:
            ModuleResult containing execution outcome

        Raises:
            Exception: Various exceptions depending on implementation
        """
        pass

    @abstractmethod
    async def cleanup(self) -> None:
        """Clean up any resources held by this runner.

        Called when the runner is no longer needed. Implementations
        should close connections, release resources, etc.
        """
        pass


class ModuleRunnerFactory:
    """Factory for creating appropriate module runners.

    Selects the correct runner implementation based on host configuration,
    providing a unified interface for module execution.

    Example:
        >>> factory = ModuleRunnerFactory()
        >>> local_host = HostConfig(
        ...     name="localhost",
        ...     ansible_host="127.0.0.1",
        ...     ansible_connection="local"
        ... )
        >>> runner = factory.create_runner(local_host)
        >>> # Returns LocalModuleRunner
    """

    def __init__(self) -> None:
        """Initialize the factory."""
        self._local_runner: LocalModuleRunner | None = None
        self._remote_runner: RemoteModuleRunner | None = None

    def create_runner(self, host: HostConfig) -> ModuleRunner:
        """Create appropriate runner for the given host.

        Args:
            host: Host configuration to determine runner type

        Returns:
            ModuleRunner instance (Local or Remote)
        """
        if host.is_local:
            if self._local_runner is None:
                self._local_runner = LocalModuleRunner()
            return self._local_runner
        else:
            if self._remote_runner is None:
                self._remote_runner = RemoteModuleRunner()
            return self._remote_runner

    async def cleanup_all(self) -> None:
        """Clean up all created runners."""
        if self._local_runner:
            await self._local_runner.cleanup()
        if self._remote_runner:
            await self._remote_runner.cleanup()


class LocalModuleRunner(ModuleRunner):
    """Runner for executing modules locally without SSH.

    Executes modules directly on the local system using subprocess,
    bypassing SSH for improved performance on localhost operations.

    Example:
        >>> runner = LocalModuleRunner()
        >>> context = ExecutionContext(
        ...     execution_config=ExecutionConfig(module_name="ping"),
        ...     gate_config=GateConfig()
        ... )
        >>> host = HostConfig(
        ...     name="localhost",
        ...     ansible_host="127.0.0.1",
        ...     ansible_connection="local"
        ... )
        >>> result = await runner.run(host, context)
        >>> result.is_success
        True
    """

    async def run(
        self,
        host: HostConfig,
        context: ExecutionContext,
    ) -> ModuleResult:
        """Execute a module locally.

        Args:
            host: Host configuration (should be local)
            context: Execution context

        Returns:
            ModuleResult with execution outcome

        Raises:
            NotImplementedError: Implementation pending
        """
        # TODO: Implement local module execution
        # This will be implemented in the next step
        raise NotImplementedError("Local module execution not yet implemented")

    async def cleanup(self) -> None:
        """Clean up local runner resources.

        Local runner has no persistent resources, so this is a no-op.
        """
        pass


class RemoteModuleRunner(ModuleRunner):
    """Runner for executing modules remotely via SSH gates.

    Manages SSH connections, gate processes, and remote module execution
    with connection pooling and caching for performance.

    Attributes:
        gate_cache: Cache of active gate connections by host

    Example:
        >>> runner = RemoteModuleRunner()
        >>> context = ExecutionContext(
        ...     execution_config=ExecutionConfig(module_name="ping"),
        ...     gate_config=GateConfig()
        ... )
        >>> host = HostConfig(
        ...     name="web01",
        ...     ansible_host="192.168.1.10"
        ... )
        >>> result = await runner.run(host, context)
    """

    def __init__(self) -> None:
        """Initialize the remote runner with empty gate cache."""
        self.gate_cache: dict[str, Any] = {}

    async def run(
        self,
        host: HostConfig,
        context: ExecutionContext,
    ) -> ModuleResult:
        """Execute a module remotely via SSH gate.

        Args:
            host: Remote host configuration
            context: Execution context

        Returns:
            ModuleResult with execution outcome

        Raises:
            NotImplementedError: Implementation pending
        """
        # TODO: Implement remote module execution via gates
        # This will be implemented after local runner
        raise NotImplementedError("Remote module execution not yet implemented")

    async def cleanup(self) -> None:
        """Clean up gate connections and resources.

        Closes all cached gate connections and clears the cache.
        """
        # TODO: Implement gate cleanup
        # Close all gates in gate_cache
        self.gate_cache.clear()
