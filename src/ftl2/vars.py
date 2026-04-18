"""Variable validation and inspection for FTL2.

Provides functionality to inspect, validate, and trace the source of
variables in host configurations. Designed for AI-assisted development
where variable correctness is critical before execution.
"""

from dataclasses import dataclass, field
from typing import Any

from .inventory import Inventory
from .types import HostConfig


@dataclass
class VariableInfo:
    """Information about a variable including its source.

    Attributes:
        name: Variable name
        value: Variable value
        source: Where the variable came from (host, group, inventory)
        source_name: Specific source name (e.g., group name)
    """

    name: str
    value: Any
    source: str  # "host", "group", "builtin"
    source_name: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "name": self.name,
            "value": self.value,
            "source": self.source,
            "source_name": self.source_name,
        }


@dataclass
class HostVariables:
    """All variables for a host with source tracking.

    Attributes:
        host_name: Name of the host
        variables: List of VariableInfo objects
        groups: Groups the host belongs to
    """

    host_name: str
    variables: list[VariableInfo] = field(default_factory=list)
    groups: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "host_name": self.host_name,
            "groups": self.groups,
            "variables": [v.to_dict() for v in self.variables],
            "variable_count": len(self.variables),
        }

    def get_var(self, name: str) -> VariableInfo | None:
        """Get a variable by name."""
        for var in self.variables:
            if var.name == name:
                return var
        return None

    def format_text(self) -> str:
        """Format as human-readable text."""
        lines = [
            f"Variables for {self.host_name}:",
            "",
        ]

        if self.groups:
            lines.append(f"Groups: {', '.join(self.groups)}")
            lines.append("")

        if not self.variables:
            lines.append("  (no variables)")
        else:
            # Group variables by source
            by_source: dict[str, list[VariableInfo]] = {}
            for var in self.variables:
                source_key = f"{var.source}:{var.source_name}" if var.source_name else var.source
                if source_key not in by_source:
                    by_source[source_key] = []
                by_source[source_key].append(var)

            # Find max name length for alignment
            max_name = max(len(v.name) for v in self.variables) if self.variables else 0

            for source_key, vars_list in by_source.items():
                source_display = source_key.replace(":", " from ")
                lines.append(f"From {source_display}:")
                for var in vars_list:
                    padding = " " * (max_name - len(var.name) + 2)
                    value_str = _format_value(var.value)
                    lines.append(f"  {var.name}{padding}{value_str}")
                lines.append("")

        return "\n".join(lines)


@dataclass
class ValidationResult:
    """Result of variable validation.

    Attributes:
        valid: Whether validation passed
        errors: List of validation errors
        warnings: List of validation warnings
        missing_vars: Variables that are required but missing
        unused_vars: Variables defined but not used
    """

    valid: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    missing_vars: list[str] = field(default_factory=list)
    unused_vars: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "valid": self.valid,
            "errors": self.errors,
            "warnings": self.warnings,
            "missing_vars": self.missing_vars,
            "unused_vars": self.unused_vars,
        }

    def format_text(self) -> str:
        """Format as human-readable text."""
        lines = []

        if self.valid:
            lines.append("Validation: PASSED")
        else:
            lines.append("Validation: FAILED")

        if self.errors:
            lines.append("")
            lines.append("Errors:")
            for error in self.errors:
                lines.append(f"  - {error}")

        if self.warnings:
            lines.append("")
            lines.append("Warnings:")
            for warning in self.warnings:
                lines.append(f"  - {warning}")

        if self.missing_vars:
            lines.append("")
            lines.append("Missing required variables:")
            for var in self.missing_vars:
                lines.append(f"  - {var}")

        if self.unused_vars:
            lines.append("")
            lines.append("Unused variables:")
            for var in self.unused_vars:
                lines.append(f"  - {var}")

        return "\n".join(lines)


def _format_value(value: Any) -> str:
    """Format a value for display."""
    if isinstance(value, str):
        # Truncate long strings
        if len(value) > 50:
            return f'"{value[:47]}..."'
        return f'"{value}"'
    elif isinstance(value, bool):
        return str(value).lower()
    elif isinstance(value, (list, dict)):
        import json
        s = json.dumps(value)
        if len(s) > 50:
            return f"{s[:47]}..."
        return s
    else:
        return str(value)


def get_host_groups(inventory: Inventory, host_name: str) -> list[str]:
    """Get all groups that a host belongs to.

    Args:
        inventory: The inventory to search
        host_name: Name of the host

    Returns:
        List of group names containing this host
    """
    groups = []
    for group in inventory.list_groups():
        if host_name in group.hosts:
            groups.append(group.name)
    return groups


def collect_host_variables(
    inventory: Inventory,
    host: HostConfig,
) -> HostVariables:
    """Collect all variables for a host with source tracking.

    Variables are collected in order of precedence (lowest to highest):
    1. Group variables (earlier groups take precedence)
    2. Host variables

    Args:
        inventory: The inventory containing group information
        host: The host configuration

    Returns:
        HostVariables with all variables and their sources
    """
    result = HostVariables(host_name=host.name)

    # Track which variables we've seen (for deduplication)
    seen: dict[str, VariableInfo] = {}

    # Get groups this host belongs to
    groups = get_host_groups(inventory, host.name)
    result.groups = groups

    # Add builtin variables (connection settings)
    builtins = [
        ("ansible_host", host.ansible_host, "builtin", "connection"),
        ("ansible_port", host.ansible_port, "builtin", "connection"),
        ("ansible_user", host.ansible_user, "builtin", "connection"),
        ("ansible_connection", host.ansible_connection, "builtin", "connection"),
        ("ansible_python_interpreter", host.ansible_python_interpreter, "builtin", "connection"),
    ]

    for name, value, source, source_name in builtins:
        var_info = VariableInfo(name=name, value=value, source=source, source_name=source_name)
        seen[name] = var_info

    # Collect group variables (in reverse order so earlier groups override later)
    for group_name in reversed(groups):
        group = inventory.get_group(group_name)
        if group and group.vars:
            for name, value in group.vars.items():
                var_info = VariableInfo(
                    name=name,
                    value=value,
                    source="group",
                    source_name=group_name,
                )
                seen[name] = var_info

    # Collect host variables (highest precedence)
    for name, value in host.vars.items():
        var_info = VariableInfo(
            name=name,
            value=value,
            source="host",
            source_name=host.name,
        )
        seen[name] = var_info

    # Convert to list
    result.variables = list(seen.values())

    return result


def validate_variables(
    host_vars: HostVariables,
    required_vars: list[str] | None = None,
) -> ValidationResult:
    """Validate variables for a host.

    Args:
        host_vars: Host variables to validate
        required_vars: List of required variable names

    Returns:
        ValidationResult with any errors or warnings
    """
    result = ValidationResult()

    # Check for required variables
    if required_vars:
        defined_names = {v.name for v in host_vars.variables}
        for var_name in required_vars:
            if var_name not in defined_names:
                result.missing_vars.append(var_name)
                result.errors.append(f"Required variable '{var_name}' is not defined")
                result.valid = False

    # Check for empty values that might be problematic
    for var in host_vars.variables:
        if var.value == "" and var.source == "host":
            result.warnings.append(f"Variable '{var.name}' has empty value")

    return result


def get_all_host_variables(inventory: Inventory) -> dict[str, HostVariables]:
    """Collect variables for all hosts in an inventory.

    Args:
        inventory: The inventory to process

    Returns:
        Dictionary mapping host names to HostVariables
    """
    result: dict[str, HostVariables] = {}

    for host_name, host in inventory.get_all_hosts().items():
        result[host_name] = collect_host_variables(inventory, host)

    return result


def format_all_hosts_text(all_vars: dict[str, HostVariables]) -> str:
    """Format all host variables as text summary.

    Args:
        all_vars: Dictionary of host variables

    Returns:
        Formatted text summary
    """
    if not all_vars:
        return "No hosts found."

    lines = ["", "Host Variables Summary:", ""]

    # Find max name length for alignment
    max_name = max(len(name) for name in all_vars)

    for host_name, host_vars in sorted(all_vars.items()):
        padding = " " * (max_name - len(host_name) + 2)
        var_count = len(host_vars.variables)
        groups_str = f" (groups: {', '.join(host_vars.groups)})" if host_vars.groups else ""
        lines.append(f"  {host_name}{padding}{var_count} variable(s){groups_str}")

    lines.append("")
    lines.append(f"Total: {len(all_vars)} host(s)")
    lines.append("")
    lines.append("Use 'ftl2 vars show <hostname>' for detailed variable information.")
    lines.append("")

    return "\n".join(lines)


def format_all_hosts_json(all_vars: dict[str, HostVariables]) -> list[dict[str, Any]]:
    """Format all host variables as JSON-serializable list.

    Args:
        all_vars: Dictionary of host variables

    Returns:
        List of dictionaries for JSON serialization
    """
    return [
        {
            "host_name": host_name,
            "groups": host_vars.groups,
            "variable_count": len(host_vars.variables),
        }
        for host_name, host_vars in sorted(all_vars.items())
    ]
