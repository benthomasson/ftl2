"""Module runner interfaces and implementations for FTL2.

This module defines the strategy pattern for module execution, providing
pluggable runners for local and remote execution with a common interface.
"""

import asyncio
import base64
import json
import logging
import sys
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from getpass import getuser
from pathlib import Path
from typing import Any

import asyncssh
from asyncssh.connection import SSHClientConnection
from asyncssh.process import SSHClientProcess

from .arguments import merge_arguments
from .exceptions import (
    AuthenticationError,
    ConnectionError,
    ErrorContext,
    ErrorTypes,
    ModuleExecutionError,
    get_suggestions,
)
from .gate import GateBuildConfig, GateBuilder
from .message import GateProtocol
from .types import ExecutionConfig, GateConfig, HostConfig, ModuleResult
from .utils import find_module, module_wants_json

logger = logging.getLogger(__name__)


@dataclass
class ExecutionContext:
    """Context for module execution operations.

    Bundles all configuration needed for executing modules across an
    inventory, reducing function parameters from 11 to 1.

    Attributes:
        execution_config: Module execution configuration
        gate_config: Gate building and caching configuration
        module_dirs_override: Optional override for module directories

    Example:
        >>> from pathlib import Path
        >>> exec_config = ExecutionConfig(
        ...     module_name="ping",
        ...     module_dirs=[Path("/usr/lib/ftl/modules")]
        ... )
        >>> gate_config = GateConfig()
        >>> context = ExecutionContext(
        ...     execution_config=exec_config,
        ...     gate_config=gate_config
        ... )
    """

    execution_config: ExecutionConfig
    gate_config: GateConfig
    module_dirs_override: list[str] = field(default_factory=list)

    @property
    def module_name(self) -> str:
        """Get the module name from execution config."""
        return self.execution_config.module_name

    @property
    def module_args(self) -> dict[str, Any]:
        """Get module arguments from execution config."""
        return self.execution_config.module_args

    @property
    def dry_run(self) -> bool:
        """Check if this is a dry-run execution."""
        return self.execution_config.dry_run


@dataclass
class Gate:
    """Container for an active SSH gate connection.

    Holds all components needed for remote module execution through a gate:
    the SSH connection, the gate process, and the temporary directory.

    Attributes:
        conn: Active SSH connection to the remote host
        gate_process: Running gate process for module execution
        temp_dir: Temporary directory path on remote host

    Example:
        >>> gate = Gate(conn, process, "/tmp")
        >>> # Use gate for module execution
        >>> await close_gate(gate)
    """

    conn: SSHClientConnection
    gate_process: "SSHClientProcess[Any]"
    temp_dir: str


class ModuleRunner(ABC):
    """Abstract base class for module execution strategies.

    Defines the interface for executing modules on hosts, enabling
    pluggable execution strategies (local vs remote) with a common API.

    This follows the Strategy pattern, allowing runtime selection of
    execution method based on host configuration.
    """

    @abstractmethod
    async def run(
        self,
        host: HostConfig,
        context: ExecutionContext,
    ) -> ModuleResult:
        """Execute a module on a single host.

        Args:
            host: Host configuration for execution target
            context: Execution context with module and gate config

        Returns:
            ModuleResult containing execution outcome

        Raises:
            Exception: Various exceptions depending on implementation
        """
        pass

    @abstractmethod
    async def cleanup(self) -> None:
        """Clean up any resources held by this runner.

        Called when the runner is no longer needed. Implementations
        should close connections, release resources, etc.
        """
        pass


class ModuleRunnerFactory:
    """Factory for creating appropriate module runners.

    Selects the correct runner implementation based on host configuration,
    providing a unified interface for module execution.

    Example:
        >>> factory = ModuleRunnerFactory()
        >>> local_host = HostConfig(
        ...     name="localhost",
        ...     ansible_host="127.0.0.1",
        ...     ansible_connection="local"
        ... )
        >>> runner = factory.create_runner(local_host)
        >>> # Returns LocalModuleRunner
    """

    def __init__(self) -> None:
        """Initialize the factory."""
        self._local_runner: LocalModuleRunner | None = None
        self._remote_runner: RemoteModuleRunner | None = None

    def create_runner(self, host: HostConfig) -> ModuleRunner:
        """Create appropriate runner for the given host.

        Args:
            host: Host configuration to determine runner type

        Returns:
            ModuleRunner instance (Local or Remote)
        """
        if host.is_local:
            if self._local_runner is None:
                self._local_runner = LocalModuleRunner()
            return self._local_runner
        else:
            if self._remote_runner is None:
                self._remote_runner = RemoteModuleRunner()
            return self._remote_runner

    async def cleanup_all(self) -> None:
        """Clean up all created runners."""
        if self._local_runner:
            await self._local_runner.cleanup()
        if self._remote_runner:
            await self._remote_runner.cleanup()


class LocalModuleRunner(ModuleRunner):
    """Runner for executing modules locally without SSH.

    Executes modules directly on the local system using subprocess,
    bypassing SSH for improved performance on localhost operations.

    Example:
        >>> runner = LocalModuleRunner()
        >>> context = ExecutionContext(
        ...     execution_config=ExecutionConfig(module_name="ping"),
        ...     gate_config=GateConfig()
        ... )
        >>> host = HostConfig(
        ...     name="localhost",
        ...     ansible_host="127.0.0.1",
        ...     ansible_connection="local"
        ... )
        >>> result = await runner.run(host, context)
        >>> result.is_success
        True
    """

    async def run(
        self,
        host: HostConfig,
        context: ExecutionContext,
    ) -> ModuleResult:
        """Execute a module locally.

        Args:
            host: Host configuration (should be local)
            context: Execution context

        Returns:
            ModuleResult with execution outcome

        Raises:
            ModuleExecutionError: If execution fails
        """
        try:
            # Merge module args with host-specific overrides
            merged_args = merge_arguments(
                host,
                context.execution_config.module_args,
                context.execution_config.host_args,
            )

            # Find the module
            module_dirs = context.execution_config.module_dirs
            if context.module_dirs_override:
                module_dirs = [Path(d) for d in context.module_dirs_override]

            module_path = find_module(module_dirs, context.module_name)
            if module_path is None:
                return ModuleResult.error_result(
                    host_name=host.name,
                    error=f"Module {context.module_name} not found in {module_dirs}",
                )

            # Handle dry-run mode
            if context.dry_run:
                return self._dry_run_result(host, context.module_name, merged_args, module_path)

            # Execute the module based on its type
            # Python modules (.py extension) use JSON or new-style interface
            # Non-Python modules are treated as binary executables
            if module_path.suffix == ".py":
                if module_wants_json(module_path):
                    result_data = await self._run_json_module(module_path, merged_args)
                else:
                    result_data = await self._run_new_style_module(module_path, merged_args)
            else:
                # No .py extension - treat as binary executable
                result_data = await self._run_binary_module(module_path, merged_args)

            # Parse the result
            if isinstance(result_data, dict):
                output = result_data
            else:
                try:
                    output = json.loads(result_data)
                except (json.JSONDecodeError, TypeError):
                    output = {"stdout": str(result_data)}

            # Determine if module made changes
            changed = output.get("changed", False)

            return ModuleResult.success_result(host_name=host.name, output=output, changed=changed)

        except Exception as e:
            logger.exception(f"Error executing module {context.module_name}")
            return ModuleResult.error_result(
                host_name=host.name, error=f"Execution failed: {str(e)}"
            )

    async def _run_binary_module(self, module_path: Path, module_args: dict[str, Any]) -> str:
        """Execute a binary module with command-line arguments.

        Args:
            module_path: Path to the binary module
            module_args: Arguments to pass as command-line args

        Returns:
            Module output as string
        """
        # Build command-line arguments
        args_str = " ".join(f"{k}={v}" for k, v in module_args.items())
        cmd = f"{module_path} {args_str}"

        # Execute the module
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await proc.communicate()
        return stdout.decode()

    async def _run_json_module(self, module_path: Path, module_args: dict[str, Any]) -> str:
        """Execute a module that wants JSON input via file.

        Args:
            module_path: Path to the module
            module_args: Arguments to pass as JSON file

        Returns:
            Module output as string
        """
        # Create temporary JSON args file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(module_args, f)
            args_file = f.name

        try:
            # Execute module with args file path
            cmd = f"python3 {module_path} {args_file}"
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await proc.communicate()
            return stdout.decode()
        finally:
            # Clean up temp file
            Path(args_file).unlink(missing_ok=True)

    async def _run_new_style_module(self, module_path: Path, module_args: dict[str, Any]) -> str:
        """Execute a new-style module with JSON stdin.

        Args:
            module_path: Path to the module
            module_args: Arguments to pass via stdin as JSON

        Returns:
            Module output as string
        """
        # Prepare JSON input
        json_input = json.dumps(module_args).encode()

        # Execute module with JSON stdin
        cmd = f"python3 {module_path}"
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await proc.communicate(json_input)
        return stdout.decode()

    async def cleanup(self) -> None:
        """Clean up local runner resources.

        Local runner has no persistent resources, so this is a no-op.
        """
        pass

    def _dry_run_result(
        self,
        host: HostConfig,
        module_name: str,
        module_args: dict[str, Any],
        module_path: Path,
    ) -> ModuleResult:
        """Generate dry-run preview result without executing.

        Args:
            host: Host configuration
            module_name: Name of the module
            module_args: Arguments that would be passed
            module_path: Path to the module file

        Returns:
            ModuleResult with dry-run preview information
        """
        preview = self._generate_preview(module_name, module_args)

        return ModuleResult(
            host_name=host.name,
            success=True,
            changed=False,
            output={
                "dry_run": True,
                "would_execute": True,
                "module": module_name,
                "module_path": str(module_path),
                "connection": "local",
                "args": module_args,
                "preview": preview,
            },
        )

    def _generate_preview(self, module_name: str, module_args: dict[str, Any]) -> str:
        """Generate human-readable preview of what would happen.

        Args:
            module_name: Name of the module
            module_args: Arguments to the module

        Returns:
            Human-readable preview string
        """
        if module_name == "ping":
            data = module_args.get("data", "pong")
            return f"Would test connectivity (response: {data})"

        elif module_name == "file":
            path = module_args.get("path", "<unknown>")
            state = module_args.get("state", "<unknown>")
            mode = module_args.get("mode")

            if state == "touch":
                preview = f"Would create file: {path}"
            elif state == "directory":
                preview = f"Would create directory: {path}"
            elif state == "absent":
                preview = f"Would remove: {path}"
            elif state == "file":
                preview = f"Would verify file exists: {path}"
            else:
                preview = f"Would manage file: {path} (state={state})"

            if mode:
                preview += f" with mode {mode}"
            return preview

        elif module_name == "shell":
            cmd = module_args.get("cmd", "<unknown>")
            return f"Would execute: {cmd}"

        elif module_name == "copy":
            src = module_args.get("src", "<unknown>")
            dest = module_args.get("dest", "<unknown>")
            return f"Would copy {src} to {dest}"

        elif module_name == "setup":
            return "Would gather system facts"

        else:
            args_str = ", ".join(f"{k}={v}" for k, v in module_args.items())
            return f"Would execute module '{module_name}' with args: {args_str}"


class RemoteModuleRunner(ModuleRunner):
    """Runner for executing modules remotely via SSH gates.

    Manages SSH connections, gate processes, and remote module execution
    with connection pooling and caching for performance.

    Attributes:
        gate_cache: Cache of active gate connections by host
        gate_builder: Builder for creating gate executables
        protocol: Message protocol for gate communication

    Example:
        >>> runner = RemoteModuleRunner()
        >>> context = ExecutionContext(
        ...     execution_config=ExecutionConfig(module_name="ping"),
        ...     gate_config=GateConfig()
        ... )
        >>> host = HostConfig(
        ...     name="web01",
        ...     ansible_host="192.168.1.10"
        ... )
        >>> result = await runner.run(host, context)
    """

    def __init__(self) -> None:
        """Initialize the remote runner with empty gate cache."""
        self.gate_cache: dict[str, Gate] = {}
        self.gate_builder: GateBuilder | None = None
        self.protocol = GateProtocol()

    async def run(
        self,
        host: HostConfig,
        context: ExecutionContext,
    ) -> ModuleResult:
        """Execute a module remotely via SSH gate.

        Args:
            host: Remote host configuration
            context: Execution context

        Returns:
            ModuleResult with execution outcome

        Raises:
            ModuleExecutionError: If module execution fails
        """
        # Merge module args with host-specific overrides
        merged_args = merge_arguments(
            host,
            context.execution_config.module_args,
            context.execution_config.host_args,
        )

        # Extract connection parameters from host config
        ssh_host = host.ansible_host if host.ansible_host else host.name
        ssh_port = host.ansible_port if host.ansible_port else 22
        ssh_user = host.ansible_user if host.ansible_user else getuser()
        ssh_password = host.get_var("ansible_password")  # Optional password auth
        ssh_key_file = host.get_var("ssh_private_key_file")  # Optional SSH key (without ansible_ prefix)
        interpreter = host.ansible_python_interpreter if host.ansible_python_interpreter else sys.executable

        # Find module
        module_dirs = context.execution_config.module_dirs
        module_path = find_module(module_dirs, context.module_name)
        if module_path is None:
            raise ModuleExecutionError(f"Module {context.module_name} not found in {module_dirs}")

        # Handle dry-run mode - return preview without connecting
        if context.dry_run:
            return self._dry_run_result(
                host, context.module_name, merged_args, module_path,
                ssh_host, ssh_port, ssh_user
            )

        # Initialize gate builder if needed
        if self.gate_builder is None:
            cache_dir = context.gate_config.cache_dir
            if cache_dir is None:
                cache_dir = Path.home() / ".ftl2" / "gates"
            self.gate_builder = GateBuilder(cache_dir)

        # Get or create gate connection
        gate = await self._get_or_create_gate(
            host.name, ssh_host, ssh_port, ssh_user, ssh_password, ssh_key_file, interpreter, context
        )

        try:
            # Execute module through gate
            result_data = await self._execute_through_gate(
                gate, module_path, context.module_name, merged_args
            )

            # Cache the gate for reuse
            self.gate_cache[host.name] = gate

            # Convert to ModuleResult
            success = result_data.get("rc", 0) == 0
            return ModuleResult(
                host_name=host.name,
                success=success,
                changed=result_data.get("changed", False),
                output=result_data,
                error=result_data.get("stderr") if not success else None,
            )

        except Exception as e:
            # Clean up gate on error
            await self._close_gate(gate)
            if host.name in self.gate_cache:
                del self.gate_cache[host.name]
            raise ModuleExecutionError(f"Remote execution failed on {host.name}: {e}") from e

    async def _get_or_create_gate(
        self,
        host_name: str,
        ssh_host: str,
        ssh_port: int,
        ssh_user: str,
        ssh_password: str | None,
        ssh_key_file: str | None,
        interpreter: str,
        context: ExecutionContext,
    ) -> Gate:
        """Get cached gate or create new one.

        Args:
            host_name: Host identifier for caching
            ssh_host: SSH hostname/IP
            ssh_port: SSH port
            ssh_user: SSH username
            ssh_password: SSH password (optional, for password auth)
            ssh_key_file: SSH private key file path (optional, for key auth)
            interpreter: Remote Python interpreter path
            context: Execution context with gate config

        Returns:
            Active Gate connection
        """
        # Check cache first
        if host_name in self.gate_cache:
            logger.debug(f"Reusing cached gate for {host_name}")
            gate = self.gate_cache[host_name]
            del self.gate_cache[host_name]  # Remove from cache to use
            return gate

        # Create new gate connection
        logger.info(f"Creating new gate for {host_name}")
        return await self._connect_gate(ssh_host, ssh_port, ssh_user, ssh_password, ssh_key_file, interpreter, context)

    async def _connect_gate(
        self,
        ssh_host: str,
        ssh_port: int,
        ssh_user: str,
        ssh_password: str | None,
        ssh_key_file: str | None,
        interpreter: str,
        context: ExecutionContext,
        max_retries: int = 3,
        register_subsystem: bool = False,
    ) -> Gate:
        """Establish SSH connection and create gate.

        Args:
            ssh_host: SSH hostname/IP
            ssh_port: SSH port
            ssh_user: SSH username
            ssh_password: SSH password (optional, for password auth)
            ssh_key_file: SSH private key file path (optional, for key auth)
            interpreter: Remote Python interpreter path
            context: Execution context with gate config
            max_retries: Maximum connection retry attempts (default: 3)
            register_subsystem: If True, register the gate as an SSH
                subsystem after connecting (requires root)

        Returns:
            Active Gate connection

        Raises:
            Exception: On connection or gate creation failure after max retries
        """
        import asyncio

        last_error = None
        auth_method = "SSH key" if ssh_key_file else ("password" if ssh_password else "default keys")

        for attempt in range(1, max_retries + 1):
            try:
                logger.info(f"Connecting to {ssh_host}:{ssh_port} (attempt {attempt}/{max_retries})")

                # Connect to SSH
                connect_kwargs = {
                    "host": ssh_host,
                    "port": ssh_port,
                    "username": ssh_user,
                    "known_hosts": None,
                    "connect_timeout": 30,  # 30 seconds per attempt
                }
                # Add authentication method
                if ssh_password:
                    connect_kwargs["password"] = ssh_password
                    logger.debug("Using password authentication")
                elif ssh_key_file:
                    # Expand ~ in path
                    import os
                    expanded_key = os.path.expanduser(ssh_key_file)
                    logger.debug(f"Using SSH key file: {expanded_key}")
                    connect_kwargs["client_keys"] = [expanded_key]
                else:
                    logger.debug("No password or key file provided, using default SSH keys")

                conn = await asyncssh.connect(**connect_kwargs)

                # Verify Python version
                await self._check_version(conn, interpreter)

                # Deploy gate executable to per-user directory
                # Using ~/.ftl avoids name collisions between users in /tmp
                # and prevents one user's cached gate from blocking another's
                await conn.run("mkdir -p ~/.ftl && chmod 700 ~/.ftl", check=True)
                result = await conn.run("echo ~/.ftl", check=True)
                temp_dir = result.stdout.strip()
                gate_file, gate_local_path = await self._send_gate(conn, temp_dir, interpreter, context)

                # Start gate process
                gate_process, remote_gate_hash, used_subsystem = await self._open_gate(conn, gate_file, interpreter)

                # Check for version mismatch on subsystem connections
                if used_subsystem and remote_gate_hash:
                    import hashlib
                    local_hash = hashlib.sha256(open(gate_local_path, "rb").read()).hexdigest()[:16]
                    if local_hash != remote_gate_hash:
                        logger.info(
                            f"Gate version mismatch (local={local_hash}, remote={remote_gate_hash}), "
                            f"updating stable path for next connection"
                        )
                        await self._update_gate_stable_path(conn, gate_local_path)

                # Register as SSH subsystem if requested
                if register_subsystem:
                    await self._register_gate_subsystem(conn, gate_local_path, interpreter)

                logger.info(f"Connected to {ssh_host}:{ssh_port} successfully")
                return Gate(conn, gate_process, temp_dir)

            except asyncssh.misc.PermissionDenied as e:
                # Authentication failures should not retry - they won't succeed
                raise AuthenticationError(
                    message=f"SSH authentication failed using {auth_method}",
                    host=ssh_host,
                    host_address=ssh_host,
                    port=ssh_port,
                    user=ssh_user,
                    key_file=ssh_key_file or "",
                ) from e

            except (
                ConnectionRefusedError,
                ConnectionResetError,
                asyncssh.misc.ConnectionLost,
                TimeoutError,
                OSError,
            ) as e:
                last_error = e
                python_error_type = type(e).__name__

                # Classify the error
                if isinstance(e, ConnectionRefusedError):
                    error_type = ErrorTypes.CONNECTION_REFUSED
                elif isinstance(e, TimeoutError):
                    error_type = ErrorTypes.CONNECTION_TIMEOUT
                else:
                    error_type = ErrorTypes.HOST_UNREACHABLE

                if attempt < max_retries:
                    # Exponential backoff: 1s, 2s, 4s
                    delay = 2 ** (attempt - 1)
                    logger.warning(
                        f"Connection to {ssh_host}:{ssh_port} failed ({python_error_type}), "
                        f"retrying in {delay}s (attempt {attempt}/{max_retries})"
                    )
                    await asyncio.sleep(delay)
                else:
                    # Final attempt failed - raise with full context
                    raise ConnectionError(
                        message=f"Failed to connect after {max_retries} attempts: {python_error_type}",
                        host=ssh_host,
                        host_address=ssh_host,
                        port=ssh_port,
                        user=ssh_user,
                        error_type=error_type,
                        attempt=attempt,
                        max_attempts=max_retries,
                    ) from e

        # All retries exhausted (should not reach here, but just in case)
        raise ConnectionError(
            message=f"Failed to connect after {max_retries} attempts",
            host=ssh_host,
            host_address=ssh_host,
            port=ssh_port,
            user=ssh_user,
            error_type=ErrorTypes.CONNECTION_TIMEOUT,
            attempt=max_retries,
            max_attempts=max_retries,
        )

    async def _check_version(self, conn: SSHClientConnection, interpreter: str) -> None:
        """Verify remote Python version meets requirements.

        Args:
            conn: Active SSH connection
            interpreter: Python interpreter path to check

        Raises:
            Exception: If Python version < 3 or unexpected output
        """
        result = await conn.run(f"{interpreter} --version")
        if result.stdout:
            output = str(result.stdout).strip()
            for line in output.split("\n"):
                line = line.strip()
                if line.startswith("Python "):
                    _, _, version = line.partition(" ")
                    major, _, _ = version.split(".")
                    if int(major) < 3:
                        raise Exception("Python 3 or greater required for interpreter")
                else:
                    raise Exception(f"Unexpected shell output: {line}")

    async def _send_gate(
        self,
        conn: SSHClientConnection,
        temp_dir: str,
        interpreter: str,
        context: ExecutionContext,
    ) -> tuple[str, str]:
        """Deploy gate executable to remote host.

        Args:
            conn: Active SSH connection
            temp_dir: Remote temporary directory
            interpreter: Python interpreter path
            context: Execution context with gate config

        Returns:
            Tuple of (remote gate path, local gate path)
        """
        # Build gate executable
        assert self.gate_builder is not None
        gate_config = GateBuildConfig(
            modules=context.execution_config.modules,
            module_dirs=context.execution_config.module_dirs,
            dependencies=context.execution_config.dependencies,
            interpreter=interpreter,
        )
        gate_path, gate_hash = self.gate_builder.build(gate_config)
        gate_file_name = f"{temp_dir}/ftl_gate_{gate_hash}.pyz"

        # Transfer if needed
        async with conn.start_sftp_client() as sftp:
            if not await sftp.exists(gate_file_name):
                logger.info(f"Sending gate to {gate_file_name}")
                await sftp.put(gate_path, gate_file_name)
                await conn.run(f"chmod 700 {gate_file_name}", check=True)
            else:
                # Check if file is complete (non-zero size)
                stats = await sftp.lstat(gate_file_name)
                if stats.size == 0:
                    logger.info(f"Resending incomplete gate {gate_file_name}")
                    await sftp.put(gate_path, gate_file_name)
                    await conn.run(f"chmod 700 {gate_file_name}", check=True)
                else:
                    logger.info(f"Reusing existing gate {gate_file_name}")

        return gate_file_name, str(gate_path)

    async def _open_gate(
        self, conn: SSHClientConnection, gate_file: str, interpreter: str
    ) -> tuple["SSHClientProcess[Any]", str, bool]:
        """Start gate process and perform handshake.

        Tries SSH subsystem first (zero shell overhead), falls back to
        exec if the subsystem isn't registered on the remote host.

        Args:
            conn: Active SSH connection
            gate_file: Path to gate executable on remote host
            interpreter: Python interpreter path to use

        Returns:
            Tuple of (gate process, remote gate hash, used subsystem)

        Raises:
            Exception: If gate fails to start or handshake fails
        """
        # Try SSH subsystem first — no shell startup, no PATH lookup
        used_subsystem = False
        try:
            process = await conn.create_process(subsystem="ftl2-gate", encoding=None)
            used_subsystem = True
            logger.info("Connected via SSH subsystem")
        except (asyncssh.ChannelOpenError, asyncssh.Error):
            # Subsystem not registered — fall back to exec
            process = await conn.create_process(f"{interpreter} {gate_file}", encoding=None)
            logger.info("Connected via SSH exec (subsystem not available)")

        # Send Hello and wait for response
        await self.protocol.send_message(process.stdin, "Hello", {})  # type: ignore[arg-type]
        response = await self.protocol.read_message(process.stdout)  # type: ignore[arg-type]

        if response is None or response[0] != "Hello":
            error = await process.stderr.read()
            logger.error(f"Gate handshake failed: {error}")
            raise Exception(f"Gate handshake failed: {error}")

        # Extract gate hash from Hello response
        _, hello_data = response
        remote_gate_hash = ""
        if isinstance(hello_data, dict):
            remote_gate_hash = hello_data.get("gate_hash", "")

        return process, remote_gate_hash, used_subsystem

    async def _execute_through_gate(
        self,
        gate: Gate,
        module_path: Path,
        module_name: str,
        module_args: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute module through gate connection.

        Args:
            gate: Active gate connection
            module_path: Path to module file
            module_name: Module name
            module_args: Module arguments

        Returns:
            Module execution result dictionary
        """
        # Try without uploading module first
        try:
            await self.protocol.send_message(
                gate.gate_process.stdin,  # type: ignore[arg-type]
                "Module",
                {
                    "module_name": module_name,
                    "module_args": module_args,
                },
            )
            response = await self.protocol.read_message(gate.gate_process.stdout)  # type: ignore[arg-type]

            if response is None:
                raise ModuleExecutionError("No response from gate")

            msg_type, data = response

            if msg_type == "ModuleResult":
                return dict(data)  # Ensure it's a dict
            elif msg_type == "ModuleNotFound":
                # Module not in gate, upload and retry
                return await self._execute_with_upload(gate, module_path, module_name, module_args)
            elif msg_type == "Error":
                raise ModuleExecutionError(f"Gate error: {data.get('message', 'Unknown error')}")
            else:
                raise ModuleExecutionError(f"Unexpected response type: {msg_type}")

        except Exception as e:
            logger.exception(f"Module execution failed: {e}")
            raise

    async def _execute_with_upload(
        self,
        gate: Gate,
        module_path: Path,
        module_name: str,
        module_args: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute module after uploading to gate.

        Args:
            gate: Active gate connection
            module_path: Path to module file
            module_name: Module name
            module_args: Module arguments

        Returns:
            Module execution result dictionary
        """
        # Read and encode module
        with open(module_path, "rb") as f:
            module_content = f.read()
        module_b64 = base64.b64encode(module_content).decode()

        # Send with module content
        await self.protocol.send_message(
            gate.gate_process.stdin,  # type: ignore[arg-type]
            "Module",
            {
                "module": module_b64,
                "module_name": module_name,
                "module_args": module_args,
            },
        )

        response = await self.protocol.read_message(gate.gate_process.stdout)  # type: ignore[arg-type]

        if response is None:
            raise ModuleExecutionError("No response from gate after upload")

        msg_type, data = response

        if msg_type == "ModuleResult":
            return dict(data)  # Ensure it's a dict
        elif msg_type == "Error":
            raise ModuleExecutionError(f"Gate error: {data.get('message', 'Unknown error')}")
        else:
            raise ModuleExecutionError(f"Unexpected response type: {msg_type}")

    async def run_ftl_module(
        self,
        gate: Gate,
        module_name: str,
        module_source: bytes,
        module_args: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute an FTL module (async Python) through the gate.

        Unlike Ansible-style modules which run as subprocesses, FTL modules
        are executed in-process by the gate using compile() and exec().

        Args:
            gate: Active gate connection
            module_name: Name of the module (for error messages)
            module_source: Python source code as bytes
            module_args: Arguments to pass to the module's main() function

        Returns:
            Module result dictionary from the module's main() function

        Raises:
            ModuleExecutionError: If module execution fails
        """
        module_b64 = base64.b64encode(module_source).decode()

        await self.protocol.send_message(
            gate.gate_process.stdin,  # type: ignore[arg-type]
            "FTLModule",
            {
                "module": module_b64,
                "module_name": module_name,
                "module_args": module_args,
            },
        )

        response = await self.protocol.read_message(gate.gate_process.stdout)  # type: ignore[arg-type]

        if response is None:
            raise ModuleExecutionError("No response from gate for FTLModule")

        msg_type, data = response

        if msg_type == "FTLModuleResult":
            return dict(data)
        elif msg_type == "Error":
            raise ModuleExecutionError(f"FTL module error: {data.get('message', 'Unknown error')}")
        else:
            raise ModuleExecutionError(f"Unexpected response type for FTLModule: {msg_type}")

    GATE_SUBSYSTEM_PATH = "/usr/local/lib/ftl2/gate.pyz"
    GATE_SUBSYSTEM_NAME = "ftl2-gate"

    async def _update_gate_stable_path(
        self,
        conn: SSHClientConnection,
        gate_local_path: str,
    ) -> None:
        """Update the gate binary at the stable subsystem path.

        Uploads the new gate so the next subsystem connection uses it.
        No sshd reload needed — sshd forks and execs fresh each time.

        Args:
            conn: Active SSH connection
            gate_local_path: Local path to the new gate .pyz file
        """
        import os
        dest = self.GATE_SUBSYSTEM_PATH
        try:
            await conn.run(f"mkdir -p {os.path.dirname(dest)}", check=True)
            async with conn.start_sftp_client() as sftp:
                await sftp.put(gate_local_path, dest)
            await conn.run(f"chmod 755 {dest}", check=True)
            logger.info(f"Updated gate at {dest}")
        except Exception as e:
            logger.warning(f"Failed to update gate at stable path: {e}")

    async def _register_gate_subsystem(
        self,
        conn: SSHClientConnection,
        gate_local_path: str,
        interpreter: str,
    ) -> bool:
        """Register the gate as an SSH subsystem on a remote host.

        Uploads the gate to a stable path and adds a Subsystem line to
        sshd_config. Idempotent — skips if already registered with the
        same gate binary.

        Args:
            conn: Active SSH connection (must be root)
            gate_local_path: Local path to the gate .pyz file
            interpreter: Python interpreter path on remote host

        Returns:
            True if subsystem was registered (or already registered),
            False if registration failed (not root, etc.)
        """
        import os

        dest = self.GATE_SUBSYSTEM_PATH
        subsystem_line = f"Subsystem {self.GATE_SUBSYSTEM_NAME} {interpreter} {dest}"

        try:
            # Check if we're root
            result = await conn.run("id -u", check=True)
            if result.stdout.strip() != "0":
                logger.debug("Not root, skipping subsystem registration")
                return False

            # Create directory
            await conn.run(f"mkdir -p {os.path.dirname(dest)}", check=True)

            # Check if gate already exists at stable path with same size
            local_size = os.path.getsize(gate_local_path)
            result = await conn.run(f"stat -c %s {dest} 2>/dev/null || echo 0")
            remote_size = int(result.stdout.strip())

            if remote_size != local_size:
                # Upload gate to stable path
                async with conn.start_sftp_client() as sftp:
                    await sftp.put(gate_local_path, dest)
                await conn.run(f"chmod 755 {dest}", check=True)
                logger.info(f"Uploaded gate to {dest}")
            else:
                logger.info(f"Gate at {dest} is up to date")

            # Check if subsystem line already in sshd_config
            result = await conn.run(
                f"grep -q '^Subsystem {self.GATE_SUBSYSTEM_NAME}' /etc/ssh/sshd_config"
            )
            if result.exit_status == 0:
                # Line exists — update it if different
                result = await conn.run(
                    f"grep -q '^{subsystem_line}$' /etc/ssh/sshd_config"
                )
                if result.exit_status == 0:
                    logger.info("Subsystem already registered in sshd_config")
                    return True
                else:
                    # Update existing line
                    await conn.run(
                        f"sed -i 's|^Subsystem {self.GATE_SUBSYSTEM_NAME}.*|{subsystem_line}|' /etc/ssh/sshd_config",
                        check=True,
                    )
                    logger.info("Updated subsystem line in sshd_config")
            else:
                # Add new line
                await conn.run(
                    f"echo '{subsystem_line}' >> /etc/ssh/sshd_config",
                    check=True,
                )
                logger.info("Added subsystem line to sshd_config")

            # Reload sshd
            await conn.run("systemctl reload sshd", check=True)
            logger.info("Reloaded sshd")
            return True

        except Exception as e:
            logger.warning(f"Failed to register gate subsystem: {e}")
            return False

    async def _close_gate(self, gate: Gate) -> None:
        """Close gate connection and clean up resources.

        Args:
            gate: Gate connection to close
        """
        try:
            # Send shutdown message
            await self.protocol.send_message(gate.gate_process.stdin, "Shutdown", {})  # type: ignore[arg-type]
            # Read any remaining stderr
            if gate.gate_process.exit_status is not None:
                await gate.gate_process.stderr.read()
        except Exception:
            pass  # Ignore errors during shutdown
        finally:
            gate.conn.close()

    async def close_all(self) -> None:
        """Close all cached gate connections.

        Call this when done with remote execution to clean up resources.
        """
        for host_name in list(self.gate_cache.keys()):
            gate = self.gate_cache.pop(host_name)
            await self._close_gate(gate)

    def _dry_run_result(
        self,
        host: HostConfig,
        module_name: str,
        module_args: dict[str, Any],
        module_path: Path,
        ssh_host: str,
        ssh_port: int,
        ssh_user: str,
    ) -> ModuleResult:
        """Generate dry-run preview result without connecting.

        Args:
            host: Host configuration
            module_name: Name of the module
            module_args: Arguments that would be passed
            module_path: Path to the module file
            ssh_host: SSH hostname/IP
            ssh_port: SSH port
            ssh_user: SSH username

        Returns:
            ModuleResult with dry-run preview information
        """
        preview = self._generate_preview(module_name, module_args)

        return ModuleResult(
            host_name=host.name,
            success=True,
            changed=False,
            output={
                "dry_run": True,
                "would_execute": True,
                "module": module_name,
                "module_path": str(module_path),
                "connection": "ssh",
                "ssh_host": ssh_host,
                "ssh_port": ssh_port,
                "ssh_user": ssh_user,
                "args": module_args,
                "preview": preview,
            },
        )

    def _generate_preview(self, module_name: str, module_args: dict[str, Any]) -> str:
        """Generate human-readable preview of what would happen.

        Args:
            module_name: Name of the module
            module_args: Arguments to the module

        Returns:
            Human-readable preview string
        """
        if module_name == "ping":
            data = module_args.get("data", "pong")
            return f"Would test connectivity (response: {data})"

        elif module_name == "file":
            path = module_args.get("path", "<unknown>")
            state = module_args.get("state", "<unknown>")
            mode = module_args.get("mode")

            if state == "touch":
                preview = f"Would create file: {path}"
            elif state == "directory":
                preview = f"Would create directory: {path}"
            elif state == "absent":
                preview = f"Would remove: {path}"
            elif state == "file":
                preview = f"Would verify file exists: {path}"
            else:
                preview = f"Would manage file: {path} (state={state})"

            if mode:
                preview += f" with mode {mode}"
            return preview

        elif module_name == "shell":
            cmd = module_args.get("cmd", "<unknown>")
            return f"Would execute: {cmd}"

        elif module_name == "copy":
            src = module_args.get("src", "<unknown>")
            dest = module_args.get("dest", "<unknown>")
            return f"Would copy {src} to {dest}"

        elif module_name == "setup":
            return "Would gather system facts"

        else:
            args_str = ", ".join(f"{k}={v}" for k, v in module_args.items())
            return f"Would execute module '{module_name}' with args: {args_str}"

    async def cleanup(self) -> None:
        """Close all cached gate connections."""
        for host_name, gate in list(self.gate_cache.items()):
            logger.debug(f"Closing cached gate for {host_name}")
            await self._close_gate(gate)
        self.gate_cache.clear()
