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
import shutil
import stat
import sys
import tempfile
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
        One of: "zip_bundle", "binary", "new_style", "want_json", "old_style"
    """
    if is_zip_bundle(module_bytes):
        return "zip_bundle"
    if is_binary_module(module_bytes):
        return "binary"
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
) -> tuple[bytes, bytes]:
    """Execute a shell command asynchronously and capture its output.

    Args:
        cmd: Shell command string to execute
        env: Optional environment variables for the subprocess
        stdin: Optional bytes data to send to process stdin

    Returns:
        Tuple of (stdout, stderr) as bytes
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
    return stdout, stderr


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
            stdout, stderr = await check_output(
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
            stdout, stderr = await check_output(f"{module_file} {args_file}")

        elif is_new_style_module(module_bytes):
            logger.info("Detected new-style module (AnsibleModule)")
            stdin_data = json.dumps({"ANSIBLE_MODULE_ARGS": module_args or {}}).encode()
            stdout, stderr = await check_output(
                f"{sys.executable} {module_file}",
                stdin=stdin_data,
                env=env,
            )

        elif is_want_json_module(module_bytes):
            logger.info("Detected WANT_JSON module")
            args_file = os.path.join(tempdir, "args")
            with open(args_file, "w") as f:
                json.dump(module_args or {}, f)
            stdout, stderr = await check_output(
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
            stdout, stderr = await check_output(
                f"{sys.executable} {module_file} {args_file}",
                env=env,
            )

        # Send result
        logger.info("Sending ModuleResult")
        await protocol.send_message(
            writer,
            "ModuleResult",
            {
                "stdout": stdout.decode(errors="replace"),
                "stderr": stderr.decode(errors="replace"),
            },
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
        module: Base64-encoded Python source code
        module_args: Arguments available to the module (passed to main)
    """
    logger.info(f"Executing FTL module: {module_name}")

    try:
        # Decode and compile module
        module_source = base64.b64decode(module)
        module_compiled = compile(module_source, module_name, "exec")

        # Execute module in isolated namespace
        globals_dict: dict[str, Any] = {
            "__file__": module_name,
            "__name__": "__main__",
        }
        locals_dict: dict[str, Any] = {}

        exec(module_compiled, globals_dict, locals_dict)

        # Find and call entry point
        if "main" in locals_dict:
            main_func = locals_dict["main"]
        elif "main" in globals_dict:
            main_func = globals_dict["main"]
        else:
            raise RuntimeError(f"Module {module_name} has no main() function")

        # Call the main function
        logger.info("Calling FTL module main()")
        if asyncio.iscoroutinefunction(main_func):
            # Async main - check if it accepts args
            import inspect
            sig = inspect.signature(main_func)
            if len(sig.parameters) > 0:
                result = await main_func(module_args or {})
            else:
                result = await main_func()
        else:
            # Sync main
            import inspect
            sig = inspect.signature(main_func)
            if len(sig.parameters) > 0:
                result = main_func(module_args or {})
            else:
                result = main_func()

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
    logging.basicConfig(
        format="%(asctime)s - %(message)s",
        filename="/tmp/ftl2_gate.log",
        level=logging.DEBUG,
    )

    logger.info("=" * 60)
    logger.info("FTL2 Gate starting")
    logger.info(f"Python: {sys.executable}")
    logger.info(f"Version: {sys.version}")
    logger.info(f"Path: {sys.path[:3]}...")
    logger.info("=" * 60)

    # Connect to stdin/stdout
    try:
        reader, writer = await connect_stdin_stdout()
        logger.info("Connected to stdin/stdout")
    except Exception as e:
        logger.error(f"Failed to connect stdin/stdout: {e}")
        return 1

    # Initialize protocol
    protocol = GateProtocol()

    # Message processing loop
    while True:
        try:
            # Read message
            msg = await protocol.read_message(reader)

            if msg is None:
                logger.info("EOF received, shutting down")
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
                await protocol.send_message(writer, "Hello", data)

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

            elif msg_type == "Shutdown":
                logger.info("Shutdown requested")
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
