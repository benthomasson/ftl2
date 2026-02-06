"""FTL HTTP operation modules.

These modules handle HTTP requests asynchronously using httpx.
They provide the same functionality as ansible.builtin.uri and
ansible.builtin.get_url but run in-process with async/await.
"""

import hashlib
from pathlib import Path
from typing import Any

import httpx

from ftl2.ftl_modules.exceptions import FTLModuleError
from ftl2.events import emit_progress

__all__ = ["ftl_uri", "ftl_get_url"]


async def ftl_uri(
    url: str,
    method: str = "GET",
    body: str | bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 30,
    return_content: bool = True,
    status_code: int | list[int] | None = None,
) -> dict[str, Any]:
    """Make an async HTTP request.

    Args:
        url: URL to request
        method: HTTP method (GET, POST, PUT, DELETE, PATCH, etc.)
        body: Request body (string or bytes)
        headers: Request headers dict
        timeout: Request timeout in seconds
        return_content: Whether to include response content in result
        status_code: Expected status code(s), raises error if not matched

    Returns:
        Result dict with:
        - changed: True if method is not GET/HEAD
        - status: HTTP status code
        - url: Final URL (after redirects)
        - content: Response body (if return_content=True)
        - json: Parsed JSON (if response is JSON)
        - headers: Response headers

    Raises:
        FTLModuleError: If request fails or status code doesn't match
    """
    method = method.upper()
    headers = headers or {}

    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.request(
                method=method,
                url=url,
                content=body,
                headers=headers,
            )

        # Check status code if specified
        if status_code is not None:
            expected = [status_code] if isinstance(status_code, int) else status_code
            if response.status_code not in expected:
                raise FTLModuleError(
                    f"Status code {response.status_code} not in expected {expected}",
                    url=url,
                    status=response.status_code,
                    expected_status=expected,
                )

        result: dict[str, Any] = {
            "changed": method not in ("GET", "HEAD", "OPTIONS"),
            "status": response.status_code,
            "url": str(response.url),
            "headers": dict(response.headers),
        }

        if return_content:
            result["content"] = response.text

            # Try to parse JSON if content-type indicates it
            content_type = response.headers.get("content-type", "")
            if "application/json" in content_type:
                try:
                    result["json"] = response.json()
                except Exception:
                    result["json"] = None
            else:
                result["json"] = None

        return result

    except httpx.TimeoutException:
        raise FTLModuleError(
            f"Request timed out after {timeout}s",
            url=url,
            timeout=timeout,
        )
    except httpx.ConnectError as e:
        raise FTLModuleError(
            f"Connection failed: {e}",
            url=url,
        )
    except httpx.HTTPError as e:
        raise FTLModuleError(
            f"HTTP error: {e}",
            url=url,
        )
    except FTLModuleError:
        raise
    except Exception as e:
        raise FTLModuleError(
            f"Request failed: {e}",
            url=url,
        )


async def ftl_get_url(
    url: str,
    dest: str,
    checksum: str | None = None,
    force: bool = True,
    timeout: int = 300,
    headers: dict[str, str] | None = None,
    emit_events: bool = True,
) -> dict[str, Any]:
    """Download a file asynchronously with progress events.

    Uses streaming download with progress reporting for large files.

    Args:
        url: URL to download
        dest: Destination file path
        checksum: Optional SHA256 checksum to verify (format: "sha256:hexdigest" or just "hexdigest")
        force: Overwrite if destination exists (default True)
        timeout: Download timeout in seconds
        headers: Optional request headers
        emit_events: Whether to emit progress events (default True)

    Returns:
        Result dict with:
        - changed: True if file was downloaded
        - dest: Destination path
        - url: Source URL
        - checksum: SHA256 of downloaded file

    Raises:
        FTLModuleError: If download fails or checksum doesn't match

    Events:
        progress: Emitted during download with percent, current, total bytes
    """
    dest_path = Path(dest)
    headers = headers or {}

    try:
        # Check if we need to download
        if dest_path.exists() and not force:
            # Verify checksum if provided
            if checksum:
                actual = _calculate_checksum(dest_path)
                expected = _normalize_checksum(checksum)
                if actual == expected:
                    return {
                        "changed": False,
                        "dest": str(dest_path),
                        "url": url,
                        "checksum": f"sha256:{actual}",
                        "msg": "File exists with correct checksum",
                    }

            return {
                "changed": False,
                "dest": str(dest_path),
                "url": url,
                "msg": "File exists and force=False",
            }

        # Ensure parent directory exists
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        # Streaming download with progress
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            async with client.stream("GET", url, headers=headers) as response:
                response.raise_for_status()

                # Get content length for progress reporting
                total_size = int(response.headers.get("content-length", 0))
                downloaded = 0
                hasher = hashlib.sha256()

                # Extract filename for progress message
                filename = dest_path.name

                if emit_events and total_size > 0:
                    emit_progress(
                        percent=0,
                        message=f"Downloading {filename}",
                        current=0,
                        total=total_size,
                    )

                # Stream to file
                with open(dest_path, "wb") as f:
                    async for chunk in response.aiter_bytes(chunk_size=65536):
                        f.write(chunk)
                        hasher.update(chunk)
                        downloaded += len(chunk)

                        if emit_events and total_size > 0:
                            percent = int(downloaded * 100 / total_size)
                            emit_progress(
                                percent=percent,
                                message=f"Downloading {filename}",
                                current=downloaded,
                                total=total_size,
                            )

        # Verify checksum if provided
        actual_checksum = hasher.hexdigest()
        if checksum:
            expected = _normalize_checksum(checksum)
            if actual_checksum != expected:
                # Remove bad file
                dest_path.unlink(missing_ok=True)
                raise FTLModuleError(
                    f"Checksum mismatch: expected {expected}, got {actual_checksum}",
                    url=url,
                    dest=dest,
                    expected_checksum=expected,
                    actual_checksum=actual_checksum,
                )

        return {
            "changed": True,
            "dest": str(dest_path),
            "url": url,
            "checksum": f"sha256:{actual_checksum}",
            "size": downloaded,
        }

    except httpx.TimeoutException:
        raise FTLModuleError(
            f"Download timed out after {timeout}s",
            url=url,
            dest=dest,
        )
    except httpx.HTTPStatusError as e:
        raise FTLModuleError(
            f"Download failed with status {e.response.status_code}",
            url=url,
            dest=dest,
            status=e.response.status_code,
        )
    except httpx.HTTPError as e:
        raise FTLModuleError(
            f"Download failed: {e}",
            url=url,
            dest=dest,
        )
    except FTLModuleError:
        raise
    except Exception as e:
        raise FTLModuleError(
            f"Download failed: {e}",
            url=url,
            dest=dest,
        )


def _calculate_checksum(path: Path) -> str:
    """Calculate SHA256 checksum of a file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _normalize_checksum(checksum: str) -> str:
    """Normalize checksum string, stripping 'sha256:' prefix if present."""
    if checksum.startswith("sha256:"):
        return checksum[7:]
    return checksum
