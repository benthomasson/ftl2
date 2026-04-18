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

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import shutil
import stat
import sys
import tempfile
import time
import traceback
from pathlib import Path
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

# Try to import policy engine (bundled into gate .pyz for gate-side enforcement)
try:
    from ftl2.policy import Policy
    HAS_POLICY = True
except ImportError:
    HAS_POLICY = False

logger = logging.getLogger("ftl_gate")


def _check_gate_policy(
    policy: Policy | None,
    module_name: str,
    module_args: dict[str, Any],
    environment: str = "",
    host: str = "localhost",
) -> tuple[bool, dict[str, Any] | None]:
    """Evaluate gate-side policy before module execution.

    Args:
        policy: Active policy instance, or None if no policy is set.
        module_name: Name of the module to execute.
        module_args: Module arguments dict.
        environment: Environment label for environment-scoped rules.
        host: Logical host name for host-scoped rules.

    Returns:
        Tuple of (permitted, denial_data). If permitted is False,
        denial_data contains the structured PolicyDenied response payload.
    """
    if policy is None or not policy.rules:
        return True, None
    result = policy.evaluate(module_name, module_args, host=host, environment=environment)
    if result.permitted:
        return True, None
    return False, {
        "module": module_name,
        "reason": result.reason,
        "rule": result.rule.to_dict() if result.rule else None,
    }


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

# Cache modules after first transfer so subsequent name-only requests
# for the same module succeed without a ModuleNotFound round trip.
# Use an LRU cache to bound memory in long-lived gate processes (GH-13).
_MODULE_CACHE_MAX_SIZE = 128
_module_cache: dict[str, bytes] = {}

# Gate-level state for GateStatus self-reporting
_error_count: int = 0
_last_error: str | None = None
_start_time: float = 0.0
_active_tasks: set | None = None
_draining: bool = False


def _module_cache_set(name: str, data: bytes) -> None:
    """Store a module in the bounded cache, evicting the oldest entry if full."""
    if name in _module_cache:
        # Delete and re-insert to move to end (most recently used)
        del _module_cache[name]
    elif len(_module_cache) >= _MODULE_CACHE_MAX_SIZE:
        # Evict oldest entry (first key in insertion-ordered dict)
        oldest = next(iter(_module_cache))
        del _module_cache[oldest]
    _module_cache[name] = data


async def run_module(
    module_name: str,
    module: str | None = None,
    module_args: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute an automation module and return the result dict.

    Raises ModuleNotFoundError if module cannot be located.
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
            _module_cache_set(module_name, module_bytes)
            with open(module_file, "wb") as f:
                f.write(module_bytes)
        elif module_name in _module_cache:
            logger.info("Loading module from cache")
            module_bytes = _module_cache[module_name]
            # Touch LRU order: move to end so frequently used modules aren't evicted
            del _module_cache[module_name]
            _module_cache[module_name] = module_bytes
            with open(module_file, "wb") as f:
                f.write(module_bytes)
        elif HAS_FTL_GATE:
            logger.info("Loading module from ftl_gate bundle")
            try:
                import importlib.resources
                gate_files = importlib.resources.files(ftl_gate)
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
                raise ModuleNotFoundError(module_name) from None
        else:
            logger.info(f"Module {module_name} not found (no bundle available)")
            raise ModuleNotFoundError(module_name)

        # Detect module type and execute appropriately
        if is_zip_bundle(module_bytes):
            logger.info("Detected ZIP bundle")
            bundle_file = os.path.join(tempdir, f"{module_name}.zip")
            with open(bundle_file, "wb") as f:
                f.write(module_bytes)
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

        # Parse module JSON output
        stdout_str = stdout.decode(errors="replace")
        stderr_str = stderr.decode(errors="replace")
        result = {
            "stdout": stdout_str,
            "stderr": stderr_str,
            "rc": rc,
        }
        try:
            module_output = json.loads(stdout_str)
            if isinstance(module_output, dict):
                result.update(module_output)
                result["rc"] = rc  # subprocess exit code takes precedence
        except (json.JSONDecodeError, ValueError):
            pass

        return result

    finally:
        logger.info(f"Cleaning up {tempdir}")
        shutil.rmtree(tempdir, ignore_errors=True)


async def execute_module(
    protocol: GateProtocol,
    writer: Any,
    module_name: str,
    module: str | None = None,
    module_args: dict[str, Any] | None = None,
) -> None:
    """Execute module and send result via protocol (serial mode wrapper)."""
    result = await run_module(module_name, module, module_args)
    logger.info("Sending ModuleResult")
    await protocol.send_message(writer, "ModuleResult", result)


async def run_ftl_module(
    module_name: str,
    module: str,
    module_args: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Execute an FTL-native module and return (response_type, response_data).

    Returns ("FTLModuleResult", {...}), ("ModuleNotFound", {...}), or ("Error", {...}).
    """
    logger.info(f"Executing FTL module: {module_name}")

    try:
        # Load module source — from message or baked-in
        if module:
            module_source = base64.b64decode(module)
        else:
            try:
                import importlib.resources
                baked = importlib.resources.files("ftl_modules_baked")
                module_source = baked.joinpath(f"{module_name}.py").read_bytes()
                logger.info(f"Loaded FTL module {module_name} from baked-in ftl_modules_baked/")
            except (ImportError, FileNotFoundError, TypeError):
                logger.info(f"FTL module {module_name} not found in gate")
                return ("ModuleNotFound", {"module_name": module_name})

        module_compiled = compile(module_source, module_name, "exec")

        namespace: dict[str, Any] = {
            "__file__": module_name,
            "__name__": f"ftl_module_{module_name}",
        }

        exec(module_compiled, namespace)

        func_name = f"ftl_{module_name}"
        if "main" in namespace:
            main_func = namespace["main"]
        elif func_name in namespace:
            main_func = namespace[func_name]
        else:
            raise RuntimeError(f"Module {module_name} has no main() or {func_name}() function")

        logger.info(f"Calling FTL module {main_func.__name__}()")
        args = module_args or {}

        import inspect
        sig = inspect.signature(main_func)
        use_kwargs = len(sig.parameters) > 1 or (
            len(sig.parameters) == 1
            and list(sig.parameters.values())[0].kind != inspect.Parameter.VAR_POSITIONAL
            and list(sig.parameters.values())[0].annotation is not dict
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

        return ("FTLModuleResult", {"result": result})

    except Exception as e:
        logger.exception(f"FTL module execution failed: {e}")
        return ("Error", {
            "message": f"FTL module execution failed: {e}",
            "traceback": traceback.format_exc(),
        })


async def execute_ftl_module(
    protocol: GateProtocol,
    writer: Any,
    module_name: str,
    module: str,
    module_args: dict[str, Any] | None = None,
) -> None:
    """Execute FTL module and send result via protocol (serial mode wrapper)."""
    resp_type, resp_data = await run_ftl_module(module_name, module, module_args)
    await protocol.send_message(writer, resp_type, resp_data)


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
        self._write_lock = None  # Set by main_multiplexed() for stdout serialization

    def add_watch(self, path: str) -> None:
        """Add a file watch. Starts the background event loop on first call."""
        if self._inotify is None:
            from inotify_simple import INotify
            from inotify_simple import flags as iflags

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
                        if self._write_lock:
                            async with self._write_lock:
                                await self._protocol.send_message(
                                    self._writer,
                                    "FileChanged",
                                    {
                                        "path": path,
                                        "event": event_name,
                                        "name": event.name,
                                    },
                                )
                        else:
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
        self._write_lock = None  # Set by main_multiplexed() for stdout serialization

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
                    if self._write_lock:
                        async with self._write_lock:
                            await self._protocol.send_message(
                                self._writer, "SystemMetrics", metrics
                            )
                    else:
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
# Gate Status Monitor
# =============================================================================


class GateStatusMonitor:
    """Streams gate health metrics via GateStatus events.

    Unlike SystemMonitor (which requires psutil for host metrics),
    this uses only stdlib to report the gate's own state:
    uptime, error count, active tasks, module cache size, and
    memory RSS via ``resource.getrusage()``.

    CPU percent is optional — uses psutil if already available
    (e.g. when SystemMonitor is also running), omits the field otherwise.

    Args:
        protocol: GateProtocol instance for sending messages
        writer: Async stream writer for stdout
        gate_hash: SHA256 prefix identifying the gate binary

    Example:
        monitor = GateStatusMonitor(protocol, writer, gate_hash)
        monitor.start(interval=5.0)
        # ... GateStatus events are pushed every 5 seconds ...
        monitor.stop()
    """

    def __init__(self, protocol: GateProtocol, writer: Any, gate_hash: str):
        self._protocol = protocol
        self._writer = writer
        self._gate_hash = gate_hash
        self._task: asyncio.Task | None = None
        self._interval = 5.0
        self._write_lock: asyncio.Lock | None = None

    def start(self, interval: float = 5.0) -> None:
        """Start the gate status monitoring loop.

        Args:
            interval: Seconds between status reports (default 5.0)
        """
        if self._task is not None:
            return
        self._interval = interval
        self._task = asyncio.create_task(self._status_loop())
        logger.info(f"Gate status monitor started (interval={interval}s)")

    async def _status_loop(self) -> None:
        """Background task that collects and sends gate status."""
        try:
            while True:
                await asyncio.sleep(self._interval)
                status = self._collect_status()
                try:
                    if self._write_lock:
                        async with self._write_lock:
                            await self._protocol.send_message(
                                self._writer, "GateStatus", status
                            )
                    else:
                        await self._protocol.send_message(
                            self._writer, "GateStatus", status
                        )
                except BrokenPipeError:
                    return
        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.error(f"GateStatusMonitor error: {e}")

    def _collect_status(self) -> dict:
        """Collect gate-introspective metrics using stdlib."""
        import resource as _resource

        hostname = os.uname().nodename

        # RSS from stdlib (no psutil needed)
        rusage = _resource.getrusage(_resource.RUSAGE_SELF)
        # ru_maxrss is KB on Linux, bytes on macOS
        rss = rusage.ru_maxrss
        if sys.platform == "darwin":
            memory_rss = rss  # already bytes
        else:
            memory_rss = rss * 1024  # KB -> bytes

        # CPU percent: try psutil for own process, omit if unavailable
        cpu_percent = None
        try:
            import psutil
            cpu_percent = psutil.Process(os.getpid()).cpu_percent(interval=0)
        except Exception:
            pass

        # Active tasks from multiplexed mode
        active = len(_active_tasks) if _active_tasks is not None else 0

        status: dict[str, Any] = {
            "gate_id": f"{hostname}-{os.getpid()}",
            "host": hostname,
            "version": "0.1.0",
            "gate_hash": self._gate_hash,
            "uptime_seconds": round(time.time() - _start_time, 1),
            "state": "executing" if active > 0 else "idle",
            "active_tasks": active,
            "queue_depth": 0,
            "error_count": _error_count,
            "last_error": _last_error,
            "module_cache_size": len(_module_cache),
            "module_cache_bytes": sum(len(v) for v in _module_cache.values()),
            "memory_rss": memory_rss,
            "pid": os.getpid(),
        }
        if cpu_percent is not None:
            status["cpu_percent"] = cpu_percent

        return status

    def stop(self) -> None:
        """Cancel the background task."""
        if self._task is not None:
            self._task.cancel()
            self._task = None
            logger.info("Gate status monitor stopped")


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

    # Record gate start time for uptime tracking
    global _start_time
    _start_time = time.time()

    # Compute gate file hash for version checking
    gate_hash = ""
    try:
        import hashlib
        gate_file = sys.argv[0] if sys.argv else ""
        if gate_file and os.path.exists(gate_file):
            with open(gate_file, "rb") as f:
                gate_hash = hashlib.sha256(f.read()).hexdigest()[:16]
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

    # Initialize file watcher, system monitor, and gate status monitor
    watcher = FileWatcher(protocol, writer)
    monitor = SystemMonitor(protocol, writer)
    gate_status_monitor = GateStatusMonitor(protocol, writer, gate_hash)

    # Gate-side policy enforcement state
    gate_policy: Policy | None = None
    gate_environment: str = ""
    gate_host: str = "localhost"

    global _draining
    _draining = False

    # Message processing loop
    while True:
        try:
            # Read message
            msg = await protocol.read_message(reader)

            if msg is None:
                logger.info("EOF received, shutting down")
                watcher.stop()
                monitor.stop()
                gate_status_monitor.stop()
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

                # Extract policy rules and host from Hello data
                if isinstance(data, dict) and HAS_POLICY:
                    policy_rules = data.get("policy_rules")
                    if policy_rules:
                        gate_policy = Policy.from_wire(policy_rules)
                        gate_environment = data.get("environment", "")
                        gate_host = data.get("host", "localhost")
                        logger.info(f"Policy loaded: {len(gate_policy.rules)} rules, environment={gate_environment!r}, host={gate_host!r}")

                # Check for multiplexing capability
                capabilities = data.get("capabilities", []) if isinstance(data, dict) else []
                if "multiplex" in capabilities:
                    response_data["capabilities"] = ["multiplex"]
                    # Respond to Hello, then enter multiplexed mode
                    if len(msg) == 3:
                        await protocol.send_message_with_id(writer, "Hello", response_data, msg[2])
                    else:
                        await protocol.send_message(writer, "Hello", response_data)
                    logger.info("Entering multiplexed mode")
                    return await main_multiplexed(reader, writer, protocol, watcher, monitor, gate_hash, gate_policy, gate_environment, gate_host, gate_status_monitor)
                else:
                    await protocol.send_message(writer, "Hello", response_data)

            elif msg_type == "Module":
                logger.info(f"Module execution requested: {data.get('module_name', 'unknown')}")

                if _draining:
                    await protocol.send_message(
                        writer, "Error", {"message": "Gate is draining — not accepting new work"}
                    )
                    continue

                if not isinstance(data, dict):
                    await protocol.send_message(
                        writer, "Error", {"message": "Invalid Module data"}
                    )
                    continue

                # Gate-side policy check
                permitted, denial = _check_gate_policy(
                    gate_policy, data.get("module_name", ""), data.get("module_args", {}), gate_environment, gate_host
                )
                if not permitted:
                    logger.info(f"Policy denied Module: {denial}")
                    await protocol.send_message(writer, "PolicyDenied", denial)
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
                    global _error_count, _last_error
                    _error_count += 1
                    _last_error = str(e)
                    await protocol.send_message(
                        writer,
                        "ModuleNotFound",
                        {"message": f"Module not found: {e}"},
                    )

                except Exception as e:
                    _error_count += 1
                    _last_error = str(e)
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

                if _draining:
                    await protocol.send_message(
                        writer, "Error", {"message": "Gate is draining — not accepting new work"}
                    )
                    continue

                if not isinstance(data, dict):
                    await protocol.send_message(
                        writer, "Error", {"message": "Invalid FTLModule data"}
                    )
                    continue

                # Gate-side policy check
                permitted, denial = _check_gate_policy(
                    gate_policy, data.get("module_name", ""), data.get("module_args", {}), gate_environment, gate_host
                )
                if not permitted:
                    logger.info(f"Policy denied FTLModule: {denial}")
                    await protocol.send_message(writer, "PolicyDenied", denial)
                    continue

                try:
                    await execute_ftl_module(
                        protocol,
                        writer,
                        data.get("module_name", ""),
                        data.get("module", ""),
                        data.get("module_args", {}),
                    )

                except ModuleNotFoundError as e:
                    _error_count += 1
                    _last_error = str(e)
                    await protocol.send_message(
                        writer,
                        "ModuleNotFound",
                        {"message": f"FTLModule not found: {e}"},
                    )

                except Exception as e:
                    _error_count += 1
                    _last_error = str(e)
                    logger.exception("FTLModule execution failed")
                    await protocol.send_message(
                        writer,
                        "Error",
                        {
                            "message": f"FTLModule execution failed: {e}",
                            "traceback": traceback.format_exc(),
                        },
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

            elif msg_type == "SetPolicy":
                logger.info("SetPolicy requested")
                if isinstance(data, dict) and HAS_POLICY:
                    policy_rules = data.get("policy_rules", [])
                    gate_policy = Policy.from_wire(policy_rules)
                    gate_environment = data.get("environment", gate_environment)
                    gate_host = data.get("host", gate_host)
                    logger.info(f"Policy updated: {len(gate_policy.rules)} rules")
                    await protocol.send_message(
                        writer, "SetPolicyResult", {"status": "ok"}
                    )
                elif not HAS_POLICY:
                    await protocol.send_message(
                        writer, "SetPolicyResult", {"status": "error", "message": "Policy module not available"}
                    )
                else:
                    await protocol.send_message(
                        writer, "SetPolicyResult", {"status": "error", "message": "Invalid SetPolicy data"}
                    )

            elif msg_type == "StartGateStatus":
                interval = data.get("interval", 5.0) if isinstance(data, dict) else 5.0
                logger.info(f"StartGateStatus requested (interval={interval}s)")
                gate_status_monitor.start(interval=interval)
                await protocol.send_message(
                    writer, "GateStatusResult", {"status": "ok"}
                )

            elif msg_type == "StopGateStatus":
                logger.info("StopGateStatus requested")
                gate_status_monitor.stop()
                await protocol.send_message(
                    writer, "GateStatusResult", {"status": "stopped"}
                )

            elif msg_type == "GateDrain":
                logger.info("GateDrain requested")
                _draining = True
                await protocol.send_message(writer, "GateDrainResult", {
                    "status": "drained",
                    "completed": 0,
                    "in_flight": 0,
                })

            elif msg_type == "Shutdown":
                logger.info("Shutdown requested")
                watcher.stop()
                monitor.stop()
                gate_status_monitor.stop()
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


async def main_multiplexed(reader, writer, protocol, watcher, monitor, gate_hash, gate_policy=None, gate_environment="", gate_host="localhost", gate_status_monitor=None):
    """Concurrent message handling loop for multiplexed mode.

    Each incoming request is handled in its own asyncio task.
    Responses carry the same msg_id as the request.
    A write_lock serializes all writes to stdout.
    """
    write_lock = asyncio.Lock()

    # Install write_lock on event sources so their unsolicited
    # messages don't interleave with request responses.
    watcher._write_lock = write_lock
    monitor._write_lock = write_lock
    if gate_status_monitor is not None:
        gate_status_monitor._write_lock = write_lock

    async def handle_request(msg_type, data, msg_id):
        """Handle a single request and send response with same msg_id."""
        nonlocal gate_policy, gate_environment, gate_host
        try:
            if msg_type == "Hello":
                await protocol.send_message_with_id(
                    writer, "Hello", {"gate_hash": gate_hash},
                    msg_id, write_lock=write_lock,
                )
                return

            if msg_type == "Module":
                if not isinstance(data, dict):
                    await protocol.send_message_with_id(
                        writer, "Error", {"message": "Invalid Module data"},
                        msg_id, write_lock=write_lock,
                    )
                    return

                # Gate-side policy check
                permitted, denial = _check_gate_policy(
                    gate_policy, data.get("module_name", ""), data.get("module_args", {}), gate_environment, gate_host
                )
                if not permitted:
                    logger.info(f"Policy denied Module (multiplexed): {denial}")
                    await protocol.send_message_with_id(
                        writer, "PolicyDenied", denial,
                        msg_id, write_lock=write_lock,
                    )
                    return

                try:
                    result = await run_module(
                        data.get("module_name", ""),
                        data.get("module"),
                        data.get("module_args", {}),
                    )
                    await protocol.send_message_with_id(
                        writer, "ModuleResult", result,
                        msg_id, write_lock=write_lock,
                    )
                except ModuleNotFoundError as e:
                    global _error_count, _last_error
                    _error_count += 1
                    _last_error = str(e)
                    await protocol.send_message_with_id(
                        writer, "ModuleNotFound",
                        {"message": f"Module not found: {e}"},
                        msg_id, write_lock=write_lock,
                    )

            elif msg_type == "FTLModule":
                if not isinstance(data, dict):
                    await protocol.send_message_with_id(
                        writer, "Error", {"message": "Invalid FTLModule data"},
                        msg_id, write_lock=write_lock,
                    )
                    return

                # Gate-side policy check
                permitted, denial = _check_gate_policy(
                    gate_policy, data.get("module_name", ""), data.get("module_args", {}), gate_environment, gate_host
                )
                if not permitted:
                    logger.info(f"Policy denied FTLModule (multiplexed): {denial}")
                    await protocol.send_message_with_id(
                        writer, "PolicyDenied", denial,
                        msg_id, write_lock=write_lock,
                    )
                    return

                try:
                    resp_type, resp_data = await run_ftl_module(
                        data.get("module_name", ""),
                        data.get("module", ""),
                        data.get("module_args", {}),
                    )
                    await protocol.send_message_with_id(
                        writer, resp_type, resp_data,
                        msg_id, write_lock=write_lock,
                    )
                except ModuleNotFoundError as e:
                    _error_count += 1
                    _last_error = str(e)
                    await protocol.send_message_with_id(
                        writer, "ModuleNotFound",
                        {"message": f"FTLModule not found: {e}"},
                        msg_id, write_lock=write_lock,
                    )
                except Exception as e:
                    _error_count += 1
                    _last_error = str(e)
                    logger.exception("FTLModule execution failed")
                    await protocol.send_message_with_id(
                        writer, "Error",
                        {
                            "message": f"FTLModule execution failed: {e}",
                            "traceback": traceback.format_exc(),
                        },
                        msg_id, write_lock=write_lock,
                    )

            elif msg_type == "Info":
                await protocol.send_message_with_id(
                    writer, "InfoResult", {
                        "python_version": sys.version,
                        "python_executable": sys.executable,
                        "gate_location": os.path.abspath(sys.argv[0]) if sys.argv else "",
                        "platform": sys.platform,
                        "pid": os.getpid(),
                        "cwd": os.getcwd(),
                    },
                    msg_id, write_lock=write_lock,
                )

            elif msg_type == "ListModules":
                modules = list_gate_modules()
                await protocol.send_message_with_id(
                    writer, "ListModulesResult", {"modules": modules},
                    msg_id, write_lock=write_lock,
                )

            elif msg_type == "Watch":
                path = data.get("path", "") if isinstance(data, dict) else ""
                try:
                    watcher.add_watch(path)
                    await protocol.send_message_with_id(
                        writer, "WatchResult",
                        {"path": path, "status": "ok"},
                        msg_id, write_lock=write_lock,
                    )
                except ImportError:
                    await protocol.send_message_with_id(
                        writer, "WatchResult",
                        {"path": path, "status": "error",
                         "message": "inotify not available (Linux only)"},
                        msg_id, write_lock=write_lock,
                    )
                except Exception as e:
                    await protocol.send_message_with_id(
                        writer, "WatchResult",
                        {"path": path, "status": "error", "message": str(e)},
                        msg_id, write_lock=write_lock,
                    )

            elif msg_type == "Unwatch":
                path = data.get("path", "") if isinstance(data, dict) else ""
                found = watcher.remove_watch(path)
                await protocol.send_message_with_id(
                    writer, "UnwatchResult",
                    {"path": path, "removed": found},
                    msg_id, write_lock=write_lock,
                )

            elif msg_type == "StartMonitor":
                interval = data.get("interval", 2.0) if isinstance(data, dict) else 2.0
                include_procs = data.get("include_processes", True) if isinstance(data, dict) else True
                try:
                    monitor.start(interval=interval, include_processes=include_procs)
                    await protocol.send_message_with_id(
                        writer, "MonitorResult", {"status": "ok"},
                        msg_id, write_lock=write_lock,
                    )
                except ImportError:
                    await protocol.send_message_with_id(
                        writer, "MonitorResult",
                        {"status": "error",
                         "message": "psutil not available — install python3-psutil"},
                        msg_id, write_lock=write_lock,
                    )
                except Exception as e:
                    await protocol.send_message_with_id(
                        writer, "MonitorResult",
                        {"status": "error", "message": str(e)},
                        msg_id, write_lock=write_lock,
                    )

            elif msg_type == "StopMonitor":
                monitor.stop()
                await protocol.send_message_with_id(
                    writer, "MonitorResult", {"status": "stopped"},
                    msg_id, write_lock=write_lock,
                )

            elif msg_type == "SetPolicy":
                logger.info("SetPolicy requested (multiplexed)")
                if isinstance(data, dict) and HAS_POLICY:
                    policy_rules = data.get("policy_rules", [])
                    gate_policy = Policy.from_wire(policy_rules)
                    gate_environment = data.get("environment", gate_environment)
                    gate_host = data.get("host", gate_host)
                    logger.info(f"Policy updated: {len(gate_policy.rules)} rules")
                    await protocol.send_message_with_id(
                        writer, "SetPolicyResult", {"status": "ok"},
                        msg_id, write_lock=write_lock,
                    )
                elif not HAS_POLICY:
                    await protocol.send_message_with_id(
                        writer, "SetPolicyResult", {"status": "error", "message": "Policy module not available"},
                        msg_id, write_lock=write_lock,
                    )
                else:
                    await protocol.send_message_with_id(
                        writer, "SetPolicyResult", {"status": "error", "message": "Invalid SetPolicy data"},
                        msg_id, write_lock=write_lock,
                    )

            elif msg_type == "StartGateStatus":
                interval = data.get("interval", 5.0) if isinstance(data, dict) else 5.0
                gate_status_monitor.start(interval=interval)
                await protocol.send_message_with_id(
                    writer, "GateStatusResult", {"status": "ok"},
                    msg_id, write_lock=write_lock,
                )

            elif msg_type == "StopGateStatus":
                gate_status_monitor.stop()
                await protocol.send_message_with_id(
                    writer, "GateStatusResult", {"status": "stopped"},
                    msg_id, write_lock=write_lock,
                )

            else:
                await protocol.send_message_with_id(
                    writer, "Error",
                    {"message": f"Unknown message type: {msg_type}"},
                    msg_id, write_lock=write_lock,
                )

        except Exception as e:
            _error_count += 1
            _last_error = str(e)
            logger.exception(f"Request handler error for msg_id={msg_id}: {e}")
            try:
                await protocol.send_message_with_id(
                    writer, "GateSystemError", {
                        "message": f"System error: {e}",
                        "traceback": traceback.format_exc(),
                    },
                    msg_id, write_lock=write_lock,
                )
            except Exception:
                pass

    # Main reader loop
    tasks = set()
    global _active_tasks
    _active_tasks = tasks
    draining = False
    try:
        while True:
            msg = await protocol.read_message(reader)

            if msg is None:
                logger.info("EOF received in multiplexed mode, shutting down")
                break

            if len(msg) == 3:
                msg_type, data, msg_id = msg
            else:
                msg_type, data = msg
                msg_id = 0
                logger.warning(f"Received 2-tuple in multiplexed mode: {msg_type}")

            logger.debug(f"Multiplexed request: {msg_type} msg_id={msg_id}")

            # Handle Shutdown synchronously
            if msg_type == "Shutdown":
                logger.info("Shutdown requested in multiplexed mode")
                await protocol.send_message_with_id(
                    writer, "Goodbye", {}, msg_id, write_lock=write_lock,
                )
                break

            # Handle GateDrain synchronously — wait for in-flight tasks
            if msg_type == "GateDrain":
                logger.info("GateDrain requested in multiplexed mode")
                draining = True
                timeout = data.get("timeout_seconds", 30) if isinstance(data, dict) else 30
                completed = 0
                in_flight = 0
                if tasks:
                    done, pending = await asyncio.wait(tasks, timeout=timeout)
                    completed = len(done)
                    in_flight = len(pending)
                    for t in pending:
                        t.cancel()
                await protocol.send_message_with_id(
                    writer, "GateDrainResult", {
                        "status": "drained",
                        "completed": completed,
                        "in_flight": in_flight,
                    }, msg_id, write_lock=write_lock,
                )
                continue

            # Reject work messages when draining
            if draining and msg_type in ("Module", "FTLModule"):
                await protocol.send_message_with_id(
                    writer, "Error",
                    {"message": "Gate is draining — not accepting new work"},
                    msg_id, write_lock=write_lock,
                )
                continue

            # Spawn concurrent task for all other message types
            task = asyncio.create_task(handle_request(msg_type, data, msg_id))
            tasks.add(task)
            task.add_done_callback(tasks.discard)

    finally:
        # Wait for in-flight tasks to complete gracefully, then cancel stragglers
        if tasks:
            logger.info(f"Waiting for {len(tasks)} in-flight tasks to complete")
            _, pending = await asyncio.wait(tasks, timeout=30)
            if pending:
                logger.warning(f"Cancelling {len(pending)} tasks after timeout")
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
        watcher.stop()
        monitor.stop()
        if gate_status_monitor is not None:
            gate_status_monitor.stop()

    return None


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
