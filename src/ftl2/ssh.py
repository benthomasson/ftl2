"""Async SSH Transport for FTL2.

Provides async SSH connections using asyncssh for remote host execution.
Implements the RemoteHost protocol for use with the module executor.

Features:
- Async SSH connections with asyncssh
- Connection pooling for host reuse
- Configurable authentication (keys, passwords, agents)
- SFTP support for file transfers
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import asyncssh

from ftl2.events import parse_event

logger = logging.getLogger(__name__)


@dataclass
class SSHConfig:
    """SSH connection configuration.

    Attributes:
        hostname: Remote hostname or IP
        port: SSH port (default 22)
        username: SSH username (default current user)
        password: Password for authentication (optional)
        client_keys: List of private key paths (optional)
        known_hosts: Path to known_hosts file (None to disable checking)
        connect_timeout: Connection timeout in seconds
        keepalive_interval: Keepalive interval (0 to disable)
    """

    hostname: str
    port: int = 22
    username: str | None = None
    password: str | None = None
    client_keys: list[str] | None = None
    known_hosts: str | None = ()  # Empty tuple = use default known_hosts
    connect_timeout: float = 30.0
    keepalive_interval: float = 30.0

    def to_asyncssh_options(self) -> dict[str, Any]:
        """Convert to asyncssh.connect() kwargs."""
        options: dict[str, Any] = {
            "host": self.hostname,
            "port": self.port,
            "connect_timeout": self.connect_timeout,
            "keepalive_interval": self.keepalive_interval,
        }

        if self.username:
            options["username"] = self.username
        if self.password:
            options["password"] = self.password
        if self.client_keys:
            options["client_keys"] = self.client_keys
        if self.known_hosts is None:
            options["known_hosts"] = None  # Disable host key checking
        elif self.known_hosts != ():
            options["known_hosts"] = self.known_hosts

        return options


class SSHHost:
    """Async SSH host implementing RemoteHost protocol.

    Provides async methods for remote command execution and file transfers
    using asyncssh. Connections are created on first use and cached.

    Example:
        host = SSHHost("server.example.com", username="deploy")
        async with host:
            stdout, stderr, rc = await host.run("uptime")
            print(stdout)
    """

    def __init__(
        self,
        hostname: str,
        port: int = 22,
        username: str | None = None,
        password: str | None = None,
        client_keys: list[str] | None = None,
        known_hosts: str | None = (),
        connect_timeout: float = 30.0,
    ):
        """Initialize SSH host.

        Args:
            hostname: Remote hostname or IP
            port: SSH port
            username: SSH username
            password: Password for auth
            client_keys: Private key paths
            known_hosts: Known hosts file (None to disable checking)
            connect_timeout: Connection timeout
        """
        self.config = SSHConfig(
            hostname=hostname,
            port=port,
            username=username,
            password=password,
            client_keys=client_keys,
            known_hosts=known_hosts,
            connect_timeout=connect_timeout,
        )
        self._conn: asyncssh.SSHClientConnection | None = None
        self._lock = asyncio.Lock()

    @property
    def name(self) -> str:
        """Host name for identification."""
        return self.config.hostname

    @property
    def is_local(self) -> bool:
        """Whether this is localhost (always False for SSH)."""
        return False

    async def connect(self) -> asyncssh.SSHClientConnection:
        """Establish SSH connection.

        Returns cached connection if available.
        """
        async with self._lock:
            if self._conn is None or self._conn.is_closed():
                logger.debug(f"Connecting to {self.config.hostname}:{self.config.port}")
                self._conn = await asyncssh.connect(
                    **self.config.to_asyncssh_options()
                )
                logger.info(f"Connected to {self.config.hostname}")
            return self._conn

    async def disconnect(self) -> None:
        """Close SSH connection."""
        async with self._lock:
            if self._conn is not None and not self._conn.is_closed():
                self._conn.close()
                await self._conn.wait_closed()
                logger.debug(f"Disconnected from {self.config.hostname}")
            self._conn = None

    async def __aenter__(self) -> "SSHHost":
        """Context manager entry - connect."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Context manager exit - disconnect."""
        await self.disconnect()

    async def run(
        self,
        command: str,
        stdin: str = "",
        timeout: int = 300,
    ) -> tuple[str, str, int]:
        """Run a command on the remote host.

        Args:
            command: Command to execute
            stdin: Input to send to command's stdin
            timeout: Command timeout in seconds

        Returns:
            Tuple of (stdout, stderr, return_code)
        """
        conn = await self.connect()

        logger.debug(f"Running on {self.config.hostname}: {command[:100]}")

        try:
            result = await asyncio.wait_for(
                conn.run(command, input=stdin, check=False),
                timeout=timeout,
            )

            stdout = result.stdout or ""
            stderr = result.stderr or ""
            return_code = result.returncode or 0

            logger.debug(
                f"Command completed: rc={return_code}, "
                f"stdout={len(stdout)} bytes, stderr={len(stderr)} bytes"
            )

            return stdout, stderr, return_code

        except asyncio.TimeoutError:
            logger.error(f"Command timed out after {timeout}s: {command[:50]}")
            return "", f"Command timed out after {timeout}s", -1

    async def run_streaming(
        self,
        command: str,
        stdin: str = "",
        timeout: int = 300,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> tuple[str, str, int, list[dict[str, Any]]]:
        """Run a command with real-time event streaming.

        Unlike run(), this method streams stderr line by line and invokes
        the event_callback for each event as it's emitted. This enables
        real-time progress reporting for remote module execution.

        Args:
            command: Command to execute
            stdin: Input to send to command's stdin
            timeout: Command timeout in seconds
            event_callback: Called for each event as it's emitted (optional)

        Returns:
            Tuple of (stdout, stderr, return_code, events)
            - stdout: Command stdout
            - stderr: Non-event stderr lines
            - return_code: Exit code
            - events: List of parsed event dicts

        Example:
            def on_progress(event):
                print(f"Progress: {event.get('percent', 0)}%")

            stdout, stderr, rc, events = await host.run_streaming(
                "python3 /tmp/bundle.pyz",
                stdin=json_params,
                event_callback=on_progress,
            )
        """
        conn = await self.connect()

        logger.debug(f"Running (streaming) on {self.config.hostname}: {command[:100]}")

        events: list[dict[str, Any]] = []
        other_stderr_lines: list[str] = []

        try:
            async with conn.create_process(command) as process:
                # Send stdin if provided
                if stdin:
                    process.stdin.write(stdin)
                    await process.stdin.drain()
                process.stdin.write_eof()

                async def read_stderr_streaming():
                    """Read stderr line by line, parsing events."""
                    async for line in process.stderr:
                        line = line.rstrip('\n\r')
                        event = parse_event(line)
                        if event is not None:
                            events.append(event)
                            if event_callback:
                                try:
                                    event_callback(event)
                                except Exception as e:
                                    logger.warning(f"Event callback error: {e}")
                        else:
                            other_stderr_lines.append(line)

                async def read_stdout():
                    """Read all stdout."""
                    return await process.stdout.read()

                # Run with timeout
                try:
                    stdout, _ = await asyncio.wait_for(
                        asyncio.gather(read_stdout(), read_stderr_streaming()),
                        timeout=timeout,
                    )
                except asyncio.TimeoutError:
                    process.kill()
                    logger.error(f"Command timed out after {timeout}s: {command[:50]}")
                    return "", f"Command timed out after {timeout}s", -1, events

                await process.wait()

                stderr = "\n".join(other_stderr_lines)
                return_code = process.returncode or 0

                logger.debug(
                    f"Streaming command completed: rc={return_code}, "
                    f"stdout={len(stdout)} bytes, events={len(events)}"
                )

                return stdout, stderr, return_code, events

        except Exception as e:
            logger.error(f"Streaming command failed: {e}")
            raise

    async def has_file(self, path: str) -> bool:
        """Check if a file exists on the remote host.

        Args:
            path: File path to check

        Returns:
            True if file exists
        """
        conn = await self.connect()

        try:
            async with conn.start_sftp_client() as sftp:
                try:
                    await sftp.stat(path)
                    return True
                except asyncssh.SFTPNoSuchFile:
                    return False
        except Exception as e:
            logger.warning(f"Error checking file {path}: {e}")
            # Fall back to shell check
            stdout, _, rc = await self.run(f"test -f {path}")
            return rc == 0

    async def write_file(self, path: str, content: bytes) -> None:
        """Write content to a file on the remote host.

        Args:
            path: Destination file path
            content: File content as bytes
        """
        conn = await self.connect()

        logger.debug(f"Writing {len(content)} bytes to {path}")

        async with conn.start_sftp_client() as sftp:
            async with sftp.open(path, "wb") as f:
                await f.write(content)

        # Make executable if it's a .pyz bundle
        if path.endswith(".pyz"):
            await self.run(f"chmod +x {path}")

        logger.debug(f"Wrote file: {path}")

    async def read_file(self, path: str) -> bytes:
        """Read a file from the remote host.

        Args:
            path: File path to read

        Returns:
            File content as bytes
        """
        conn = await self.connect()

        async with conn.start_sftp_client() as sftp:
            async with sftp.open(path, "rb") as f:
                return await f.read()


class SSHConnectionPool:
    """Pool of SSH connections for host reuse.

    Maintains a cache of SSHHost instances keyed by (hostname, port, username).
    Connections are reused when the same host is accessed multiple times.

    Example:
        pool = SSHConnectionPool()

        # These will reuse the same connection
        host1 = await pool.get("server.example.com", username="deploy")
        host2 = await pool.get("server.example.com", username="deploy")
        assert host1 is host2

        # Cleanup
        await pool.close_all()
    """

    def __init__(self):
        self._hosts: dict[tuple[str, int, str | None], SSHHost] = {}
        self._lock = asyncio.Lock()

    async def get(
        self,
        hostname: str,
        port: int = 22,
        username: str | None = None,
        password: str | None = None,
        client_keys: list[str] | None = None,
        known_hosts: str | None = (),
    ) -> SSHHost:
        """Get or create an SSHHost for the given parameters.

        Args:
            hostname: Remote hostname
            port: SSH port
            username: SSH username
            password: Password for auth
            client_keys: Private key paths
            known_hosts: Known hosts file

        Returns:
            SSHHost instance (may be reused)
        """
        key = (hostname, port, username)

        async with self._lock:
            if key not in self._hosts:
                self._hosts[key] = SSHHost(
                    hostname=hostname,
                    port=port,
                    username=username,
                    password=password,
                    client_keys=client_keys,
                    known_hosts=known_hosts,
                )
                logger.debug(f"Created new SSH host: {hostname}:{port}")

            return self._hosts[key]

    async def close_all(self) -> None:
        """Close all connections in the pool."""
        async with self._lock:
            for host in self._hosts.values():
                await host.disconnect()
            self._hosts.clear()
            logger.debug("Closed all pooled connections")

    async def __aenter__(self) -> "SSHConnectionPool":
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close_all()


# Convenience function for one-off commands
async def ssh_run(
    hostname: str,
    command: str,
    username: str | None = None,
    password: str | None = None,
    client_keys: list[str] | None = None,
    port: int = 22,
    timeout: int = 300,
) -> tuple[str, str, int]:
    """Run a single command on a remote host.

    Convenience function that handles connection lifecycle.

    Args:
        hostname: Remote hostname
        command: Command to run
        username: SSH username
        password: Password for auth
        client_keys: Private key paths
        port: SSH port
        timeout: Command timeout

    Returns:
        Tuple of (stdout, stderr, return_code)

    Example:
        stdout, stderr, rc = await ssh_run("server.example.com", "uptime")
    """
    host = SSHHost(
        hostname=hostname,
        port=port,
        username=username,
        password=password,
        client_keys=client_keys,
        known_hosts=None,  # Disable for one-off commands
    )

    async with host:
        return await host.run(command, timeout=timeout)


# Convenience function for running on multiple hosts
async def ssh_run_on_hosts(
    hostnames: list[str],
    command: str,
    username: str | None = None,
    password: str | None = None,
    port: int = 22,
    timeout: int = 300,
) -> list[tuple[str, str, str, int]]:
    """Run a command on multiple hosts concurrently.

    Args:
        hostnames: List of hostnames
        command: Command to run
        username: SSH username
        password: Password for auth
        port: SSH port
        timeout: Command timeout

    Returns:
        List of (hostname, stdout, stderr, return_code) tuples

    Example:
        results = await ssh_run_on_hosts(
            ["server1", "server2", "server3"],
            "uptime",
            username="deploy"
        )
        for hostname, stdout, stderr, rc in results:
            print(f"{hostname}: {stdout.strip()}")
    """
    async def run_one(hostname: str) -> tuple[str, str, str, int]:
        stdout, stderr, rc = await ssh_run(
            hostname=hostname,
            command=command,
            username=username,
            password=password,
            port=port,
            timeout=timeout,
        )
        return hostname, stdout, stderr, rc

    tasks = [run_one(h) for h in hostnames]
    return await asyncio.gather(*tasks)
