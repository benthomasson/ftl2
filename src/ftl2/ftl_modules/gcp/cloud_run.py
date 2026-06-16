"""FTL Cloud Run module.

Async Cloud Run service management using the Google Cloud Run SDK.
"""

from typing import Any

from ftl2.ftl_modules.exceptions import FTLModuleError, requires_extra

__all__ = ["ftl_cloud_run_service"]


def _extract_service(service: Any) -> dict[str, Any]:
    """Normalize a Cloud Run Service proto to a flat result dict."""
    containers = service.template.containers
    container = containers[0] if containers else None

    env_vars = {}
    if container:
        for env in container.env:
            if env.value:
                env_vars[env.name] = env.value

    result: dict[str, Any] = {
        "name": service.name,
        "uri": service.uri,
        "state": "present",
        "image": container.image if container else None,
        "port": None,
        "env_vars": env_vars,
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
    """Check if the current service state differs from desired."""
    checks = [
        ("image", desired.get("image")),
        ("port", desired.get("port")),
        ("min_instances", desired.get("min_instances")),
        ("max_instances", desired.get("max_instances")),
        ("memory", desired.get("memory")),
        ("cpu", desired.get("cpu")),
    ]
    for key, val in checks:
        if val is not None and current.get(key) != val:
            return True

    desired_env = desired.get("env_vars")
    if desired_env is not None and current.get("env_vars") != desired_env:
        return True

    desired_sa = desired.get("service_account")
    if desired_sa is not None and current.get("service_account") != desired_sa:
        return True

    return False


def _build_service(
    *,
    image: str,
    port: int,
    env_vars: dict[str, str] | None,
    secrets: dict[str, str] | None,
    min_instances: int,
    max_instances: int,
    memory: str,
    cpu: str,
    service_account: str | None,
) -> Any:
    """Build a Cloud Run Service proto from parameters."""
    from google.cloud.run_v2 import types

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
    port: int = 8080,
    min_instances: int = 0,
    max_instances: int = 100,
    memory: str = "512Mi",
    cpu: str = "1",
    service_account: str | None = None,
    state: str = "present",
    check_mode: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    """Create, update, or delete a Cloud Run service.

    Uses Application Default Credentials (ADC) for authentication.
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

    if image is None:
        raise FTLModuleError("'image' is required when state=present")

    build_kwargs = dict(
        image=image, port=port, env_vars=env_vars, secrets=secrets,
        min_instances=min_instances, max_instances=max_instances,
        memory=memory, cpu=cpu, service_account=service_account,
    )

    if existing is None:
        if check_mode:
            return {"changed": True, "service": {"name": name, "state": "present"}}
        service_obj = _build_service(**build_kwargs)
        operation = await client.create_service(
            parent=parent, service=service_obj, service_id=name,
        )
        result = await operation.result()
        return {"changed": True, "service": _extract_service(result)}

    current = _extract_service(existing)
    if not _needs_update(current, **build_kwargs):
        current["changed"] = False
        return {"changed": False, "service": current}

    if check_mode:
        return {"changed": True, "service": current}

    service_obj = _build_service(**build_kwargs)
    service_obj.name = full_name
    operation = await client.update_service(service=service_obj)
    result = await operation.result()
    return {"changed": True, "service": _extract_service(result)}
