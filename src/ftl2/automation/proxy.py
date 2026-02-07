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


class HostScopedProxy:
    """Proxy that runs modules on a specific host or group.

    Enables syntax like ftl.webservers.service(...) which is equivalent to
    ftl.run_on("webservers", "service", ...).

    Example:
        ftl.webservers.service(name="nginx", state="restarted")
        ftl.web01.file(path="/tmp/test", state="touch")
        ftl.local.community.general.linode_v4(label="web01", ...)
    """

    def __init__(self, context: "AutomationContext", target: str):
        """Initialize the host-scoped proxy.

        Args:
            context: The AutomationContext that handles execution
            target: Host name or group name to target
        """
        self._context = context
        self._target = target

    def __getattr__(self, name: str) -> "HostScopedModuleProxy":
        """Return a module proxy scoped to this host/group.

        Args:
            name: Module name or namespace component

        Returns:
            HostScopedModuleProxy for the module
        """
        if name.startswith("_"):
            raise AttributeError(name)

        return HostScopedModuleProxy(self._context, self._target, name)

    def __repr__(self) -> str:
        return f"HostScopedProxy({self._target!r})"


class HostScopedModuleProxy:
    """Proxy for a module scoped to a specific host/group.

    Supports both simple modules and FQCN:
        ftl.webservers.service(...)
        ftl.webservers.ansible.posix.firewalld(...)
    """

    def __init__(self, context: "AutomationContext", target: str, path: str):
        """Initialize the host-scoped module proxy.

        Args:
            context: The AutomationContext that handles execution
            target: Host name or group name to target
            path: Module name or namespace path
        """
        self._context = context
        self._target = target
        self._path = path

    def __getattr__(self, name: str) -> "HostScopedModuleProxy":
        """Extend the module path for FQCN support.

        Args:
            name: Next component of the namespace

        Returns:
            HostScopedModuleProxy with extended path
        """
        if name.startswith("_"):
            raise AttributeError(name)

        new_path = f"{self._path}.{name}"
        return HostScopedModuleProxy(self._context, self._target, new_path)

    async def __call__(self, **kwargs: Any) -> list:
        """Execute the module on the target host/group.

        Args:
            **kwargs: Module parameters

        Returns:
            list[ExecuteResult] for all targets (consistent return type)
        """
        # Special case: local/localhost executes directly without inventory
        if self._target in ("local", "localhost"):
            await self._context.execute(self._path, kwargs)
            # Get the result that was just appended to _results
            return [self._context._results[-1]]

        return await self._context.run_on(self._target, self._path, **kwargs)

    def __repr__(self) -> str:
        return f"HostScopedModuleProxy({self._target!r}, {self._path!r})"


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

    def __getattr__(self, name: str) -> Callable[..., Any] | NamespaceProxy | HostScopedProxy:
        """Return async wrapper for module, host proxy, or namespace proxy.

        Priority:
        1. local/localhost -> HostScopedProxy for localhost
        2. Host/group names -> HostScopedProxy for that target
        3. Known modules -> async wrapper
        4. Unknown names -> NamespaceProxy for FQCN

        Args:
            name: Module name, host/group name, or namespace

        Returns:
            Async function for known modules, HostScopedProxy for hosts/groups,
            NamespaceProxy for collection namespaces

        Raises:
            AttributeError: For private attributes or disabled modules
        """
        # Don't intercept private attributes
        if name.startswith("_"):
            raise AttributeError(name)

        # Check for local/localhost first
        if name in ("local", "localhost"):
            return HostScopedProxy(self._context, "localhost")

        # Check if it's a host or group name
        try:
            hosts_proxy = self._context.hosts
            if name in hosts_proxy.groups or name in hosts_proxy.keys():
                return HostScopedProxy(self._context, name)
        except Exception:
            # Inventory not loaded or other issue - continue to module check
            pass

        # Check if it's a known simple module
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
