"""Automation context for clean module access.

Provides the AutomationContext class that enables the intuitive
ftl.module_name() syntax for automation scripts.
"""

from typing import Any

from ftl2.automation.proxy import ModuleProxy
from ftl2.ftl_modules import list_modules, ExecuteResult


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
        check_mode: bool = False,
        verbose: bool = False,
    ):
        """Initialize the automation context.

        Args:
            modules: List of module names to enable (None = all modules)
            check_mode: Enable dry-run mode (modules report what would change)
            verbose: Enable verbose output for debugging
        """
        self._enabled_modules = modules
        self.check_mode = check_mode
        self.verbose = verbose
        self._proxy = ModuleProxy(self)
        self._results: list[ExecuteResult] = []

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

        Performs any necessary cleanup.
        """
        # Currently no cleanup needed, but this is where we would:
        # - Close connections
        # - Flush logs
        # - Report summary
        pass
