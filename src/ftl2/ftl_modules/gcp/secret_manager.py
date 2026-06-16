"""FTL Secret Manager module.

Async Secret Manager secret management using the Google Cloud SDK.
"""

import hashlib
from typing import Any

from ftl2.ftl_modules.exceptions import FTLModuleError, requires_extra

__all__ = ["ftl_secret_manager_secret"]


def _extract_secret(secret: Any) -> dict[str, Any]:
    """Normalize a Secret Manager Secret proto to a flat result dict."""
    return {
        "name": secret.name,
        "create_time": secret.create_time.isoformat() if secret.create_time else None,
    }


@requires_extra("gcp", "google.cloud.secretmanager_v1")
async def ftl_secret_manager_secret(
    *,
    name: str,
    project: str,
    secret_data: str | None = None,
    state: str = "present",
    check_mode: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    """Create, update, or delete a Secret Manager secret.

    Uses Application Default Credentials (ADC) for authentication.
    When secret_data is provided, a new version is added if the data differs
    from the latest version (compared by SHA256 hash).
    """
    from google.api_core.exceptions import NotFound
    from google.cloud.secretmanager_v1 import SecretManagerServiceAsyncClient
    from google.cloud.secretmanager_v1 import types

    client = SecretManagerServiceAsyncClient()
    parent = f"projects/{project}"
    full_name = f"{parent}/secrets/{name}"

    existing = None
    try:
        existing = await client.get_secret(name=full_name)
    except NotFound:
        pass

    if state == "absent":
        if existing is None:
            return {"changed": False, "state": "absent"}
        if check_mode:
            return {"changed": True, "state": "absent"}
        await client.delete_secret(name=full_name)
        return {"changed": True, "state": "absent"}

    changed = False

    if existing is None:
        if check_mode:
            return {"changed": True, "secret": {"name": name}}
        existing = await client.create_secret(
            parent=parent,
            secret_id=name,
            secret=types.Secret(
                replication=types.Replication(
                    automatic=types.Replication.Automatic(),
                ),
            ),
        )
        changed = True

    result = _extract_secret(existing)

    if secret_data is not None:
        data_bytes = secret_data.encode("utf-8")
        desired_hash = hashlib.sha256(data_bytes).hexdigest()

        needs_version = True
        if not changed:
            try:
                latest = await client.access_secret_version(
                    name=f"{full_name}/versions/latest",
                )
                current_hash = hashlib.sha256(latest.payload.data).hexdigest()
                if current_hash == desired_hash:
                    needs_version = False
            except NotFound:
                pass

        if needs_version:
            if check_mode:
                return {"changed": True, "secret": result}
            await client.add_secret_version(
                parent=full_name,
                payload=types.SecretPayload(data=data_bytes),
            )
            changed = True

    result["changed"] = changed
    return {"changed": changed, "secret": result}
