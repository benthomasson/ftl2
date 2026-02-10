"""FTL wait_for module - Wait for a condition before continuing.

Polls a TCP port until it becomes reachable (or unreachable), replacing
crude shell sleeps with targeted readiness checks. Commonly used to wait
for a server to finish booting before configuring it.

Example:
    result = ftl_wait_for(host="192.168.1.10", port=22, timeout=180)
    # Waits up to 180s for SSH to become available
"""

import socket
import time
from typing import Any

from ftl2.ftl_modules.exceptions import FTLModuleError

__all__ = ["ftl_wait_for"]


def _can_connect(host: str, port: int, timeout: int) -> bool:
    """Try to open a TCP connection to host:port."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        result = sock.connect_ex((host, port))
        return result == 0
    except (OSError, socket.error):
        return False
    finally:
        sock.close()


def ftl_wait_for(
    host: str = "127.0.0.1",
    port: int | None = None,
    timeout: int = 300,
    delay: int = 0,
    sleep: int = 1,
    state: str = "started",
    connect_timeout: int = 5,
) -> dict[str, Any]:
    """Wait for a TCP port to become reachable or unreachable.

    Polls host:port at regular intervals until the desired state is
    reached or the timeout expires.

    Args:
        host: Target hostname or IP address.
        port: TCP port to check. Required.
        timeout: Maximum seconds to wait before failing. Default 300.
        delay: Seconds to wait before the first check. Default 0.
        sleep: Seconds between checks. Default 1.
        state: Desired state - "started" (port open) or "stopped" (port closed).
        connect_timeout: Timeout in seconds for each connection attempt. Default 5.

    Returns:
        Result dict with:
        - changed: Always False (waiting doesn't modify anything)
        - elapsed: Seconds spent waiting
        - host: Target host
        - port: Target port
        - state: Confirmed state

    Raises:
        FTLModuleError: If port is not specified, state is invalid,
            or timeout is exceeded.
    """
    if port is None:
        raise FTLModuleError("Missing required argument: port")

    if state not in ("started", "stopped"):
        raise FTLModuleError(
            f"Invalid state: {state!r}. Must be 'started' or 'stopped'.",
            state=state,
        )

    start = time.monotonic()

    if delay > 0:
        time.sleep(delay)

    while True:
        elapsed = time.monotonic() - start
        reachable = _can_connect(host, port, connect_timeout)

        if state == "started" and reachable:
            return {
                "changed": False,
                "elapsed": round(elapsed, 1),
                "host": host,
                "port": port,
                "state": "started",
            }

        if state == "stopped" and not reachable:
            return {
                "changed": False,
                "elapsed": round(elapsed, 1),
                "host": host,
                "port": port,
                "state": "stopped",
            }

        if elapsed >= timeout:
            raise FTLModuleError(
                f"Timeout waiting for {host}:{port} to be {state} "
                f"after {round(elapsed, 1)}s",
                elapsed=round(elapsed, 1),
                host=host,
                port=port,
                state=state,
            )

        time.sleep(sleep)
