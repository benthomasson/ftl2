"""Module executor for Ansible modules.

Provides local and remote execution of Ansible modules:
- Local: Direct execution, no bundling needed
- Local streaming: Async execution with real-time event callbacks
- Remote: Execute pre-staged bundles via SSH
"""

import asyncio
import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from ftl2.module_loading.fqcn import (
    resolve_fqcn,
    get_collection_paths,
    find_ansible_builtin_path,
)
from ftl2.module_loading.bundle import Bundle, BundleCache
from ftl2.events import parse_events, parse_event

logger = logging.getLogger(__name__)


@dataclass
class ExecutionResult:
    """Result of module execution.

    Attributes:
        success: Whether execution succeeded
        changed: Whether the module made changes
        output: The module's output dictionary
        error: Error message if failed
        return_code: Process return code
        stdout: Raw stdout from execution
        stderr: Raw stderr from execution (with events removed)
        events: List of parsed events emitted during execution
    """

    success: bool
    changed: bool = False
    output: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    return_code: int = 0
    stdout: str = ""
    stderr: str = ""
    events: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_module_output(cls, stdout: str, stderr: str, return_code: int) -> "ExecutionResult":
        """Create result from module execution output.

        Parses JSON-line events from stderr, separating them from regular
        stderr output. Events are stored in the `events` field.
        """
        # Parse events from stderr
        events, remaining_stderr = parse_events(stderr)

        try:
            output = json.loads(stdout) if stdout.strip() else {}
        except json.JSONDecodeError as e:
            return cls(
                success=False,
                error=f"Invalid JSON output: {e}",
                return_code=return_code,
                stdout=stdout,
                stderr=remaining_stderr,
                events=events,
            )

        # Check for failure indicators
        failed = output.get("failed", False)
        if failed or return_code != 0:
            return cls(
                success=False,
                changed=output.get("changed", False),
                output=output,
                error=output.get("msg", remaining_stderr or "Unknown error"),
                return_code=return_code,
                stdout=stdout,
                stderr=remaining_stderr,
                events=events,
            )

        return cls(
            success=True,
            changed=output.get("changed", False),
            output=output,
            return_code=return_code,
            stdout=stdout,
            stderr=remaining_stderr,
            events=events,
        )


def get_module_utils_pythonpath() -> str:
    """Get PYTHONPATH for module_utils imports.

    Returns paths where ansible.module_utils and collection
    module_utils can be imported from.
    """
    paths: list[str] = []

    # Add collection paths
    for collection_path in get_collection_paths():
        if collection_path.exists():
            paths.append(str(collection_path))

    # Add ansible core path (parent of module_utils)
    builtin_path = find_ansible_builtin_path()
    if builtin_path:
        # modules path -> parent is ansible package
        ansible_package = builtin_path.parent
        if ansible_package.exists():
            # We need the parent of ansible/ for imports to work
            paths.append(str(ansible_package.parent))

    return os.pathsep.join(paths)


def execute_local(
    module_path: Path,
    params: dict[str, Any],
    timeout: int = 300,
    check_mode: bool = False,
) -> ExecutionResult:
    """Execute a module locally without bundling.

    For local execution, we don't need bundles. The module and its
    dependencies are already on the filesystem. We just set PYTHONPATH
    so module_utils can be imported.

    Args:
        module_path: Path to the module file
        params: Module parameters (ANSIBLE_MODULE_ARGS)
        timeout: Execution timeout in seconds
        check_mode: Whether to run in check mode

    Returns:
        ExecutionResult with output and status
    """
    # Build module args
    module_args = dict(params)
    if check_mode:
        module_args["_ansible_check_mode"] = True

    stdin_data = json.dumps({"ANSIBLE_MODULE_ARGS": module_args})

    # Set up environment with PYTHONPATH for module_utils
    env = os.environ.copy()
    extra_pythonpath = get_module_utils_pythonpath()
    if extra_pythonpath:
        existing = env.get("PYTHONPATH", "")
        if existing:
            env["PYTHONPATH"] = f"{extra_pythonpath}{os.pathsep}{existing}"
        else:
            env["PYTHONPATH"] = extra_pythonpath

    logger.debug(f"Executing local module: {module_path}")
    logger.debug(f"PYTHONPATH: {env.get('PYTHONPATH', '')}")

    try:
        # Use -I (isolated mode) to prevent Python from adding the module's
        # directory to sys.path, which would cause name shadowing issues
        # (e.g., ansible/modules/tempfile.py shadows stdlib tempfile)
        result = subprocess.run(
            [sys.executable, "-I", str(module_path)],
            input=stdin_data,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )

        return ExecutionResult.from_module_output(
            result.stdout,
            result.stderr,
            result.returncode,
        )

    except subprocess.TimeoutExpired:
        return ExecutionResult(
            success=False,
            error=f"Module execution timed out after {timeout}s",
            return_code=-1,
        )
    except Exception as e:
        return ExecutionResult(
            success=False,
            error=f"Execution failed: {e}",
            return_code=-1,
        )


async def execute_local_streaming(
    module_path: Path,
    params: dict[str, Any],
    timeout: int = 300,
    check_mode: bool = False,
    event_callback: Callable[[dict[str, Any]], None] | None = None,
) -> ExecutionResult:
    """Execute a module locally with real-time event streaming.

    Unlike execute_local(), this async function streams stderr line by line
    and invokes the event_callback for each event as it's emitted. This
    enables real-time progress reporting for long-running operations.

    Args:
        module_path: Path to the module file
        params: Module parameters (ANSIBLE_MODULE_ARGS)
        timeout: Execution timeout in seconds
        check_mode: Whether to run in check mode
        event_callback: Called for each event as it's emitted (optional)

    Returns:
        ExecutionResult with output, status, and collected events

    Example:
        async def on_event(event):
            if event["event"] == "progress":
                print(f"Progress: {event['percent']}%")

        result = await execute_local_streaming(
            Path("module.py"),
            {"src": "/large/file"},
            event_callback=on_event,
        )
    """
    # Build module args
    module_args = dict(params)
    if check_mode:
        module_args["_ansible_check_mode"] = True

    stdin_data = json.dumps({"ANSIBLE_MODULE_ARGS": module_args}).encode()

    # Set up environment with PYTHONPATH for module_utils
    env = os.environ.copy()
    extra_pythonpath = get_module_utils_pythonpath()
    if extra_pythonpath:
        existing = env.get("PYTHONPATH", "")
        if existing:
            env["PYTHONPATH"] = f"{extra_pythonpath}{os.pathsep}{existing}"
        else:
            env["PYTHONPATH"] = extra_pythonpath

    logger.debug(f"Executing local module (streaming): {module_path}")

    try:
        # Create async subprocess
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-I", str(module_path),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        # Send params to stdin and close
        process.stdin.write(stdin_data)
        await process.stdin.drain()
        process.stdin.close()
        await process.stdin.wait_closed()

        # Stream stderr for events
        events: list[dict[str, Any]] = []
        other_stderr_lines: list[str] = []

        async def read_stderr():
            """Read stderr line by line, parsing events."""
            async for line_bytes in process.stderr:
                line = line_bytes.decode().rstrip('\n\r')
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

        # Read stdout and stderr concurrently with timeout
        async def read_stdout():
            """Read all stdout."""
            return await process.stdout.read()

        try:
            stdout_bytes, _ = await asyncio.wait_for(
                asyncio.gather(read_stdout(), read_stderr()),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            return ExecutionResult(
                success=False,
                error=f"Module execution timed out after {timeout}s",
                return_code=-1,
                events=events,  # Return any events collected before timeout
            )

        await process.wait()

        # Parse result from stdout
        stdout_str = stdout_bytes.decode() if stdout_bytes else ""
        stderr_str = "\n".join(other_stderr_lines)

        try:
            output = json.loads(stdout_str) if stdout_str.strip() else {}
        except json.JSONDecodeError as e:
            return ExecutionResult(
                success=False,
                error=f"Invalid JSON output: {e}",
                return_code=process.returncode or 0,
                stdout=stdout_str,
                stderr=stderr_str,
                events=events,
            )

        # Check for failure indicators
        failed = output.get("failed", False)
        return_code = process.returncode or 0

        if failed or return_code != 0:
            return ExecutionResult(
                success=False,
                changed=output.get("changed", False),
                output=output,
                error=output.get("msg", stderr_str or "Unknown error"),
                return_code=return_code,
                stdout=stdout_str,
                stderr=stderr_str,
                events=events,
            )

        return ExecutionResult(
            success=True,
            changed=output.get("changed", False),
            output=output,
            return_code=return_code,
            stdout=stdout_str,
            stderr=stderr_str,
            events=events,
        )

    except Exception as e:
        return ExecutionResult(
            success=False,
            error=f"Execution failed: {e}",
            return_code=-1,
        )


async def execute_local_fqcn_streaming(
    fqcn: str,
    params: dict[str, Any],
    timeout: int = 300,
    check_mode: bool = False,
    playbook_dir: Path | None = None,
    extra_paths: list[Path] | None = None,
    event_callback: Callable[[dict[str, Any]], None] | None = None,
) -> ExecutionResult:
    """Execute a module locally by FQCN with real-time event streaming.

    Convenience function that resolves the FQCN and executes with streaming.

    Args:
        fqcn: Fully qualified collection name
        params: Module parameters
        timeout: Execution timeout in seconds
        check_mode: Whether to run in check mode
        playbook_dir: Optional playbook directory for collection search
        extra_paths: Optional additional collection paths
        event_callback: Called for each event as it's emitted

    Returns:
        ExecutionResult with output and status
    """
    try:
        module_path = resolve_fqcn(fqcn, playbook_dir, extra_paths)
    except Exception as e:
        return ExecutionResult(
            success=False,
            error=f"Failed to resolve module: {e}",
            return_code=-1,
        )

    return await execute_local_streaming(
        module_path, params, timeout, check_mode, event_callback
    )


def execute_local_fqcn(
    fqcn: str,
    params: dict[str, Any],
    timeout: int = 300,
    check_mode: bool = False,
    playbook_dir: Path | None = None,
    extra_paths: list[Path] | None = None,
) -> ExecutionResult:
    """Execute a module locally by FQCN.

    Convenience function that resolves the FQCN and executes.

    Args:
        fqcn: Fully qualified collection name
        params: Module parameters
        timeout: Execution timeout in seconds
        check_mode: Whether to run in check mode
        playbook_dir: Optional playbook directory for collection search
        extra_paths: Optional additional collection paths

    Returns:
        ExecutionResult with output and status
    """
    try:
        module_path = resolve_fqcn(fqcn, playbook_dir, extra_paths)
    except Exception as e:
        return ExecutionResult(
            success=False,
            error=f"Failed to resolve module: {e}",
            return_code=-1,
        )

    return execute_local(module_path, params, timeout, check_mode)


def execute_bundle_local(
    bundle: Bundle,
    params: dict[str, Any],
    timeout: int = 300,
    check_mode: bool = False,
    work_dir: Path | None = None,
) -> ExecutionResult:
    """Execute a bundle locally (for testing).

    This is primarily for testing bundles before deploying to remote hosts.

    Args:
        bundle: The bundle to execute
        params: Module parameters
        timeout: Execution timeout in seconds
        check_mode: Whether to run in check mode
        work_dir: Optional working directory for bundle file

    Returns:
        ExecutionResult with output and status
    """
    import tempfile

    # Build module args
    module_args = dict(params)
    if check_mode:
        module_args["_ansible_check_mode"] = True

    stdin_data = json.dumps({"ANSIBLE_MODULE_ARGS": module_args})

    # Write bundle to temp file
    if work_dir:
        bundle_path = work_dir / f"{bundle.info.content_hash}.pyz"
        bundle.write_to_file(bundle_path)
        cleanup = False
    else:
        # Use temp directory
        temp_dir = tempfile.mkdtemp(prefix="ftl2_bundle_")
        bundle_path = Path(temp_dir) / f"{bundle.info.content_hash}.pyz"
        bundle.write_to_file(bundle_path)
        cleanup = True

    try:
        result = subprocess.run(
            [sys.executable, str(bundle_path)],
            input=stdin_data,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        return ExecutionResult.from_module_output(
            result.stdout,
            result.stderr,
            result.returncode,
        )

    except subprocess.TimeoutExpired:
        return ExecutionResult(
            success=False,
            error=f"Bundle execution timed out after {timeout}s",
            return_code=-1,
        )
    except Exception as e:
        return ExecutionResult(
            success=False,
            error=f"Bundle execution failed: {e}",
            return_code=-1,
        )
    finally:
        if cleanup:
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)


class RemoteHost(Protocol):
    """Protocol for remote host execution."""

    async def run(
        self,
        command: str,
        stdin: str = "",
        timeout: int = 300,
    ) -> tuple[str, str, int]:
        """Run a command on the remote host.

        Returns:
            Tuple of (stdout, stderr, return_code)
        """
        ...

    async def run_streaming(
        self,
        command: str,
        stdin: str = "",
        timeout: int = 300,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> tuple[str, str, int, list[dict[str, Any]]]:
        """Run a command with real-time event streaming.

        Returns:
            Tuple of (stdout, stderr, return_code, events)
        """
        ...

    async def has_file(self, path: str) -> bool:
        """Check if a file exists on the remote host."""
        ...

    async def write_file(self, path: str, content: bytes) -> None:
        """Write content to a file on the remote host."""
        ...


async def stage_bundle_remote(
    host: RemoteHost,
    bundle: Bundle,
    bundle_dir: str = "/tmp/ftl2_bundles",
) -> str:
    """Stage a bundle on a remote host.

    Args:
        host: Remote host to stage on
        bundle: Bundle to stage
        bundle_dir: Directory on remote host for bundles

    Returns:
        Path to bundle on remote host
    """
    bundle_path = f"{bundle_dir}/{bundle.info.content_hash}.pyz"

    # Check if already staged
    if await host.has_file(bundle_path):
        logger.debug(f"Bundle already staged: {bundle_path}")
        return bundle_path

    # Create directory and write bundle
    await host.run(f"mkdir -p {bundle_dir}")
    await host.write_file(bundle_path, bundle.data)
    logger.info(f"Staged bundle on remote: {bundle_path}")

    return bundle_path


async def execute_remote(
    host: RemoteHost,
    bundle_path: str,
    params: dict[str, Any],
    timeout: int = 300,
    check_mode: bool = False,
) -> ExecutionResult:
    """Execute a bundle on a remote host.

    Args:
        host: Remote host to execute on
        bundle_path: Path to bundle on remote host
        params: Module parameters
        timeout: Execution timeout in seconds
        check_mode: Whether to run in check mode

    Returns:
        ExecutionResult with output and status
    """
    # Build module args
    module_args = dict(params)
    if check_mode:
        module_args["_ansible_check_mode"] = True

    stdin_data = json.dumps({"ANSIBLE_MODULE_ARGS": module_args})

    logger.debug(f"Executing remote bundle: {bundle_path}")

    try:
        stdout, stderr, return_code = await host.run(
            f"python3 {bundle_path}",
            stdin=stdin_data,
            timeout=timeout,
        )

        return ExecutionResult.from_module_output(stdout, stderr, return_code)

    except Exception as e:
        return ExecutionResult(
            success=False,
            error=f"Remote execution failed: {e}",
            return_code=-1,
        )


async def execute_remote_with_staging(
    host: RemoteHost,
    bundle: Bundle,
    params: dict[str, Any],
    timeout: int = 300,
    check_mode: bool = False,
    bundle_dir: str = "/tmp/ftl2_bundles",
) -> ExecutionResult:
    """Stage bundle if needed and execute on remote host.

    Convenience function that handles staging automatically.

    Args:
        host: Remote host to execute on
        bundle: Bundle to execute
        params: Module parameters
        timeout: Execution timeout in seconds
        check_mode: Whether to run in check mode
        bundle_dir: Directory on remote host for bundles

    Returns:
        ExecutionResult with output and status
    """
    bundle_path = await stage_bundle_remote(host, bundle, bundle_dir)
    return await execute_remote(host, bundle_path, params, timeout, check_mode)


async def execute_remote_streaming(
    host: RemoteHost,
    bundle_path: str,
    params: dict[str, Any],
    timeout: int = 300,
    check_mode: bool = False,
    event_callback: Callable[[dict[str, Any]], None] | None = None,
) -> ExecutionResult:
    """Execute a bundle on a remote host with real-time event streaming.

    Unlike execute_remote(), this function streams stderr line by line
    and invokes the event_callback for each event as it's emitted.

    Args:
        host: Remote host to execute on
        bundle_path: Path to bundle on remote host
        params: Module parameters
        timeout: Execution timeout in seconds
        check_mode: Whether to run in check mode
        event_callback: Called for each event as it's emitted

    Returns:
        ExecutionResult with output, status, and collected events
    """
    # Build module args
    module_args = dict(params)
    if check_mode:
        module_args["_ansible_check_mode"] = True

    stdin_data = json.dumps({"ANSIBLE_MODULE_ARGS": module_args})

    logger.debug(f"Executing remote bundle (streaming): {bundle_path}")

    try:
        stdout, stderr, return_code, events = await host.run_streaming(
            f"python3 {bundle_path}",
            stdin=stdin_data,
            timeout=timeout,
            event_callback=event_callback,
        )

        # Parse result from stdout
        try:
            output = json.loads(stdout) if stdout.strip() else {}
        except json.JSONDecodeError as e:
            return ExecutionResult(
                success=False,
                error=f"Invalid JSON output: {e}",
                return_code=return_code,
                stdout=stdout,
                stderr=stderr,
                events=events,
            )

        # Check for failure indicators
        failed = output.get("failed", False)
        if failed or return_code != 0:
            return ExecutionResult(
                success=False,
                changed=output.get("changed", False),
                output=output,
                error=output.get("msg", stderr or "Unknown error"),
                return_code=return_code,
                stdout=stdout,
                stderr=stderr,
                events=events,
            )

        return ExecutionResult(
            success=True,
            changed=output.get("changed", False),
            output=output,
            return_code=return_code,
            stdout=stdout,
            stderr=stderr,
            events=events,
        )

    except Exception as e:
        return ExecutionResult(
            success=False,
            error=f"Remote execution failed: {e}",
            return_code=-1,
        )


async def execute_remote_with_staging_streaming(
    host: RemoteHost,
    bundle: Bundle,
    params: dict[str, Any],
    timeout: int = 300,
    check_mode: bool = False,
    bundle_dir: str = "/tmp/ftl2_bundles",
    event_callback: Callable[[dict[str, Any]], None] | None = None,
) -> ExecutionResult:
    """Stage bundle if needed and execute on remote host with event streaming.

    Convenience function that handles staging automatically and provides
    real-time event streaming.

    Args:
        host: Remote host to execute on
        bundle: Bundle to execute
        params: Module parameters
        timeout: Execution timeout in seconds
        check_mode: Whether to run in check mode
        bundle_dir: Directory on remote host for bundles
        event_callback: Called for each event as it's emitted

    Returns:
        ExecutionResult with output, status, and collected events
    """
    bundle_path = await stage_bundle_remote(host, bundle, bundle_dir)
    return await execute_remote_streaming(
        host, bundle_path, params, timeout, check_mode, event_callback
    )


@dataclass
class ModuleExecutor:
    """Executor for Ansible modules with smart local/remote handling.

    Uses direct execution for local hosts (no bundling) and
    bundle execution for remote hosts.
    """

    bundle_cache: BundleCache = field(default_factory=BundleCache)
    playbook_dir: Path | None = None
    extra_collection_paths: list[Path] = field(default_factory=list)
    default_timeout: int = 300

    def execute_local(
        self,
        fqcn: str,
        params: dict[str, Any],
        timeout: int | None = None,
        check_mode: bool = False,
    ) -> ExecutionResult:
        """Execute module locally (no bundling).

        Args:
            fqcn: Fully qualified collection name
            params: Module parameters
            timeout: Execution timeout in seconds
            check_mode: Whether to run in check mode

        Returns:
            ExecutionResult with output and status
        """
        return execute_local_fqcn(
            fqcn,
            params,
            timeout=timeout or self.default_timeout,
            check_mode=check_mode,
            playbook_dir=self.playbook_dir,
            extra_paths=self.extra_collection_paths,
        )

    def get_bundle(self, fqcn: str) -> Bundle:
        """Get or build a bundle for a module.

        Args:
            fqcn: Fully qualified collection name

        Returns:
            Bundle for the module
        """
        return self.bundle_cache.get_or_build(
            fqcn,
            playbook_dir=self.playbook_dir,
            extra_paths=self.extra_collection_paths,
        )

    async def execute_remote(
        self,
        host: RemoteHost,
        fqcn: str,
        params: dict[str, Any],
        timeout: int | None = None,
        check_mode: bool = False,
    ) -> ExecutionResult:
        """Execute module on remote host (with bundling).

        Args:
            host: Remote host to execute on
            fqcn: Fully qualified collection name
            params: Module parameters
            timeout: Execution timeout in seconds
            check_mode: Whether to run in check mode

        Returns:
            ExecutionResult with output and status
        """
        bundle = self.get_bundle(fqcn)
        return await execute_remote_with_staging(
            host,
            bundle,
            params,
            timeout=timeout or self.default_timeout,
            check_mode=check_mode,
        )

    def prebuild_bundles(self, fqcns: list[str]) -> dict[str, Bundle]:
        """Pre-build bundles for multiple modules.

        Useful for building all bundles at startup before
        executing tasks.

        Args:
            fqcns: List of FQCNs to build bundles for

        Returns:
            Dictionary mapping FQCN to Bundle
        """
        result = {}
        for fqcn in fqcns:
            try:
                result[fqcn] = self.get_bundle(fqcn)
            except Exception as e:
                logger.error(f"Failed to build bundle for {fqcn}: {e}")
        return result

    async def prestage_bundles(
        self,
        hosts: list[RemoteHost],
        fqcns: list[str],
        bundle_dir: str = "/tmp/ftl2_bundles",
    ) -> dict[str, list[str]]:
        """Pre-stage bundles on multiple remote hosts.

        Args:
            hosts: Remote hosts to stage on
            fqcns: FQCNs of modules to stage
            bundle_dir: Directory on remote hosts for bundles

        Returns:
            Dictionary mapping FQCN to list of staged paths
        """
        result: dict[str, list[str]] = {}

        for fqcn in fqcns:
            bundle = self.get_bundle(fqcn)
            paths = []
            for host in hosts:
                path = await stage_bundle_remote(host, bundle, bundle_dir)
                paths.append(path)
            result[fqcn] = paths

        return result
