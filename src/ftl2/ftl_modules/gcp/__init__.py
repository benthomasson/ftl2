"""FTL GCP modules.

Async Google Cloud modules using the Google Cloud Python SDK.
"""

from ftl2.ftl_modules.gcp.artifact_registry import ftl_artifact_registry_repository
from ftl2.ftl_modules.gcp.cloud_run import ftl_cloud_run_service
from ftl2.ftl_modules.gcp.compute import (
    ftl_gcp_compute_instance,
    ftl_gcp_compute_instance_info,
)
from ftl2.ftl_modules.gcp.secret_manager import ftl_secret_manager_secret

__all__ = [
    "ftl_cloud_run_service",
    "ftl_artifact_registry_repository",
    "ftl_secret_manager_secret",
    "ftl_gcp_compute_instance",
    "ftl_gcp_compute_instance_info",
]
