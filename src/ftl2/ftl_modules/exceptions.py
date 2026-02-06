"""FTL Module exceptions."""

from typing import Any


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
