"""Become-control kwargs extraction.

Lightweight module with no heavy dependencies, so it can be
imported directly in tests without stubbing the rest of the package.
"""

from typing import Any

# Become-control kwargs that are NOT module parameters
_BECOME_KWARGS = frozenset({"become", "become_user", "become_method"})


def _extract_become_overrides(kwargs: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Separate become-control kwargs from module parameters.

    Returns:
        Tuple of (become_overrides, module_params)
    """
    become_overrides: dict[str, Any] = {}
    module_params: dict[str, Any] = {}
    for k, v in kwargs.items():
        if k in _BECOME_KWARGS:
            become_overrides[k] = v
        else:
            module_params[k] = v
    return become_overrides, module_params
