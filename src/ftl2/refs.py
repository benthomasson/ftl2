"""Variable reference and dereferencing system for FTL2.

This module provides a variable reference mechanism for dynamic access
to nested configuration values using dot notation, similar to Ansible's
variable lookup system.

Key Features:
- Dynamic attribute reference creation with dot notation
- Lazy evaluation of variable paths
- Type-safe dereferencing
- Automatic caching of reference objects
"""

from typing import Any


def deref(host_data: dict[str, Any], ref_or_value: Any) -> Any:
    """Dereference a Ref object or return a regular value unchanged.

    This is the main entry point for resolving variable references. If the
    input is a Ref object, it resolves it against the host data. Otherwise,
    it returns the value unchanged.

    Args:
        host_data: Dictionary containing host variables and configuration
        ref_or_value: Either a Ref object or any other value

    Returns:
        Resolved value from host data if Ref, otherwise the value unchanged

    Raises:
        KeyError: If reference path doesn't exist in host data
        TypeError: If intermediate values are not dict-like

    Example:
        >>> host = {"network": {"ip": "192.168.1.100"}}
        >>> ref = Ref(None, "network").ip
        >>> deref(host, ref)
        '192.168.1.100'
        >>> deref(host, "literal")
        'literal'
    """
    if isinstance(ref_or_value, Ref):
        path = get_ref_path(ref_or_value)
        return get_nested_value(host_data, path)
    else:
        return ref_or_value


def get_ref_path(ref: "Ref") -> list[str]:
    """Extract the complete variable path from a Ref object.

    Traverses the reference chain to build the complete path of
    nested attribute names.

    Args:
        ref: A Ref object representing a variable reference

    Returns:
        List of strings representing the path from root to leaf

    Example:
        >>> root = Ref(None, "config")
        >>> nested = root.database.host
        >>> get_ref_path(nested)
        ['config', 'database', 'host']
    """
    path: list[str] = []
    current: Ref | None = ref

    # Walk backwards from leaf to root
    while current is not None and current._parent is not None:
        path.append(current._name)
        current = current._parent

    # Add root name if exists
    if current is not None:
        path.append(current._name)

    # Reverse to get root-to-leaf order
    return path[::-1]


def get_nested_value(data: dict[str, Any], path: list[str]) -> Any:
    """Retrieve a value from nested dictionaries using a path.

    Navigates through nested dictionaries using a sequence of keys
    to retrieve the final value.

    Args:
        data: Dictionary containing nested data
        path: List of keys ordered from outermost to innermost

    Returns:
        Value found at the end of the path

    Raises:
        KeyError: If any key in path doesn't exist
        TypeError: If intermediate value is not dict-like

    Example:
        >>> data = {"app": {"db": {"host": "localhost"}}}
        >>> get_nested_value(data, ["app", "db", "host"])
        'localhost'
    """
    value: Any = data
    for key in path:
        value = value[key]
    return value


class Ref:
    """Dynamic variable reference builder for nested data access.

    Enables building variable references using Python attribute syntax
    that are resolved later against actual data.

    Attributes:
        _parent: Parent Ref object in the chain (None for root)
        _name: Name of this reference level

    Example:
        >>> config = Ref(None, "config")
        >>> db_host = config.database.host
        >>> web_port = config.web.port
        >>>
        >>> # Later resolve against data
        >>> host_data = {
        ...     "config": {
        ...         "database": {"host": "db.example.com"},
        ...         "web": {"port": 8080}
        ...     }
        ... }
        >>> deref(host_data, db_host)
        'db.example.com'
    """

    def __init__(self, parent: "Ref | None", name: str) -> None:
        """Initialize a variable reference.

        Args:
            parent: Parent Ref in the chain (None for root)
            name: Name for this reference level

        Example:
            >>> root = Ref(None, "config")
            >>> child = Ref(root, "database")
        """
        self._parent = parent
        self._name = name

    def __getattr__(self, name: str) -> "Ref":
        """Create and cache a child reference for attribute access.

        Args:
            name: Attribute name being accessed

        Returns:
            New Ref object representing the nested attribute

        Example:
            >>> config = Ref(None, "config")
            >>> db = config.database  # Creates Ref(config, "database")
            >>> host = db.host  # Creates Ref(db, "host")
        """
        ref = Ref(self, name)
        # Cache the reference for future access
        object.__setattr__(self, name, ref)
        return ref

    def __repr__(self) -> str:
        """Return string representation of the reference."""
        path = get_ref_path(self)
        return f"Ref({'.'.join(path)})"
