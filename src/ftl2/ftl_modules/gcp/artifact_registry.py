"""FTL Artifact Registry module.

Async Artifact Registry repository management using the Google Cloud SDK.
"""

from typing import Any

from ftl2.ftl_modules.exceptions import FTLModuleError, requires_extra

__all__ = ["ftl_artifact_registry_repository"]


def _extract_repository(repo: Any) -> dict[str, Any]:
    """Normalize an Artifact Registry Repository proto to a flat result dict."""
    return {
        "name": repo.name,
        "format": repo.format_.name if repo.format_ else None,
        "description": repo.description,
        "create_time": repo.create_time.isoformat() if repo.create_time else None,
        "update_time": repo.update_time.isoformat() if repo.update_time else None,
    }


@requires_extra("gcp", "google.cloud.artifactregistry_v1")
async def ftl_artifact_registry_repository(
    *,
    name: str,
    project: str,
    location: str,
    format: str = "DOCKER",
    description: str = "",
    state: str = "present",
    check_mode: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    """Create or delete an Artifact Registry repository.

    Uses Application Default Credentials (ADC) for authentication.
    """
    from google.api_core.exceptions import NotFound
    from google.cloud.artifactregistry_v1 import (
        ArtifactRegistryAsyncClient,
        Repository,
    )

    client = ArtifactRegistryAsyncClient()
    parent = f"projects/{project}/locations/{location}"
    full_name = f"{parent}/repositories/{name}"

    existing = None
    try:
        existing = await client.get_repository(name=full_name)
    except NotFound:
        pass

    if state == "absent":
        if existing is None:
            return {"changed": False, "state": "absent"}
        if check_mode:
            return {"changed": True, "state": "absent"}
        operation = await client.delete_repository(name=full_name)
        await operation.result()
        return {"changed": True, "state": "absent"}

    if existing is not None:
        result = _extract_repository(existing)
        return {"changed": False, "repository": result}

    if check_mode:
        return {"changed": True, "repository": {"name": name, "format": format}}

    format_enum = getattr(Repository.Format, format.upper(), Repository.Format.DOCKER)
    repo = Repository(
        format_=format_enum,
        description=description,
    )
    operation = await client.create_repository(
        parent=parent, repository=repo, repository_id=name,
    )
    result = await operation.result()
    return {"changed": True, "repository": _extract_repository(result)}
