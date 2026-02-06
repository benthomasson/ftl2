"""FTL HTTP operation modules.

These modules handle HTTP requests asynchronously using httpx.
They provide the same functionality as ansible.builtin.uri and
ansible.builtin.get_url but run in-process with async/await.
"""

from typing import Any

from ftl2.ftl_modules.exceptions import FTLModuleError

__all__ = ["ftl_uri", "ftl_get_url"]


async def ftl_uri(
    url: str,
    method: str = "GET",
    body: str | bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    """Make an async HTTP request.

    Args:
        url: URL to request
        method: HTTP method (GET, POST, PUT, DELETE, etc.)
        body: Request body
        headers: Request headers
        timeout: Request timeout in seconds

    Returns:
        Result dict with status, content, headers
    """
    # Placeholder - will be implemented in Phase 2
    raise NotImplementedError("ftl_uri will be implemented in Phase 2")


async def ftl_get_url(
    url: str,
    dest: str,
    checksum: str | None = None,
) -> dict[str, Any]:
    """Download a file asynchronously.

    Args:
        url: URL to download
        dest: Destination file path
        checksum: Optional SHA256 checksum to verify

    Returns:
        Result dict with changed status
    """
    # Placeholder - will be implemented in Phase 2
    raise NotImplementedError("ftl_get_url will be implemented in Phase 2")
