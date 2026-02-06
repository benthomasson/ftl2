"""Module proxy for dynamic attribute access.

Enables the ftl.module_name() syntax by intercepting attribute access
and returning async wrappers for module functions.
"""

from typing import Any, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from ftl2.automation.context import AutomationContext


class ModuleProxy:
    """Proxy that enables ftl.module_name() syntax via __getattr__.

    When you access an attribute like `ftl.file`, this proxy intercepts
    the access and returns an async wrapper that calls the FTL module.

    Example:
        proxy = ModuleProxy(context)
        result = await proxy.file(path="/tmp/test", state="touch")
        # Equivalent to: await context.execute("file", {"path": "/tmp/test", "state": "touch"})
    """

    def __init__(self, context: "AutomationContext"):
        """Initialize the proxy with an automation context.

        Args:
            context: The AutomationContext that handles execution
        """
        self._context = context

    def __getattr__(self, name: str) -> Callable[..., Any]:
        """Return async wrapper for the named module.

        Args:
            name: Module name (e.g., "file", "copy", "command")

        Returns:
            Async function that executes the module

        Raises:
            AttributeError: If module doesn't exist
        """
        # Don't intercept private attributes
        if name.startswith("_"):
            raise AttributeError(name)

        # Check if module exists
        from ftl2.ftl_modules import get_module
        module = get_module(name)
        if module is None:
            raise AttributeError(
                f"Module '{name}' not found. "
                f"Available modules: {', '.join(self._context.available_modules)}"
            )

        # Return async wrapper
        async def wrapper(**kwargs: Any) -> dict[str, Any]:
            """Execute the module with the given parameters."""
            return await self._context.execute(name, kwargs)

        # Copy function metadata for better debugging
        wrapper.__name__ = name
        wrapper.__doc__ = f"Execute the '{name}' module."

        return wrapper
