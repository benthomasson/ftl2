"""Exception classes for FTL2 automation framework.

Provides structured exception types with rich context for debugging
and actionable error messages for AI-assisted development.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ErrorContext:
    """Rich context for error diagnosis.

    Attributes:
        host: Host name where error occurred
        host_address: Host IP/hostname and port
        user: SSH username (for remote errors)
        module: Module name being executed
        error_type: Classification of error (e.g., "ConnectionTimeout")
        message: Human-readable error message
        exit_code: Exit code if applicable
        attempt: Current attempt number
        max_attempts: Maximum retry attempts
        suggestions: List of actionable suggestions
        debug_command: Command to run for debugging
        related_errors: Other hosts with same error type
    """

    host: str = ""
    host_address: str = ""
    user: str = ""
    module: str = ""
    error_type: str = "Unknown"
    message: str = ""
    exit_code: int | None = None
    attempt: int = 0
    max_attempts: int = 0
    suggestions: list[str] = field(default_factory=list)
    debug_command: str = ""
    related_errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        result: dict[str, Any] = {
            "error_type": self.error_type,
            "message": self.message,
        }
        if self.host:
            result["host"] = self.host
        if self.host_address:
            result["host_address"] = self.host_address
        if self.user:
            result["user"] = self.user
        if self.module:
            result["module"] = self.module
        if self.exit_code is not None:
            result["exit_code"] = self.exit_code
        if self.attempt:
            result["attempt"] = self.attempt
            result["max_attempts"] = self.max_attempts
        if self.suggestions:
            result["suggestions"] = self.suggestions
        if self.debug_command:
            result["debug_command"] = self.debug_command
        if self.related_errors:
            result["related_hosts"] = self.related_errors
        return result

    def format_text(self) -> str:
        """Format as human-readable text."""
        lines = []

        # Header
        if self.host:
            lines.append(f"Error on host '{self.host}'")
        else:
            lines.append("Error")

        # Error type and message
        lines.append(f"  Type: {self.error_type}")
        lines.append(f"  Message: {self.message}")

        # Context section
        if self.host_address or self.user or self.module or self.exit_code is not None:
            lines.append("")
            lines.append("  Context:")
            if self.host_address:
                lines.append(f"    Host: {self.host_address}")
            if self.user:
                lines.append(f"    User: {self.user}")
            if self.module:
                lines.append(f"    Module: {self.module}")
            if self.exit_code is not None:
                lines.append(f"    Exit Code: {self.exit_code}")
            if self.attempt:
                lines.append(f"    Attempt: {self.attempt} of {self.max_attempts}")

        # Suggestions section
        if self.suggestions:
            lines.append("")
            lines.append("  Suggested Actions:")
            for i, suggestion in enumerate(self.suggestions, 1):
                lines.append(f"    {i}. {suggestion}")

        # Debug command
        if self.debug_command:
            lines.append("")
            lines.append(f"  Debug Command: {self.debug_command}")

        # Related hosts
        if self.related_errors:
            lines.append("")
            lines.append(f"  Related Hosts (same error): {', '.join(self.related_errors)}")

        return "\n".join(lines)


# Error type constants for classification
class ErrorTypes:
    """Standard error type classifications."""

    CONNECTION_TIMEOUT = "ConnectionTimeout"
    CONNECTION_REFUSED = "ConnectionRefused"
    AUTHENTICATION_FAILED = "AuthenticationFailed"
    HOST_UNREACHABLE = "HostUnreachable"
    MODULE_NOT_FOUND = "ModuleNotFound"
    MODULE_EXECUTION_ERROR = "ModuleExecutionError"
    MODULE_TIMEOUT = "ModuleTimeout"
    PERMISSION_DENIED = "PermissionDenied"
    INVENTORY_ERROR = "InventoryError"
    GATE_ERROR = "GateError"
    GATE_TIMEOUT = "GateTimeout"
    GATE_UNRESPONSIVE = "GateUnresponsive"
    UNKNOWN = "Unknown"


# Suggestion mappings for each error type
ERROR_SUGGESTIONS: dict[str, list[str]] = {
    ErrorTypes.CONNECTION_TIMEOUT: [
        "Verify host is reachable: ping {host_address}",
        "Check if SSH port is open: nc -zv {host} {port}",
        "Verify firewall allows connections on port {port}",
        "Check network connectivity between this machine and target",
    ],
    ErrorTypes.CONNECTION_REFUSED: [
        "Verify SSH daemon is running on target: systemctl status sshd",
        "Check SSH is listening on port {port}: ss -tlnp | grep {port}",
        "Verify correct port in inventory (current: {port})",
    ],
    ErrorTypes.AUTHENTICATION_FAILED: [
        "Verify SSH credentials are correct",
        "Check SSH key is in authorized_keys: ssh-copy-id {user}@{host}",
        "Verify SSH key file exists and has correct permissions (600)",
        "Try connecting manually: ssh -i {key_file} {user}@{host}",
    ],
    ErrorTypes.HOST_UNREACHABLE: [
        "Verify host is powered on and connected to network",
        "Check DNS resolution: nslookup {host}",
        "Verify IP address is correct in inventory",
    ],
    ErrorTypes.MODULE_NOT_FOUND: [
        "Check module name spelling",
        "Verify module exists in module directories",
        "Use -M to specify additional module directories",
        "List available modules with: ftl2 run --list-modules",
    ],
    ErrorTypes.MODULE_EXECUTION_ERROR: [
        "Check module arguments are correct",
        "Review module output for specific error details",
        "Run with --verbose for more details",
    ],
    ErrorTypes.PERMISSION_DENIED: [
        "Verify user has required permissions on target",
        "Check if sudo/become is needed for this operation",
        "Review file/directory permissions on target",
    ],
}


def get_suggestions(error_type: str, **context: Any) -> list[str]:
    """Get suggestions for an error type with context substitution.

    Args:
        error_type: The error type classification
        **context: Variables for substitution (host, port, user, etc.)

    Returns:
        List of actionable suggestions
    """
    templates = ERROR_SUGGESTIONS.get(error_type, [])
    suggestions = []
    for template in templates:
        try:
            suggestion = template.format(**context)
            suggestions.append(suggestion)
        except KeyError:
            # If substitution fails, use template as-is but clean up placeholders
            import re
            cleaned = re.sub(r'\{[^}]+\}', '<value>', template)
            suggestions.append(cleaned)
    return suggestions


class FTL2Error(Exception):
    """Base exception for all FTL2 errors.

    Attributes:
        context: Rich error context for diagnosis
    """

    def __init__(self, message: str, context: ErrorContext | None = None):
        super().__init__(message)
        self.context = context or ErrorContext(message=message)

    def with_context(self, **kwargs: Any) -> "FTL2Error":
        """Add context to this error."""
        for key, value in kwargs.items():
            if hasattr(self.context, key):
                setattr(self.context, key, value)
        return self


class ModuleNotFound(FTL2Error):
    """Raised when a module cannot be found in any module directory."""

    def __init__(self, message: str, module_name: str = "", search_paths: list[str] | None = None):
        context = ErrorContext(
            error_type=ErrorTypes.MODULE_NOT_FOUND,
            message=message,
            module=module_name,
            suggestions=get_suggestions(ErrorTypes.MODULE_NOT_FOUND),
        )
        super().__init__(message, context)
        self.module_name = module_name
        self.search_paths = search_paths or []


class ModuleExecutionError(FTL2Error):
    """Raised when module execution fails."""

    def __init__(
        self,
        message: str,
        host: str = "",
        module: str = "",
        exit_code: int | None = None,
        error_type: str = ErrorTypes.MODULE_EXECUTION_ERROR,
    ):
        context = ErrorContext(
            error_type=error_type,
            message=message,
            host=host,
            module=module,
            exit_code=exit_code,
            suggestions=get_suggestions(error_type),
        )
        super().__init__(message, context)


class FTL2ConnectionError(FTL2Error):
    """Raised when SSH connection fails.

    Named FTL2ConnectionError to avoid shadowing Python's builtin
    ConnectionError (an OSError subclass).
    """

    def __init__(
        self,
        message: str,
        host: str = "",
        host_address: str = "",
        port: int = 22,
        user: str = "",
        error_type: str = ErrorTypes.CONNECTION_TIMEOUT,
        attempt: int = 0,
        max_attempts: int = 3,
    ):
        suggestions = get_suggestions(
            error_type,
            host=host,
            host_address=host_address,
            port=port,
            user=user,
        )
        context = ErrorContext(
            error_type=error_type,
            message=message,
            host=host,
            host_address=f"{host_address}:{port}",
            user=user,
            attempt=attempt,
            max_attempts=max_attempts,
            suggestions=suggestions,
            debug_command=f"ftl2 test-ssh -i <inventory> --timeout 30",
        )
        super().__init__(message, context)


# Deprecated alias — will be removed in a future release.
# Avoid importing this name: it shadows Python's builtin ConnectionError.
ConnectionError = FTL2ConnectionError


class AuthenticationError(FTL2Error):
    """Raised when SSH authentication fails."""

    def __init__(
        self,
        message: str,
        host: str = "",
        host_address: str = "",
        port: int = 22,
        user: str = "",
        key_file: str = "",
    ):
        suggestions = get_suggestions(
            ErrorTypes.AUTHENTICATION_FAILED,
            host=host_address,
            port=port,
            user=user,
            key_file=key_file or "~/.ssh/id_rsa",
        )
        context = ErrorContext(
            error_type=ErrorTypes.AUTHENTICATION_FAILED,
            message=message,
            host=host,
            host_address=f"{host_address}:{port}",
            user=user,
            suggestions=suggestions,
        )
        super().__init__(message, context)


class GateError(FTL2Error):
    """Raised when gate operations fail."""

    def __init__(self, message: str, host: str = ""):
        context = ErrorContext(
            error_type=ErrorTypes.GATE_ERROR,
            message=message,
            host=host,
        )
        super().__init__(message, context)


class InventoryError(FTL2Error):
    """Raised when inventory operations fail."""

    def __init__(self, message: str):
        context = ErrorContext(
            error_type=ErrorTypes.INVENTORY_ERROR,
            message=message,
        )
        super().__init__(message, context)


class GateRequestTimeoutError(ModuleExecutionError):
    """Raised when a gate request exceeds the per-request timeout.

    Extends ModuleExecutionError so callers catching the parent type
    still handle it. Indicates an individual request hung — the gate
    may still be healthy for other requests.
    """

    def __init__(self, message: str, host: str = "", module: str = ""):
        super().__init__(
            message, host=host, module=module,
            error_type=ErrorTypes.GATE_TIMEOUT,
        )


class GateHandshakeTimeoutError(FTL2ConnectionError):
    """Raised when the initial Hello handshake with a gate times out.

    Indicates the gate process failed to start or respond.
    """

    def __init__(self, message: str, host: str = ""):
        super().__init__(
            message, host=host,
            error_type=ErrorTypes.GATE_TIMEOUT,
        )


class GateUnresponsiveError(FTL2ConnectionError):
    """Raised when a gate fails periodic keepalive health checks.

    Indicates the gate process is wedged (deadlock, hang, resource
    exhaustion). All pending futures on the connection are failed
    with this error.
    """

    def __init__(self, message: str, host: str = ""):
        super().__init__(
            message, host=host,
            error_type=ErrorTypes.GATE_UNRESPONSIVE,
        )


class ExcludedModuleError(FTL2Error):
    """Raised when user calls an Ansible module that doesn't apply to FTL2.

    Some Ansible modules exist only as interfaces to Ansible's internal
    execution model (connection plugins, playbook flow control, fact system).
    These modules don't apply to FTL2's direct execution model.
    """

    def __init__(self, module: "ExcludedModule"):
        from ftl2.module_loading.excluded import ExcludedModule

        self.module = module
        message = self._format_message()
        super().__init__(message)

    def _format_message(self) -> str:
        lines = [
            "",
            "=" * 60,
            f"Module '{self.module.name}' is not available in FTL2",
            "=" * 60,
            "",
            f"Reason: {self.module.reason}",
            "",
            f"Alternative: {self.module.alternative}",
        ]
        if self.module.example:
            lines.append(f"\nExample:{self.module.example}")
        return "\n".join(lines)
