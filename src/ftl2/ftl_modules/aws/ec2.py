"""FTL EC2 module.

Async EC2 instance management using aioboto3.
"""

from typing import Any

from ftl2.ftl_modules.exceptions import FTLModuleError

__all__ = ["ftl_ec2_instance"]


async def ftl_ec2_instance(
    instance_id: str | None = None,
    state: str = "present",
    instance_type: str = "t3.micro",
    image_id: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Manage EC2 instances.

    Args:
        instance_id: ID of existing instance
        state: Desired state - present, absent, running, stopped
        instance_type: EC2 instance type
        image_id: AMI ID for new instances
        **kwargs: Additional EC2 parameters

    Returns:
        Result dict with instance_id and changed status
    """
    # Placeholder - will be implemented in Phase 4
    raise NotImplementedError("ftl_ec2_instance will be implemented in Phase 4")
