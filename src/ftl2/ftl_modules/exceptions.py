"""FTL Module exceptions and decorators."""

from __future__ import annotations

import functools
import importlib
from typing import Any, Callable


class FTLModuleError(Exception):
    """Raised when an FTL module fails.

    This exception automatically creates a result dict with failed=True.

    Attributes:
        msg: Human-readable error message
        result: Result dict with failed=True and any additional fields

    Example:
        raise FTLModuleError("File not found", path="/tmp/missing.txt")
        # Creates result: {"failed": True, "msg": "File not found", "path": "/tmp/missing.txt"}
    """

    def __init__(self, msg: str, **result_fields: Any) -> None:
        super().__init__(msg)
        self.msg = msg
        self.result: dict[str, Any] = {
            "failed": True,
            "msg": msg,
            **result_fields,
        }

    def __str__(self) -> str:
        return self.msg


class FTLModuleCheckModeError(FTLModuleError):
    """Raised when a module doesn't support check mode.

    Some modules cannot safely predict what changes would be made
    without actually making them.
    """

    def __init__(self, module_name: str) -> None:
        super().__init__(
            f"Module '{module_name}' does not support check mode",
            module=module_name,
            check_mode_supported=False,
        )


class FTLModuleNotFoundError(FTLModuleError):
    """Raised when a requested module is not found in the registry."""

    def __init__(self, module_name: str) -> None:
        super().__init__(
            f"Module '{module_name}' not found",
            module=module_name,
        )


class FTLModuleMissingDependencyError(FTLModuleError):
    """Raised when a module requires an optional dependency that isn't installed."""

    def __init__(self, module_name: str, extra: str, package: str) -> None:
        super().__init__(
            f"{module_name} requires the '{extra}' extra. "
            f"Install it with: pip install ftl2[{extra}]",
            module=module_name,
            extra=extra,
            package=package,
        )


def requires_extra(extra: str, package: str) -> Callable:
    """Decorator for native modules that require optional dependencies.

    Checks that the package is importable before calling the module function.
    If not, raises FTLModuleMissingDependencyError with an actionable message.

    Args:
        extra: The pip extra name (e.g., "aws")
        package: The Python package to check (e.g., "aioboto3")

    Example:
        @requires_extra("aws", "aioboto3")
        async def ftl_ec2_instance(**kwargs):
            import aioboto3
            ...
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                importlib.import_module(package)
            except ImportError:
                # Derive module name from function name (strip ftl_ prefix)
                module_name = func.__name__
                if module_name.startswith("ftl_"):
                    module_name = module_name[4:]
                raise FTLModuleMissingDependencyError(module_name, extra, package)
            return await func(*args, **kwargs)
        return wrapper
    return decorator
