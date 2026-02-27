#!/usr/bin/env python3
"""FTL2 Gate runtime entry point for remote execution.

This module serves as the entry point when a gate executable is run
on a remote host. It establishes communication with the main process
via stdin/stdout using the length-prefixed JSON protocol and coordinates
module execution.

Message Protocol:
- 8-byte hex length prefix + JSON body
- Message format: [message_type, message_data]
- Types: Hello, Module, FTLModule, Shutdown, etc.

Module Execution:
Supports multiple module types:
- Binary modules: Executable files with JSON args file
- New-style modules: Python using AnsibleModule class (args via stdin)
- WANT_JSON modules: Python with JSON args file parameter
- Old-style modules: Python with key=value args file
- FTL modules: Native async Python modules with main() function
"""

import asyncio
import base64
import json
import logging
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import time
import traceback
from typing import Any

# Import the gate protocol from parent package
# This will work when the gate is packaged as a .pyz
try:
    from ftl2.message import GateProtocol
except ImportError:
    # Fallback for development/testing
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
    from ftl2.message import GateProtocol

# Try to import ftl_gate for bundled modules (works when packaged as .pyz)
try:
    import ftl_gate  # type: ignore
    HAS_FTL_GATE = True
except ImportError:
    HAS_FTL_GATE = False

logger = logging.getLogger("ftl_gate")


class ModuleNotFoundError(Exception):
    """Raised when a requested module cannot be found in the gate bundle."""

    pass


async def execute_module_stub(
    module_name: str,
    module: str | None = None,
    module_args: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Stub for testing module execution without full gate setup.

    Returns a minimal result dict with the expected structure
    without requiring subprocess execution or gate infrastructure.

    Args:
        module_name: Name of the module
        module: Optional base64-encoded module content (ignored in stub)
        module_args: Arguments that would be passed to the module

    Returns:
        Dict with stdout, stderr, rc, and changed keys
    """
    if module_args is None:
        module_args = {}
    return {
        "stdout": json.dumps({"module": module_name, "args": module_args}),
        "stderr": "",
        "rc": 0,
        "changed": False,
    }


class StdinReader:
    """Fallback async reader for stdin when StreamReader fails."""

    async def read(self, n: int) -> bytes:
        """Read up to n bytes from stdin asynchronously."""
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, sys.stdin.buffer.read, n)
        return result


class StdoutWriter:
    """Fallback async writer for stdout when StreamWriter fails."""

    def write(self, data: bytes) -> None:
        """Write bytes to stdout."""
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()

    async def drain(self) -> None:
        """Drain output buffer (no-op for direct stdout writes)."""
        pass


async def connect_stdin_stdout() -> tuple[Any, Any]:
    """Establish async I/O connections to stdin and stdout."""
    loop = asyncio.get_event_loop()

    try:
        stream_reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(stream_reader)
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)

        w_transport, w_protocol = await loop.connect_write_pipe(
            asyncio.streams.FlowControlMixin,
            sys.stdout,  # type: ignore
        )
        stream_writer = asyncio.StreamWriter(
            w_transport,
            w_protocol,
            stream_reader,
            loop,  # type: ignore
        )

        reader = stream_reader
        writer = stream_writer
        logger.debug("Using native asyncio StreamReader/StreamWriter")

    except ValueError as e:
        logger.debug(f"Falling back to custom reader/writer: {e}")
        reader = StdinReader()
        writer = StdoutWriter()

    return reader, writer


# =============================================================================
# Module Type Detection
# =============================================================================


def is_binary_module(module: bytes) -> bool:
    """Detect if a module is a binary executable rather than a text script."""
    try:
        module.decode()
        return False
    except UnicodeDecodeError:
        return True


def is_ftl_module(module: bytes) -> bool:
    """Detect if a module is an FTL2 module (JSON stdin/stdout, no Ansible deps)."""
    return b"FTL_MODULE" in module


def is_new_style_module(module: bytes) -> bool:
    """Detect if a module uses Ansible's new-style module format (AnsibleModule)."""
    return b"AnsibleModule(" in module


def is_want_json_module(module: bytes) -> bool:
    """Detect if a module expects JSON arguments via file parameter."""
    return b"WANT_JSON" in module


def is_zip_bundle(module: bytes) -> bool:
    """Detect if a module is a ZIP bundle (built by build_bundle_from_fqcn).

    ZIP files start with the magic bytes PK\\x03\\x04.
    These bundles contain __main__.py and are executed as `python bundle.zip`.
    """
    return module[:4] == b"PK\x03\x04"


def detect_module_type(module_bytes: bytes) -> str:
    """Detect the type of a module from its content.

    Returns:
        One of: "zip_bundle", "binary", "ftl", "new_style", "want_json", "old_style"
    """
    if is_zip_bundle(module_bytes):
        return "zip_bundle"
    if is_binary_module(module_bytes):
        return "binary"
    if is_ftl_module(module_bytes):
        return "ftl"
    if is_new_style_module(module_bytes):
        return "new_style"
    if is_want_json_module(module_bytes):
        return "want_json"
    return "old_style"


def list_gate_modules() -> list[dict[str, str]]:
    """List all modules bundled in the gate.

    Returns:
        List of dicts with 'name' and 'type' for each module.
    """
    modules = []

    if not HAS_FTL_GATE:
        return modules

    import importlib.resources

    try:
        gate_files = importlib.resources.files(ftl_gate)
        for item in gate_files.iterdir():
            name = item.name
            # Skip __init__.py and __pycache__
            if name.startswith("__"):
                continue
            try:
                content = item.read_bytes()
                module_type = detect_module_type(content)
                modules.append({"name": name, "type": module_type})
            except Exception:
                modules.append({"name": name, "type": "unknown"})
    except Exception:
        pass

    return modules


def get_python_path() -> str:
    """Get the current Python path for subprocess environment setup."""
    return os.pathsep.join(sys.path)


# =============================================================================
# Command Execution
# =============================================================================


async def check_output(
    cmd: str,
    env: dict[str, str] | None = None,
    stdin: bytes | None = None,
) -> tuple[bytes, bytes, int]:
    """Execute a shell command asynchronously and capture its output.

    Args:
        cmd: Shell command string to execute
        env: Optional environment variables for the subprocess
        stdin: Optional bytes data to send to process stdin

    Returns:
        Tuple of (stdout, stderr, returncode) as (bytes, bytes, int)
    """
    logger.debug(f"check_output: {cmd}")
    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )

    stdout, stderr = await proc.communicate(stdin)
    logger.debug(f"check_output complete: rc={proc.returncode}")
    return stdout, stderr, proc.returncode


# =============================================================================
# Module Execution
# =============================================================================


async def execute_module(
    protocol: GateProtocol,
    writer: Any,
    module_name: str,
    module: str | None = None,
    module_args: dict[str, Any] | None = None,
) -> None:
    """Execute an automation module within the FTL gate environment.

    Handles running modules in various formats:
    - Binary: Execute directly with JSON args file
    - New-style: Python with AnsibleModule - args via stdin
    - WANT_JSON: Python with JSON args file parameter
    - Old-style: Python with key=value args file

    Args:
        protocol: Gate protocol for sending responses
        writer: Output writer for sending results
        module_name: Name of the module to execute
        module: Optional base64-encoded module content
        module_args: Arguments to pass to the module
    """
    logger.info(f"Executing module: {module_name}")
    tempdir = tempfile.mkdtemp(prefix="ftl-module-")

    try:
        module_file = os.path.join(tempdir, f"ftl_{module_name}")
        env = os.environ.copy()
        env["PYTHONPATH"] = get_python_path()

        # Load module content
        if module is not None:
            logger.info("Loading module from message")
            module_bytes = base64.b64decode(module)
            with open(module_file, "wb") as f:
                f.write(module_bytes)
        elif HAS_FTL_GATE:
            logger.info("Loading module from ftl_gate bundle")
            try:
                import importlib.resources
                gate_files = importlib.resources.files(ftl_gate)
                # Try exact name first, then with .py extension
                for candidate in (module_name, f"{module_name}.py"):
                    try:
                        module_bytes = gate_files.joinpath(candidate).read_bytes()
                        break
                    except FileNotFoundError:
                        continue
                else:
                    raise FileNotFoundError(module_name)
                with open(module_file, "wb") as f:
                    f.write(module_bytes)
            except FileNotFoundError:
                logger.info(f"Module {module_name} not found in gate bundle")
                raise ModuleNotFoundError(module_name)
        else:
            logger.info(f"Module {module_name} not found (no bundle available)")
            raise ModuleNotFoundError(module_name)

        # Detect module type and execute appropriately
        if is_zip_bundle(module_bytes):
            # ZIP bundle (from build_bundle_from_fqcn) - execute as python bundle.zip
            logger.info("Detected ZIP bundle")
            bundle_file = os.path.join(tempdir, f"{module_name}.zip")
            with open(bundle_file, "wb") as f:
                f.write(module_bytes)
            # Bundles expect JSON args on stdin (like new-style modules)
            stdin_data = json.dumps({"ANSIBLE_MODULE_ARGS": module_args or {}}).encode()
            stdout, stderr, rc = await check_output(
                f"{sys.executable} {bundle_file}",
                stdin=stdin_data,
                env=env,
            )

        elif is_binary_module(module_bytes):
            logger.info("Detected binary module")
            args_file = os.path.join(tempdir, "args")
            with open(args_file, "w") as f:
                json.dump(module_args or {}, f)
            os.chmod(module_file, stat.S_IEXEC | stat.S_IREAD)
            stdout, stderr, rc = await check_output(f"{module_file} {args_file}")

        elif is_ftl_module(module_bytes):
            # FTL modules: raw JSON args on stdin, JSON result on stdout
            logger.info("Detected FTL module")
            stdin_data = json.dumps(module_args or {}).encode()
            stdout, stderr, rc = await check_output(
                f"{sys.executable} {module_file}",
                stdin=stdin_data,
                env=env,
            )

        elif is_new_style_module(module_bytes):
            logger.info("Detected new-style module (AnsibleModule)")
            stdin_data = json.dumps({"ANSIBLE_MODULE_ARGS": module_args or {}}).encode()
            stdout, stderr, rc = await check_output(
                f"{sys.executable} {module_file}",
                stdin=stdin_data,
                env=env,
            )

        elif is_want_json_module(module_bytes):
            logger.info("Detected WANT_JSON module")
            args_file = os.path.join(tempdir, "args")
            with open(args_file, "w") as f:
                json.dump(module_args or {}, f)
            stdout, stderr, rc = await check_output(
                f"{sys.executable} {module_file} {args_file}",
                env=env,
            )

        else:
            logger.info("Detected old-style module (key=value)")
            args_file = os.path.join(tempdir, "args")
            with open(args_file, "w") as f:
                if module_args:
                    f.write(" ".join(f"{k}={v}" for k, v in module_args.items()))
                else:
                    f.write("")
            stdout, stderr, rc = await check_output(
                f"{sys.executable} {module_file} {args_file}",
                env=env,
            )

        # Parse module JSON output to extract structured result fields
        stdout_str = stdout.decode(errors="replace")
        stderr_str = stderr.decode(errors="replace")
        result = {
            "stdout": stdout_str,
            "stderr": stderr_str,
            "rc": rc,
        }
        # Ansible modules write JSON to stdout; extract fields like
        # changed, failed, msg, rc so the runner can handle them properly
        try:
            module_output = json.loads(stdout_str)
            if isinstance(module_output, dict):
                result.update(module_output)
        except (json.JSONDecodeError, ValueError):
            pass

        # Send result
        logger.info("Sending ModuleResult")
        await protocol.send_message(
            writer,
            "ModuleResult",
            result,
        )

    finally:
        logger.info(f"Cleaning up {tempdir}")
        shutil.rmtree(tempdir, ignore_errors=True)


async def execute_ftl_module(
    protocol: GateProtocol,
    writer: Any,
    module_name: str,
    module: str,
    module_args: dict[str, Any] | None = None,
) -> None:
    """Execute an FTL-native module with async main() function.

    FTL modules are Python modules with an async main() function that
    can be executed directly without subprocess overhead.

    Args:
        protocol: Gate protocol for sending responses
        writer: Output writer for sending results
        module_name: Name identifier for the module
        module: Base64-encoded Python source code, or empty for baked-in lookup
        module_args: Arguments available to the module (passed to main)
    """
    logger.info(f"Executing FTL module: {module_name}")

    try:
        # Load module source — from message or baked-in
        if module:
            module_source = base64.b64decode(module)
        else:
            # Try baked-in FTL module lookup
            try:
                import importlib.resources
                baked = importlib.resources.files("ftl_modules_baked")
                module_source = baked.joinpath(f"{module_name}.py").read_bytes()
                logger.info(f"Loaded FTL module {module_name} from baked-in ftl_modules_baked/")
            except (ImportError, FileNotFoundError, TypeError):
                logger.info(f"FTL module {module_name} not found in gate")
                await protocol.send_message(
                    writer, "ModuleNotFound", {"module_name": module_name}
                )
                return

        module_compiled = compile(module_source, module_name, "exec")

        # Execute module in isolated namespace
        # Use single dict for globals and locals to avoid exec() pitfall where
        # module-level imports go into locals but function bodies look up in globals
        # Use module_name (not __main__) to avoid triggering if __name__ == "__main__" blocks
        namespace: dict[str, Any] = {
            "__file__": module_name,
            "__name__": f"ftl_module_{module_name}",
        }

        exec(module_compiled, namespace)

        # Find and call entry point
        # Look for: main(), ftl_{module_name}(), or the first callable
        func_name = f"ftl_{module_name}"
        if "main" in namespace:
            main_func = namespace["main"]
        elif func_name in namespace:
            main_func = namespace[func_name]
        else:
            raise RuntimeError(f"Module {module_name} has no main() or {func_name}() function")

        # Call the module function
        logger.info(f"Calling FTL module {main_func.__name__}()")
        args = module_args or {}

        # Determine calling convention: main() gets dict arg, ftl_* gets kwargs
        import inspect
        sig = inspect.signature(main_func)
        use_kwargs = len(sig.parameters) > 1 or (
            len(sig.parameters) == 1
            and list(sig.parameters.values())[0].kind != inspect.Parameter.VAR_POSITIONAL
            and list(sig.parameters.values())[0].annotation != dict
            and main_func.__name__ != "main"
        )

        if asyncio.iscoroutinefunction(main_func):
            if not sig.parameters:
                result = await main_func()
            elif use_kwargs:
                result = await main_func(**args)
            else:
                result = await main_func(args)
        else:
            if not sig.parameters:
                result = main_func()
            elif use_kwargs:
                result = main_func(**args)
            else:
                result = main_func(args)

        # Send result
        logger.info("Sending FTLModuleResult")
        await protocol.send_message(
            writer,
            "FTLModuleResult",
            {"result": result},
        )

    except Exception as e:
        logger.exception(f"FTL module execution failed: {e}")
        await protocol.send_message(
            writer,
            "Error",
            {
                "message": f"FTL module execution failed: {e}",
                "traceback": traceback.format_exc(),
            },
        )


# =============================================================================
# =============================================================================
# File Watcher
# =============================================================================


class FileWatcher:
    """Watches files for changes using Linux inotify and emits FileChanged events.

    Runs an asyncio background task that monitors inotify file descriptors
    and writes FileChanged messages to the gate's stdout. The inotify
    library is loaded lazily on first watch to avoid import errors on
    non-Linux systems.
    """

    # Map inotify flag bits to human-readable event names
    _FLAG_NAMES = {
        0x00000002: "modified",
        0x00000004: "attrib",
        0x00000008: "close_write",
        0x00000040: "moved_from",
        0x00000080: "moved_to",
        0x00000100: "created",
        0x00000200: "deleted",
        0x00000400: "delete_self",
        0x00000800: "move_self",
        0x00008000: "ignored",
    }

    def __init__(self, protocol, writer):
        self._protocol = protocol
        self._writer = writer
        self._inotify = None
        self._watches = {}  # wd -> path
        self._task = None

    def add_watch(self, path: str) -> None:
        """Add a file watch. Starts the background event loop on first call."""
        if self._inotify is None:
            from inotify_simple import INotify, flags as iflags

            self._inotify = INotify(nonblocking=True)
            self._task = asyncio.create_task(self._event_loop())
            self._iflags = iflags

        watch_mask = (
            self._iflags.MODIFY
            | self._iflags.ATTRIB
            | self._iflags.CLOSE_WRITE
            | self._iflags.MOVED_FROM
            | self._iflags.MOVED_TO
            | self._iflags.CREATE
            | self._iflags.DELETE
            | self._iflags.DELETE_SELF
            | self._iflags.MOVE_SELF
        )
        wd = self._inotify.add_watch(path, watch_mask)
        self._watches[wd] = path
        logger.info(f"Watching {path} (wd={wd})")

    def remove_watch(self, path: str) -> bool:
        """Remove a file watch by path. Returns True if found."""
        for wd, watched_path in list(self._watches.items()):
            if watched_path == path:
                try:
                    self._inotify.rm_watch(wd)
                except OSError:
                    pass  # Already removed by kernel
                del self._watches[wd]
                logger.info(f"Unwatched {path} (wd={wd})")
                return True
        return False

    async def _event_loop(self) -> None:
        """Background task that reads inotify events and emits FileChanged messages."""
        loop = asyncio.get_event_loop()
        fd = self._inotify.fileno()

        try:
            while True:
                # Wait for the inotify fd to become readable
                readable = asyncio.Event()
                loop.add_reader(fd, readable.set)
                try:
                    await readable.wait()
                finally:
                    loop.remove_reader(fd)

                # Read all pending events
                for event in self._inotify.read(timeout=0):
                    path = self._watches.get(event.wd)
                    if path is None:
                        continue

                    event_name = self._mask_to_name(event.mask)

                    # Handle watch removal by kernel (file deleted, fs unmounted)
                    if event.mask & 0x00008000:  # IGNORED
                        self._watches.pop(event.wd, None)
                        logger.info(f"Watch removed by kernel for {path}")

                    try:
                        await self._protocol.send_message(
                            self._writer,
                            "FileChanged",
                            {
                                "path": path,
                                "event": event_name,
                                "name": event.name,
                            },
                        )
                    except BrokenPipeError:
                        return
        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.error(f"FileWatcher error: {e}")

    def _mask_to_name(self, mask: int) -> str:
        """Convert inotify event mask to a human-readable name."""
        for flag_val, name in self._FLAG_NAMES.items():
            if mask & flag_val:
                return name
        return f"unknown(0x{mask:x})"

    def stop(self) -> None:
        """Cancel the background task and close inotify."""
        if self._task is not None:
            self._task.cancel()
            self._task = None
        if self._inotify is not None:
            try:
                self._inotify.close()
            except Exception:
                pass
            self._inotify = None
        self._watches.clear()


# =============================================================================
# System Monitor
# =============================================================================


class SystemMonitor:
    """Streams system metrics (CPU, memory, disk, network, processes) via events.

    Uses psutil (must be installed on the remote host as a system package)
    to sample metrics at a configurable interval. Metrics are sent as
    SystemMetrics messages on the gate's stdout channel.
    """

    def __init__(self, protocol, writer):
        self._protocol = protocol
        self._writer = writer
        self._task = None
        self._interval = 2.0
        self._include_processes = True
        self._psutil = None
        self._prev_net = None
        self._prev_time = None

    def start(self, interval: float = 2.0, include_processes: bool = True) -> None:
        """Start the monitoring loop. Lazy-imports psutil."""
        if self._task is not None:
            return  # Already running
        import psutil

        self._psutil = psutil
        self._interval = interval
        self._include_processes = include_processes
        self._prev_net = psutil.net_io_counters()
        self._prev_time = time.time()
        self._task = asyncio.create_task(self._monitor_loop())
        logger.info(f"System monitor started (interval={interval}s)")

    async def _monitor_loop(self) -> None:
        """Background task that samples metrics and emits SystemMetrics events."""
        try:
            while True:
                await asyncio.sleep(self._interval)
                metrics = self._collect_metrics()
                try:
                    await self._protocol.send_message(
                        self._writer, "SystemMetrics", metrics
                    )
                except BrokenPipeError:
                    return
        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.error(f"SystemMonitor error: {e}")

    def _collect_metrics(self) -> dict:
        """Sample all system metrics via psutil."""
        psutil = self._psutil
        now = time.time()

        # CPU
        cpu_per = psutil.cpu_percent(interval=0, percpu=True)
        cpu_total = sum(cpu_per) / len(cpu_per) if cpu_per else 0.0
        load_avg = list(os.getloadavg())

        # Memory
        mem = psutil.virtual_memory()

        # Swap
        swap = psutil.swap_memory()

        # Disk
        disk = psutil.disk_usage("/")

        # Network with rate calculation
        net = psutil.net_io_counters()
        elapsed = now - self._prev_time if self._prev_time else self._interval
        if elapsed <= 0:
            elapsed = self._interval
        net_data = {
            "bytes_sent": net.bytes_sent,
            "bytes_recv": net.bytes_recv,
            "bytes_sent_rate": int(
                (net.bytes_sent - self._prev_net.bytes_sent) / elapsed
            )
            if self._prev_net
            else 0,
            "bytes_recv_rate": int(
                (net.bytes_recv - self._prev_net.bytes_recv) / elapsed
            )
            if self._prev_net
            else 0,
        }
        self._prev_net = net
        self._prev_time = now

        # Uptime
        uptime = now - psutil.boot_time()

        metrics = {
            "timestamp": now,
            "hostname": os.uname().nodename,
            "cpu": {
                "percent_per_cpu": cpu_per,
                "percent_total": round(cpu_total, 1),
                "count": psutil.cpu_count(),
                "load_avg": load_avg,
            },
            "memory": {
                "total": mem.total,
                "used": mem.used,
                "available": mem.available,
                "percent": mem.percent,
            },
            "swap": {
                "total": swap.total,
                "used": swap.used,
                "percent": swap.percent,
            },
            "disk": {
                "total": disk.total,
                "used": disk.used,
                "free": disk.free,
                "percent": disk.percent,
            },
            "net": net_data,
            "uptime": int(uptime),
        }

        # Processes (optional, top 20 by CPU)
        if self._include_processes:
            procs = []
            for p in psutil.process_iter(
                ["pid", "name", "cpu_percent", "memory_info", "status", "username"]
            ):
                try:
                    info = p.info
                    procs.append(
                        {
                            "pid": info["pid"],
                            "name": info["name"],
                            "cpu_percent": info["cpu_percent"] or 0.0,
                            "memory_rss": info["memory_info"].rss
                            if info["memory_info"]
                            else 0,
                            "status": info["status"],
                            "username": info["username"] or "",
                        }
                    )
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            procs.sort(key=lambda p: p["cpu_percent"], reverse=True)
            metrics["processes"] = procs[:20]

        return metrics

    def stop(self) -> None:
        """Cancel the background task."""
        if self._task is not None:
            self._task.cancel()
            self._task = None
            logger.info("System monitor stopped")


# =============================================================================
# Main Entry Point
# =============================================================================


async def main(args: list[str]) -> int | None:
    """Main entry point for the FTL2 gate process.

    Initializes logging, establishes communication, and enters the
    message processing loop.

    Args:
        args: Command-line arguments (currently unused)

    Returns:
        Exit code: None for normal shutdown, 1 for error
    """
    # Set up logging
    gate_log = Path.home() / ".ftl" / "gate.log"
    gate_log.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        format="%(asctime)s - %(message)s",
        filename=str(gate_log),
        level=logging.DEBUG,
    )

    logger.info("=" * 60)
    logger.info("FTL2 Gate starting")
    logger.info(f"Python: {sys.executable}")
    logger.info(f"Version: {sys.version}")
    logger.info(f"Path: {sys.path[:3]}...")
    logger.info("=" * 60)

    # Compute gate file hash for version checking
    gate_hash = ""
    try:
        import hashlib
        gate_file = sys.argv[0] if sys.argv else ""
        if gate_file and os.path.exists(gate_file):
            gate_hash = hashlib.sha256(open(gate_file, "rb").read()).hexdigest()[:16]
            logger.info(f"Gate hash: {gate_hash}")
    except Exception:
        pass

    # Connect to stdin/stdout
    try:
        reader, writer = await connect_stdin_stdout()
        logger.info("Connected to stdin/stdout")
    except Exception as e:
        logger.error(f"Failed to connect stdin/stdout: {e}")
        return 1

    # Initialize protocol
    protocol = GateProtocol()

    # Initialize file watcher and system monitor (events are emitted concurrently)
    watcher = FileWatcher(protocol, writer)
    monitor = SystemMonitor(protocol, writer)

    # Message processing loop
    while True:
        try:
            # Read message
            msg = await protocol.read_message(reader)

            if msg is None:
                logger.info("EOF received, shutting down")
                watcher.stop()
                monitor.stop()
                try:
                    await protocol.send_message(writer, "Goodbye", {})
                except Exception:
                    pass
                return None

            msg_type, data = msg
            logger.debug(f"Received message: {msg_type}")

            # Handle message by type
            if msg_type == "Hello":
                logger.info("Hello received")
                response_data = dict(data) if isinstance(data, dict) else {}
                response_data["gate_hash"] = gate_hash
                await protocol.send_message(writer, "Hello", response_data)

            elif msg_type == "Module":
                logger.info(f"Module execution requested: {data.get('module_name', 'unknown')}")

                if not isinstance(data, dict):
                    await protocol.send_message(
                        writer, "Error", {"message": "Invalid Module data"}
                    )
                    continue

                try:
                    await execute_module(
                        protocol,
                        writer,
                        data.get("module_name", ""),
                        data.get("module"),
                        data.get("module_args", {}),
                    )

                except ModuleNotFoundError as e:
                    await protocol.send_message(
                        writer,
                        "ModuleNotFound",
                        {"message": f"Module not found: {e}"},
                    )

                except Exception as e:
                    logger.exception("Module execution failed")
                    await protocol.send_message(
                        writer,
                        "Error",
                        {
                            "message": f"Module execution failed: {e}",
                            "traceback": traceback.format_exc(),
                        },
                    )

            elif msg_type == "FTLModule":
                logger.info(f"FTLModule execution requested: {data.get('module_name', 'unknown')}")

                if not isinstance(data, dict):
                    await protocol.send_message(
                        writer, "Error", {"message": "Invalid FTLModule data"}
                    )
                    continue

                await execute_ftl_module(
                    protocol,
                    writer,
                    data.get("module_name", ""),
                    data.get("module", ""),
                    data.get("module_args", {}),
                )

            elif msg_type == "Info":
                logger.info("Info requested")
                await protocol.send_message(
                    writer,
                    "InfoResult",
                    {
                        "python_version": sys.version,
                        "python_executable": sys.executable,
                        "gate_location": os.path.abspath(sys.argv[0]) if sys.argv else "",
                        "platform": sys.platform,
                        "pid": os.getpid(),
                        "cwd": os.getcwd(),
                    },
                )

            elif msg_type == "ListModules":
                logger.info("ListModules requested")
                modules = list_gate_modules()
                await protocol.send_message(
                    writer, "ListModulesResult", {"modules": modules}
                )

            elif msg_type == "Watch":
                path = data.get("path", "") if isinstance(data, dict) else ""
                logger.info(f"Watch requested: {path}")
                try:
                    watcher.add_watch(path)
                    await protocol.send_message(
                        writer, "WatchResult", {"path": path, "status": "ok"}
                    )
                except ImportError:
                    await protocol.send_message(
                        writer,
                        "WatchResult",
                        {
                            "path": path,
                            "status": "error",
                            "message": "inotify not available (Linux only)",
                        },
                    )
                except Exception as e:
                    await protocol.send_message(
                        writer,
                        "WatchResult",
                        {"path": path, "status": "error", "message": str(e)},
                    )

            elif msg_type == "Unwatch":
                path = data.get("path", "") if isinstance(data, dict) else ""
                logger.info(f"Unwatch requested: {path}")
                found = watcher.remove_watch(path)
                await protocol.send_message(
                    writer,
                    "UnwatchResult",
                    {"path": path, "removed": found},
                )

            elif msg_type == "StartMonitor":
                interval = data.get("interval", 2.0) if isinstance(data, dict) else 2.0
                include_procs = data.get("include_processes", True) if isinstance(data, dict) else True
                logger.info(f"StartMonitor requested (interval={interval}s)")
                try:
                    monitor.start(interval=interval, include_processes=include_procs)
                    await protocol.send_message(
                        writer, "MonitorResult", {"status": "ok"}
                    )
                except ImportError:
                    await protocol.send_message(
                        writer,
                        "MonitorResult",
                        {
                            "status": "error",
                            "message": "psutil not available — install python3-psutil on this host",
                        },
                    )
                except Exception as e:
                    await protocol.send_message(
                        writer,
                        "MonitorResult",
                        {"status": "error", "message": str(e)},
                    )

            elif msg_type == "StopMonitor":
                logger.info("StopMonitor requested")
                monitor.stop()
                await protocol.send_message(
                    writer, "MonitorResult", {"status": "stopped"}
                )

            elif msg_type == "Shutdown":
                logger.info("Shutdown requested")
                watcher.stop()
                monitor.stop()
                await protocol.send_message(writer, "Goodbye", {})
                return None

            else:
                logger.warning(f"Unknown message type: {msg_type}")
                await protocol.send_message(
                    writer, "Error", {"message": f"Unknown message type: {msg_type}"}
                )

        except ModuleNotFoundError as e:
            logger.warning(f"Module not found: {e}")
            try:
                await protocol.send_message(
                    writer, "ModuleNotFound", {"message": str(e)}
                )
            except Exception:
                pass

        except Exception as e:
            logger.error(f"Gate system error: {e}")
            logger.error(traceback.format_exc())

            try:
                await protocol.send_message(
                    writer,
                    "GateSystemError",
                    {
                        "message": f"System error: {e}",
                        "traceback": traceback.format_exc(),
                    },
                )
            except Exception:
                pass

            return 1


if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main(sys.argv[1:]))
        sys.exit(exit_code or 0)
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(130)
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        logger.error(traceback.format_exc())
        sys.exit(1)
