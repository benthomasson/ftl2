"""FTL EC2 module.

Async EC2 instance management using aioboto3.
"""

from typing import Any

from ftl2.ftl_modules.exceptions import FTLModuleError, requires_extra

__all__ = ["ftl_ec2_instance", "ftl_ec2_instance_info"]

_RUNNING_STATES = ("running",)
_STOPPED_STATES = ("stopped",)
_TERMINATED_STATES = ("terminated", "shutting-down")
_ACTIVE_STATES = ("pending", "running", "stopping", "stopped")


def _extract_instance(instance: dict) -> dict[str, Any]:
    """Normalize a boto3 instance dict to a flat result."""
    tags_list = instance.get("Tags") or []
    tags = {t["Key"]: t["Value"] for t in tags_list}
    return {
        "instance_id": instance["InstanceId"],
        "instance_type": instance.get("InstanceType"),
        "state": instance["State"]["Name"],
        "public_ip": instance.get("PublicIpAddress"),
        "private_ip": instance.get("PrivateIpAddress"),
        "vpc_id": instance.get("VpcId"),
        "subnet_id": instance.get("SubnetId"),
        "image_id": instance.get("ImageId"),
        "key_name": instance.get("KeyName"),
        "launch_time": instance.get("LaunchTime", ""),
        "tags": tags,
        "security_groups": [
            {"id": sg["GroupId"], "name": sg["GroupName"]}
            for sg in instance.get("SecurityGroups", [])
        ],
    }


async def _find_instance(ec2, instance_id: str | None, name: str | None) -> dict | None:
    """Find an existing instance by ID or Name tag, skipping terminated."""
    if instance_id:
        try:
            resp = await ec2.describe_instances(InstanceIds=[instance_id])
        except ec2.exceptions.ClientError:
            return None
        for res in resp.get("Reservations", []):
            for inst in res.get("Instances", []):
                if inst["State"]["Name"] not in _TERMINATED_STATES:
                    return inst
        return None

    if name:
        resp = await ec2.describe_instances(Filters=[
            {"Name": "tag:Name", "Values": [name]},
            {"Name": "instance-state-name", "Values": list(_ACTIVE_STATES)},
        ])
        for res in resp.get("Reservations", []):
            for inst in res.get("Instances", []):
                return inst
    return None


async def _wait_for_state(ec2, instance_id: str, target: str, timeout: int) -> None:
    """Wait for an instance to reach a target state."""
    import asyncio

    waited = 0
    interval = 5
    while waited < timeout:
        resp = await ec2.describe_instances(InstanceIds=[instance_id])
        state = resp["Reservations"][0]["Instances"][0]["State"]["Name"]
        if state == target:
            return
        await asyncio.sleep(interval)
        waited += interval
    raise FTLModuleError(
        f"Timed out waiting for instance {instance_id} to reach '{target}' "
        f"after {timeout}s",
        instance_id=instance_id,
        target_state=target,
    )


@requires_extra("aws", "aioboto3")
async def ftl_ec2_instance(
    instance_id: str | None = None,
    name: str | None = None,
    state: str = "present",
    instance_type: str = "t3.micro",
    image_id: str | None = None,
    key_name: str | None = None,
    security_groups: list[str] | None = None,
    vpc_subnet_id: str | None = None,
    tags: dict[str, str] | None = None,
    user_data: str | None = None,
    wait: bool = True,
    wait_timeout: int = 600,
    region: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Manage EC2 instances.

    Args:
        instance_id: ID of existing instance
        name: Name tag for idempotent instance lookup/creation
        state: Desired state (present, running, started, stopped, terminated, absent)
        instance_type: EC2 instance type
        image_id: AMI ID for new instances
        key_name: SSH key pair name
        security_groups: List of security group IDs
        vpc_subnet_id: VPC subnet ID
        tags: Tags to apply (Name tag added automatically from name param)
        user_data: User data script
        wait: Wait for state transitions (default True)
        wait_timeout: Timeout in seconds (default 600)
        region: AWS region (defaults to AWS_DEFAULT_REGION or profile)
        **kwargs: Additional EC2 parameters

    Returns:
        Result dict with changed, instance_id, state, and instance details
    """
    import aioboto3
    from botocore.exceptions import BotoCoreError, ClientError

    state = state.lower()
    if state == "started":
        state = "running"
    if state == "absent":
        state = "terminated"
    if state == "present":
        state = "running"

    session = aioboto3.Session()
    try:
        async with session.client("ec2", region_name=region) as ec2:
            existing = await _find_instance(ec2, instance_id, name)

            if state in ("running",):
                if existing:
                    current = existing["State"]["Name"]
                    if current in _RUNNING_STATES:
                        return {
                            "changed": False,
                            "instance_id": existing["InstanceId"],
                            "state": current,
                            "instance": _extract_instance(existing),
                        }
                    if current in _STOPPED_STATES:
                        await ec2.start_instances(
                            InstanceIds=[existing["InstanceId"]],
                        )
                        if wait:
                            await _wait_for_state(
                                ec2, existing["InstanceId"], "running", wait_timeout,
                            )
                        resp = await ec2.describe_instances(
                            InstanceIds=[existing["InstanceId"]],
                        )
                        inst = resp["Reservations"][0]["Instances"][0]
                        return {
                            "changed": True,
                            "instance_id": inst["InstanceId"],
                            "state": inst["State"]["Name"],
                            "instance": _extract_instance(inst),
                        }

                if not image_id:
                    raise FTLModuleError(
                        "image_id is required to create a new instance",
                    )

                run_params: dict[str, Any] = {
                    "ImageId": image_id,
                    "InstanceType": instance_type,
                    "MinCount": 1,
                    "MaxCount": 1,
                }
                if key_name:
                    run_params["KeyName"] = key_name
                if security_groups:
                    run_params["SecurityGroupIds"] = security_groups
                if vpc_subnet_id:
                    run_params["SubnetId"] = vpc_subnet_id
                if user_data:
                    run_params["UserData"] = user_data

                all_tags = dict(tags or {})
                if name:
                    all_tags["Name"] = name
                if all_tags:
                    run_params["TagSpecifications"] = [{
                        "ResourceType": "instance",
                        "Tags": [
                            {"Key": k, "Value": v} for k, v in all_tags.items()
                        ],
                    }]

                resp = await ec2.run_instances(**run_params)
                inst = resp["Instances"][0]
                new_id = inst["InstanceId"]

                if wait:
                    await _wait_for_state(ec2, new_id, "running", wait_timeout)
                    resp = await ec2.describe_instances(InstanceIds=[new_id])
                    inst = resp["Reservations"][0]["Instances"][0]

                return {
                    "changed": True,
                    "instance_id": new_id,
                    "state": inst["State"]["Name"],
                    "instance": _extract_instance(inst),
                }

            elif state == "stopped":
                if not existing:
                    raise FTLModuleError(
                        "Cannot stop instance: no instance found",
                        instance_id=instance_id,
                        name=name,
                    )
                current = existing["State"]["Name"]
                if current in _STOPPED_STATES:
                    return {
                        "changed": False,
                        "instance_id": existing["InstanceId"],
                        "state": current,
                        "instance": _extract_instance(existing),
                    }
                await ec2.stop_instances(InstanceIds=[existing["InstanceId"]])
                if wait:
                    await _wait_for_state(
                        ec2, existing["InstanceId"], "stopped", wait_timeout,
                    )
                resp = await ec2.describe_instances(
                    InstanceIds=[existing["InstanceId"]],
                )
                inst = resp["Reservations"][0]["Instances"][0]
                return {
                    "changed": True,
                    "instance_id": inst["InstanceId"],
                    "state": inst["State"]["Name"],
                    "instance": _extract_instance(inst),
                }

            elif state == "terminated":
                if not existing:
                    return {
                        "changed": False,
                        "instance_id": instance_id,
                        "state": "terminated",
                        "instance": None,
                    }
                await ec2.terminate_instances(
                    InstanceIds=[existing["InstanceId"]],
                )
                if wait:
                    await _wait_for_state(
                        ec2, existing["InstanceId"], "terminated", wait_timeout,
                    )
                return {
                    "changed": True,
                    "instance_id": existing["InstanceId"],
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
    except ClientError as e:
        raise FTLModuleError(
            f"AWS API error: {e}",
            instance_id=instance_id,
            name=name,
        ) from e
    except BotoCoreError as e:
        raise FTLModuleError(
            f"AWS connection error: {e}",
            instance_id=instance_id,
            name=name,
        ) from e
    except Exception as e:
        raise FTLModuleError(
            f"EC2 operation failed: {e}",
            instance_id=instance_id,
            name=name,
        ) from e


@requires_extra("aws", "aioboto3")
async def ftl_ec2_instance_info(
    instance_ids: list[str] | None = None,
    filters: dict[str, str | list[str]] | None = None,
    region: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Gather information about EC2 instances.

    Args:
        instance_ids: List of instance IDs to query
        filters: EC2 API filters (e.g., {"instance-state-name": "running"})
        region: AWS region
        **kwargs: Additional parameters

    Returns:
        Result dict with instances list
    """
    import aioboto3
    from botocore.exceptions import BotoCoreError, ClientError

    session = aioboto3.Session()
    try:
        async with session.client("ec2", region_name=region) as ec2:
            params: dict[str, Any] = {}
            if instance_ids:
                params["InstanceIds"] = instance_ids
            if filters:
                params["Filters"] = [
                    {"Name": k, "Values": v if isinstance(v, list) else [v]}
                    for k, v in filters.items()
                ]

            instances = []
            paginator = ec2.get_paginator("describe_instances")
            async for page in paginator.paginate(**params):
                for res in page.get("Reservations", []):
                    for inst in res.get("Instances", []):
                        instances.append(_extract_instance(inst))

            return {
                "changed": False,
                "instances": instances,
            }

    except ClientError as e:
        raise FTLModuleError(
            f"AWS API error: {e}",
        ) from e
    except BotoCoreError as e:
        raise FTLModuleError(
            f"AWS connection error: {e}",
        ) from e
    except Exception as e:
        raise FTLModuleError(
            f"EC2 query failed: {e}",
        ) from e
