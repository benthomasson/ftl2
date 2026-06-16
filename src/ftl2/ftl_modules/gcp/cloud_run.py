"""FTL Cloud Run module.

Async Cloud Run service management using the Google Cloud Run SDK.
"""

from typing import Any

from ftl2.ftl_modules.exceptions import FTLModuleError, requires_extra

__all__ = ["ftl_cloud_run_service"]

_SENTINEL = object()


def _extract_service(service: Any) -> dict[str, Any]:
    """Normalize a Cloud Run Service proto to a flat result dict."""
    containers = service.template.containers
    container = containers[0] if containers else None

    env_vars = {}
    secrets = {}
    if container:
        for env in container.env:
            if env.value_source and env.value_source.secret_key_ref:
                ref = env.value_source.secret_key_ref
                secrets[env.name] = f"{ref.secret}:{ref.version}"
            elif env.value:
                env_vars[env.name] = env.value

    result: dict[str, Any] = {
        "name": service.name,
        "uri": service.uri,
        "state": "present",
        "image": container.image if container else None,
        "port": None,
        "env_vars": env_vars,
        "secrets": secrets,
        "min_instances": None,
        "max_instances": None,
        "memory": None,
        "cpu": None,
        "service_account": service.template.service_account,
    }

    if container and container.ports:
        result["port"] = container.ports[0].container_port

    scaling = service.template.scaling
    if scaling:
        result["min_instances"] = scaling.min_instance_count
        result["max_instances"] = scaling.max_instance_count

    if container and container.resources and container.resources.limits:
        result["memory"] = container.resources.limits.get("memory")
        result["cpu"] = container.resources.limits.get("cpu")

    return result


def _needs_update(current: dict[str, Any], **desired: Any) -> bool:
    """Check if the current service state differs from desired.

    Only compares fields that were explicitly provided (not None/sentinel).
    """
    checks = ["image", "port", "min_instances", "max_instances", "memory", "cpu"]
    for key in checks:
        val = desired.get(key, _SENTINEL)
        if val is not _SENTINEL and val is not None and current.get(key) != val:
            return True

    for key in ("env_vars", "secrets"):
        val = desired.get(key, _SENTINEL)
        if val is not _SENTINEL and val is not None and current.get(key) != val:
            return True

    desired_sa = desired.get("service_account", _SENTINEL)
    if desired_sa is not _SENTINEL and desired_sa is not None and current.get("service_account") != desired_sa:
        return True

    return False


def _build_service(
    existing: Any | None,
    *,
    image: str,
    port: int | None,
    env_vars: dict[str, str] | None,
    secrets: dict[str, str] | None,
    min_instances: int | None,
    max_instances: int | None,
    memory: str | None,
    cpu: str | None,
    service_account: str | None,
) -> Any:
    """Build a Cloud Run Service proto, merging with existing state if updating."""
    from google.cloud.run_v2 import types

    if existing:
        current = _extract_service(existing)
        if port is None:
            port = current.get("port", 8080)
        if env_vars is None:
            env_vars = current.get("env_vars")
        if secrets is None:
            secrets = current.get("secrets")
        if min_instances is None:
            min_instances = current.get("min_instances", 0)
        if max_instances is None:
            max_instances = current.get("max_instances", 100)
        if memory is None:
            memory = current.get("memory", "512Mi")
        if cpu is None:
            cpu = current.get("cpu", "1")
        if service_account is None:
            service_account = current.get("service_account")

    port = port or 8080
    min_instances = min_instances if min_instances is not None else 0
    max_instances = max_instances if max_instances is not None else 100
    memory = memory or "512Mi"
    cpu = cpu or "1"

    env = []
    if env_vars:
        for k, v in env_vars.items():
            env.append(types.EnvVar(name=k, value=v))
    if secrets:
        for k, secret_ref in secrets.items():
            parts = secret_ref.split(":")
            secret_name = parts[0]
            version = parts[1] if len(parts) > 1 else "latest"
            env.append(types.EnvVar(
                name=k,
                value_source=types.EnvVarSource(
                    secret_key_ref=types.SecretKeySelector(
                        secret=secret_name,
                        version=version,
                    )
                ),
            ))

    container = types.Container(
        image=image,
        ports=[types.ContainerPort(container_port=port)],
        env=env,
        resources=types.ResourceRequirements(
            limits={"memory": memory, "cpu": cpu},
        ),
    )

    scaling = types.RevisionScaling(
        min_instance_count=min_instances,
        max_instance_count=max_instances,
    )

    template = types.RevisionTemplate(
        containers=[container],
        scaling=scaling,
    )
    if service_account:
        template.service_account = service_account

    return types.Service(template=template)


@requires_extra("gcp", "google.cloud.run_v2")
async def ftl_cloud_run_service(
    *,
    name: str,
    project: str,
    location: str,
    image: str | None = None,
    env_vars: dict[str, str] | None = None,
    secrets: dict[str, str] | None = None,
    port: int | None = None,
    min_instances: int | None = None,
    max_instances: int | None = None,
    memory: str | None = None,
    cpu: str | None = None,
    service_account: str | None = None,
    state: str = "present",
    check_mode: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    """Create, update, or delete a Cloud Run service.

    Uses Application Default Credentials (ADC) for authentication.
    Unspecified parameters preserve existing values on update.
    On create, defaults are: port=8080, min_instances=0, max_instances=100,
    memory=512Mi, cpu=1.
    """
    from google.api_core.exceptions import NotFound
    from google.cloud.run_v2 import ServicesAsyncClient

    client = ServicesAsyncClient()
    parent = f"projects/{project}/locations/{location}"
    full_name = f"{parent}/services/{name}"

    existing = None
    try:
        existing = await client.get_service(name=full_name)
    except NotFound:
        pass

    if state == "absent":
        if existing is None:
            return {"changed": False, "state": "absent"}
        if check_mode:
            return {"changed": True, "state": "absent"}
        operation = await client.delete_service(name=full_name)
        await operation.result()
        return {"changed": True, "state": "absent"}

    if image is None and existing is None:
        raise FTLModuleError("'image' is required when creating a new service")

    if existing is not None and image is None:
        image = _extract_service(existing)["image"]

    desired = dict(
        image=image, port=port, env_vars=env_vars, secrets=secrets,
        min_instances=min_instances, max_instances=max_instances,
        memory=memory, cpu=cpu, service_account=service_account,
    )

    if existing is None:
        if check_mode:
            return {"changed": True, "service": {"name": name, "state": "present"}}
        service_obj = _build_service(None, **desired)
        operation = await client.create_service(
            parent=parent, service=service_obj, service_id=name,
        )
        result = await operation.result()
        return {"changed": True, "service": _extract_service(result)}

    current = _extract_service(existing)
    if not _needs_update(current, **desired):
        return {"changed": False, "service": current}

    if check_mode:
        return {"changed": True, "service": current}

    service_obj = _build_service(existing, **desired)
    service_obj.name = full_name
    operation = await client.update_service(service=service_obj)
    result = await operation.result()
    return {"changed": True, "service": _extract_service(result)}
