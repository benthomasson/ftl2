"""Module proxy for dynamic attribute access.

Enables the ftl.module_name() syntax by intercepting attribute access
and returning async wrappers for module functions.

Supports both simple modules and FQCN (Fully Qualified Collection Name):
    await ftl.file(path="/tmp/test", state="touch")
    await ftl.amazon.aws.ec2_instance(instance_type="t3.micro")
"""

from typing import Any, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from ftl2.automation.context import AutomationContext


class NamespaceProxy:
    """Proxy for FQCN namespace traversal.

    Enables dotted access like ftl.amazon.aws.ec2_instance by tracking
    the namespace path and returning nested proxies until the final
    module is called.

    Example:
        ftl.amazon        -> NamespaceProxy(context, "amazon")
        ftl.amazon.aws    -> NamespaceProxy(context, "amazon.aws")
        ftl.amazon.aws.ec2_instance(...) -> executes "amazon.aws.ec2_instance"
    """

    def __init__(self, context: "AutomationContext", path: str):
        """Initialize the namespace proxy.

        Args:
            context: The AutomationContext that handles execution
            path: The current namespace path (e.g., "amazon" or "amazon.aws")
        """
        self._context = context
        self._path = path

    def __getattr__(self, name: str) -> "NamespaceProxy":
        """Return a nested proxy for the next namespace component.

        Args:
            name: Next component of the namespace

        Returns:
            NamespaceProxy with extended path
        """
        if name.startswith("_"):
            raise AttributeError(name)

        # Extend the path
        new_path = f"{self._path}.{name}"
        return NamespaceProxy(self._context, new_path)

    async def __call__(self, **kwargs: Any) -> dict[str, Any]:
        """Execute the module at the current path.

        This is called when the namespace proxy is invoked as a function,
        e.g., ftl.amazon.aws.ec2_instance(instance_type="t3.micro")

        Args:
            **kwargs: Module parameters

        Returns:
            Module output dictionary
        """
        return await self._context.execute(self._path, kwargs)

    def __repr__(self) -> str:
        return f"NamespaceProxy({self._path!r})"


class ModuleProxy:
    """Proxy that enables ftl.module_name() syntax via __getattr__.

    When you access an attribute like `ftl.file`, this proxy intercepts
    the access and returns an async wrapper that calls the FTL module.

    For simple modules (file, copy, command), it returns a callable wrapper.
    For namespaced modules (amazon.aws.ec2_instance), it returns a
    NamespaceProxy that enables chained attribute access.

    Example:
        proxy = ModuleProxy(context)

        # Simple module
        result = await proxy.file(path="/tmp/test", state="touch")

        # FQCN module (collection)
        result = await proxy.amazon.aws.ec2_instance(instance_type="t3.micro")
    """

    def __init__(self, context: "AutomationContext"):
        """Initialize the proxy with an automation context.

        Args:
            context: The AutomationContext that handles execution
        """
        self._context = context

    def __getattr__(self, name: str) -> Callable[..., Any] | NamespaceProxy:
        """Return async wrapper for module or namespace proxy for collections.

        Args:
            name: Module name or namespace (e.g., "file", "amazon")

        Returns:
            Async function for known modules, NamespaceProxy for namespaces

        Raises:
            AttributeError: If module doesn't exist and isn't a valid namespace
        """
        # Don't intercept private attributes
        if name.startswith("_"):
            raise AttributeError(name)

        # Check if it's a known simple module first
        from ftl2.ftl_modules import get_module, list_modules

        module = get_module(name)
        if module is not None:
            # Known module - return async wrapper
            async def wrapper(**kwargs: Any) -> dict[str, Any]:
                """Execute the module with the given parameters."""
                return await self._context.execute(name, kwargs)

            wrapper.__name__ = name
            wrapper.__doc__ = f"Execute the '{name}' module."
            return wrapper

        # Check if it's in the enabled modules list (if restricted)
        if self._context._enabled_modules is not None:
            if name in list_modules():
                raise AttributeError(
                    f"Module '{name}' is not enabled. "
                    f"Enabled modules: {', '.join(self._context._enabled_modules)}"
                )

        # Not a known simple module - treat as namespace for FQCN
        # This enables: ftl.amazon.aws.ec2_instance(...)
        return NamespaceProxy(self._context, name)
