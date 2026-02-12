"""HashiCorp Vault KV secret backend for FTL2.

Reads secrets from Vault's KV v2 engine using standard VAULT_ADDR
and VAULT_TOKEN environment variables. Secrets are referenced as
"path#field" strings and resolved at automation context startup.

Requires the `hvac` package: pip install ftl2[vault]
"""

import os
from typing import Any


class VaultError(Exception):
    """Raised when Vault operations fail."""


def create_vault_client() -> Any:
    """Create an authenticated Vault client from environment variables.

    Uses VAULT_ADDR and VAULT_TOKEN (standard Vault convention).

    Returns:
        Authenticated hvac.Client

    Raises:
        VaultError: If env vars missing or authentication fails
    """
    try:
        import hvac
    except ImportError:
        raise VaultError(
            "hvac package is required for Vault support. "
            "Install with: pip install ftl2[vault]"
        ) from None

    addr = os.environ.get("VAULT_ADDR")
    token = os.environ.get("VAULT_TOKEN")
    if not addr:
        raise VaultError("VAULT_ADDR environment variable is not set")
    if not token:
        raise VaultError("VAULT_TOKEN environment variable is not set")

    client = hvac.Client(url=addr, token=token)
    if not client.is_authenticated():
        raise VaultError(f"Vault authentication failed for {addr}")

    return client


def read_vault_secrets(secret_refs: dict[str, str]) -> dict[str, str]:
    """Read secrets from Vault KV v2 engine.

    Args:
        secret_refs: Mapping of {name: "path#field"} references.
            Example: {"DB_PW": "myapp#db_password"}
            The path is relative to the KV mount (default "secret").

    Returns:
        Dict of {name: value} with resolved secret values.

    Raises:
        VaultError: If ref format is invalid, path not found, or field missing
    """
    client = create_vault_client()

    # Group by path to minimize API calls
    paths: dict[str, list[tuple[str, str]]] = {}
    for name, ref in secret_refs.items():
        if "#" not in ref:
            raise VaultError(
                f"Invalid vault ref '{ref}' for '{name}': expected 'path#field'"
            )
        path, field = ref.rsplit("#", 1)
        paths.setdefault(path, []).append((name, field))

    results: dict[str, str] = {}
    for path, fields in paths.items():
        try:
            response = client.secrets.kv.v2.read_secret_version(
                path=path, raise_on_deleted_version=True
            )
        except Exception as e:
            field_names = ", ".join(name for name, _ in fields)
            raise VaultError(
                f"Failed to read Vault path '{path}' (needed for {field_names}): {e}"
            ) from e

        data = response["data"]["data"]
        for name, field in fields:
            if field not in data:
                available = ", ".join(sorted(data.keys()))
                raise VaultError(
                    f"Field '{field}' not found at Vault path '{path}' "
                    f"(available: {available})"
                )
            results[name] = data[field]

    return results
