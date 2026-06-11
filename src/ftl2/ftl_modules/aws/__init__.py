"""FTL AWS modules.

Async AWS modules using aioboto3 for non-blocking cloud operations.
"""

from ftl2.ftl_modules.aws.ec2 import ftl_ec2_instance, ftl_ec2_instance_info

__all__ = ["ftl_ec2_instance", "ftl_ec2_instance_info"]
