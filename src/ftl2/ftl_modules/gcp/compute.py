"""FTL GCP Compute Engine module.

Async GCE instance management using google-cloud-compute.
All SDK calls are synchronous and wrapped with asyncio.to_thread().
"""

import asyncio
from typing import Any

from ftl2.ftl_modules.exceptions import FTLModuleError, requires_extra

__all__ = ["ftl_gcp_compute_instance", "ftl_gcp_compute_instance_info"]

_RUNNING_STATES = ("RUNNING",)
_STOPPED_STATES = ("TERMINATED", "STOPPED")


def _extract_instance(instance: Any) -> dict[str, Any]:
    """Normalize a GCP Instance protobuf to a flat result dict."""
    external_ip = None
    internal_ip = None
    network = None
    subnet = None

    if instance.network_interfaces:
        ni = instance.network_interfaces[0]
        internal_ip = ni.network_i_p if ni.network_i_p else None
        network = ni.network if ni.network else None
        subnet = ni.subnetwork if ni.subnetwork else None
        if ni.access_configs:
            for ac in ni.access_configs:
                if ac.nat_i_p:
                    external_ip = ac.nat_i_p
                    break

    tags = list(instance.tags.items) if instance.tags and instance.tags.items else []

    metadata = {}
    if instance.metadata and instance.metadata.items:
        for item in instance.metadata.items:
            metadata[item.key] = item.value

    zone_name = instance.zone.rsplit("/", 1)[-1] if instance.zone else ""
    machine_type_short = instance.machine_type.rsplit("/", 1)[-1] if instance.machine_type else ""

    return {
        "instance_id": str(instance.id) if instance.id else "",
        "name": instance.name,
        "machine_type": machine_type_short,
        "status": instance.status,
        "external_ip": external_ip,
        "internal_ip": internal_ip,
        "zone": zone_name,
        "network": network,
        "subnet": subnet,
        "self_link": instance.self_link,
        "tags": tags,
        "metadata": metadata,
        "creation_timestamp": instance.creation_timestamp or "",
    }


async def _find_instance(client: Any, project: str, zone: str, name: str) -> Any | None:
    """Find an existing instance by name, return None if not found."""
    from google.api_core.exceptions import NotFound

    try:
        return await asyncio.to_thread(client.get, project=project, zone=zone, instance=name)
    except NotFound:
        return None


async def _wait_for_operation(
    project: str, zone: str, operation: str, timeout: int,
) -> None:
    """Wait for a zone operation to complete."""
    from google.cloud import compute_v1

    ops_client = compute_v1.ZoneOperationsClient()
    waited = 0
    interval = 5
    while waited < timeout:
        result = await asyncio.to_thread(
            ops_client.get, project=project, zone=zone, operation=operation,
        )
        if result.status == compute_v1.Operation.Status.DONE:
            if result.error and result.error.errors:
                msgs = [e.message for e in result.error.errors]
                raise FTLModuleError(
                    f"Operation {operation} failed: {'; '.join(msgs)}",
                    operation=operation,
                )
            return
        await asyncio.sleep(interval)
        waited += interval

    raise FTLModuleError(
        f"Timed out waiting for operation {operation} after {timeout}s",
        operation=operation,
    )


@requires_extra("gcp", "google.cloud.compute_v1")
async def ftl_gcp_compute_instance(
    name: str,
    project: str,
    zone: str = "us-central1-a",
    state: str = "present",
    machine_type: str = "e2-micro",
    source_image: str | None = None,
    disk_size_gb: int = 20,
    network: str = "global/networks/default",
    subnet: str | None = None,
    tags: list[str] | None = None,
    metadata: dict[str, str] | None = None,
    service_account_email: str | None = None,
    service_account_scopes: list[str] | None = None,
    labels: dict[str, str] | None = None,
    wait: bool = True,
    wait_timeout: int = 600,
    **kwargs: Any,
) -> dict[str, Any]:
    """Manage GCP Compute Engine instances.

    Args:
        name: Instance name (used for idempotent lookup)
        project: GCP project ID
        zone: GCE zone (e.g., us-central1-a)
        state: Desired state (present, running, started, stopped, terminated, absent)
        machine_type: Machine type (e.g., e2-micro, n1-standard-1)
        source_image: Boot disk source image (e.g., projects/fedora-cloud/global/images/family/fedora-cloud-42)
        disk_size_gb: Boot disk size in GB (default 20)
        network: VPC network (default: global/networks/default)
        subnet: Subnet (optional)
        tags: Network tags
        metadata: Instance metadata key-value pairs
        service_account_email: Service account email
        service_account_scopes: Service account scopes
        labels: Labels to apply
        wait: Wait for state transitions (default True)
        wait_timeout: Timeout in seconds (default 600)
        **kwargs: Additional parameters

    Returns:
        Result dict with changed, instance_id, state, and instance details
    """
    from google.api_core.exceptions import GoogleAPICallError
    from google.cloud import compute_v1

    state = state.lower()
    if state == "started":
        state = "running"
    if state == "absent":
        state = "terminated"
    if state == "present":
        state = "running"

    client = compute_v1.InstancesClient()

    try:
        existing = await _find_instance(client, project, zone, name)

        if state == "running":
            if existing:
                current = existing.status
                if current in _RUNNING_STATES:
                    return {
                        "changed": False,
                        "instance_id": str(existing.id),
                        "state": current,
                        "instance": _extract_instance(existing),
                    }
                if current in ("STAGING", "PROVISIONING"):
                    if wait:
                        waited = 0
                        while waited < wait_timeout:
                            inst = await asyncio.to_thread(
                                client.get, project=project, zone=zone, instance=name,
                            )
                            if inst.status in _RUNNING_STATES:
                                return {
                                    "changed": False,
                                    "instance_id": str(inst.id),
                                    "state": inst.status,
                                    "instance": _extract_instance(inst),
                                }
                            await asyncio.sleep(5)
                            waited += 5
                        raise FTLModuleError(
                            f"Timed out waiting for {name} to reach RUNNING after {wait_timeout}s",
                            instance=name,
                        )
                    return {
                        "changed": False,
                        "instance_id": str(existing.id),
                        "state": current,
                        "instance": _extract_instance(existing),
                    }
                if current in ("STOPPING", "SUSPENDING", "SUSPENDED"):
                    waited = 0
                    while waited < wait_timeout:
                        inst = await asyncio.to_thread(
                            client.get, project=project, zone=zone, instance=name,
                        )
                        if inst.status in _STOPPED_STATES:
                            current = inst.status
                            break
                        await asyncio.sleep(5)
                        waited += 5
                    else:
                        raise FTLModuleError(
                            f"Timed out waiting for {name} to stop after {wait_timeout}s",
                            instance=name,
                            current_state=current,
                        )
                if current in _STOPPED_STATES:
                    op = await asyncio.to_thread(
                        client.start, project=project, zone=zone, instance=name,
                    )
                    if wait:
                        await _wait_for_operation(project, zone, op.name, wait_timeout)
                    inst = await asyncio.to_thread(
                        client.get, project=project, zone=zone, instance=name,
                    )
                    return {
                        "changed": True,
                        "instance_id": str(inst.id),
                        "state": inst.status,
                        "instance": _extract_instance(inst),
                    }
                raise FTLModuleError(
                    f"Instance {name} is in unexpected state: {current}",
                    instance=name,
                    current_state=current,
                )

            if not source_image:
                raise FTLModuleError(
                    "source_image is required to create a new instance",
                )

            machine_type_url = f"zones/{zone}/machineTypes/{machine_type}"

            boot_disk = compute_v1.AttachedDisk(
                auto_delete=True,
                boot=True,
                initialize_params=compute_v1.AttachedDiskInitializeParams(
                    source_image=source_image,
                    disk_size_gb=disk_size_gb,
                ),
            )

            access_config = compute_v1.AccessConfig(
                name="External NAT",
                type_="ONE_TO_ONE_NAT",
            )
            network_interface = compute_v1.NetworkInterface(
                network=network,
                access_configs=[access_config],
            )
            if subnet:
                network_interface.subnetwork = subnet

            instance_resource = compute_v1.Instance(
                name=name,
                machine_type=machine_type_url,
                disks=[boot_disk],
                network_interfaces=[network_interface],
            )

            if tags:
                instance_resource.tags = compute_v1.Tags(items=tags)

            if metadata:
                instance_resource.metadata = compute_v1.Metadata(
                    items=[
                        compute_v1.Items(key=k, value=v)
                        for k, v in metadata.items()
                    ],
                )

            if labels:
                instance_resource.labels = labels

            if service_account_email:
                sa = compute_v1.ServiceAccount(email=service_account_email)
                if service_account_scopes:
                    sa.scopes = service_account_scopes
                instance_resource.service_accounts = [sa]

            op = await asyncio.to_thread(
                client.insert, project=project, zone=zone,
                instance_resource=instance_resource,
            )

            if wait:
                await _wait_for_operation(project, zone, op.name, wait_timeout)

            inst = await asyncio.to_thread(
                client.get, project=project, zone=zone, instance=name,
            )
            return {
                "changed": True,
                "instance_id": str(inst.id),
                "state": inst.status,
                "instance": _extract_instance(inst),
            }

        elif state == "stopped":
            if not existing:
                raise FTLModuleError(
                    "Cannot stop instance: no instance found",
                    instance=name,
                )
            current = existing.status
            if current in _STOPPED_STATES:
                return {
                    "changed": False,
                    "instance_id": str(existing.id),
                    "state": current,
                    "instance": _extract_instance(existing),
                }
            op = await asyncio.to_thread(
                client.stop, project=project, zone=zone, instance=name,
            )
            if wait:
                await _wait_for_operation(project, zone, op.name, wait_timeout)
            inst = await asyncio.to_thread(
                client.get, project=project, zone=zone, instance=name,
            )
            return {
                "changed": True,
                "instance_id": str(inst.id),
                "state": inst.status,
                "instance": _extract_instance(inst),
            }

        elif state == "terminated":
            if not existing:
                return {
                    "changed": False,
                    "instance_id": None,
                    "state": "terminated",
                    "instance": None,
                }
            op = await asyncio.to_thread(
                client.delete, project=project, zone=zone, instance=name,
            )
            if wait:
                await _wait_for_operation(project, zone, op.name, wait_timeout)
            return {
                "changed": True,
                "instance_id": str(existing.id),
                "state": "terminated",
                "instance": _extract_instance(existing),
            }

        else:
            raise FTLModuleError(
                f"Unsupported state: {state}",
                supported_states=["present", "running", "started",
                                  "stopped", "terminated", "absent"],
            )

    except FTLModuleError:
        raise
    except GoogleAPICallError as e:
        raise FTLModuleError(
            f"GCP API error: {e}",
            instance=name,
        ) from e
    except Exception as e:
        raise FTLModuleError(
            f"GCE operation failed: {e}",
            instance=name,
        ) from e


@requires_extra("gcp", "google.cloud.compute_v1")
async def ftl_gcp_compute_instance_info(
    project: str,
    zone: str = "us-central1-a",
    instance: str | None = None,
    filter_expr: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Gather information about GCP Compute Engine instances.

    Args:
        project: GCP project ID
        zone: GCE zone
        instance: Specific instance name to query
        filter_expr: GCP API filter expression (e.g., "status=RUNNING")
        **kwargs: Additional parameters

    Returns:
        Result dict with instances list
    """
    from google.api_core.exceptions import GoogleAPICallError, NotFound
    from google.cloud import compute_v1

    client = compute_v1.InstancesClient()

    try:
        if instance:
            try:
                inst = await asyncio.to_thread(
                    client.get, project=project, zone=zone, instance=instance,
                )
                return {
                    "changed": False,
                    "instances": [_extract_instance(inst)],
                }
            except NotFound:
                return {
                    "changed": False,
                    "instances": [],
                }

        request = compute_v1.ListInstancesRequest(project=project, zone=zone)
        if filter_expr:
            request.filter = filter_expr

        raw_instances = await asyncio.to_thread(
            lambda: list(client.list(request=request)),
        )
        instances = [_extract_instance(inst) for inst in raw_instances]

        return {
            "changed": False,
            "instances": instances,
        }

    except GoogleAPICallError as e:
        raise FTLModuleError(
            f"GCP API error: {e}",
        ) from e
    except Exception as e:
        raise FTLModuleError(
            f"GCE query failed: {e}",
        ) from e
