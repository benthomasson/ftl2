"""FTL AWS modules.

Async AWS modules using aioboto3 for non-blocking cloud operations.
"""

from ftl2.ftl_modules.aws.ec2 import ftl_ec2_instance, ftl_ec2_instance_info
from ftl2.ftl_modules.aws.route53 import ftl_route53_info, ftl_route53_record

__all__ = [
    "ftl_ec2_instance",
    "ftl_ec2_instance_info",
    "ftl_route53_record",
    "ftl_route53_info",
]
