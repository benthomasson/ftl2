"""Module proxy for dynamic attribute access.

Enables the ftl.module_name() syntax by intercepting attribute access
and returning async wrappers for module functions.

Supports both simple modules and FQCN (Fully Qualified Collection Name):
    await ftl.file(path="/tmp/test", state="touch")
    await ftl.amazon.aws.ec2_instance(instance_type="t3.micro")
"""

import asyncio
import time
from typing import Any, Callable, TYPE_CHECKING

from ftl2.module_loading.excluded import get_excluded
from ftl2.module_loading.shadowed import is_shadowed, get_native_method
from ftl2.exceptions import ExcludedModuleError

if TYPE_CHECKING:
    from ftl2.automation.context import AutomationContext


def _check_excluded(module_path: str) -> None:
    """Check if a module is excluded and raise if so.

    Args:
        module_path: Module name or FQCN

    Raises:
        ExcludedModuleError: If the module is excluded
    """
    excluded = get_excluded(module_path)
    if excluded:
        raise ExcludedModuleError(excluded)


class HostScopedProxy:
    """Proxy that runs modules on a specific host or group.

    Enables syntax like ftl.webservers.service(...) which is equivalent to
    ftl.run_on("webservers", "service", ...).

    Example:
        ftl.webservers.service(name="nginx", state="restarted")
        ftl.web01.file(path="/tmp/test", state="touch")
        ftl.local.community.general.linode_v4(label="web01", ...)
    """

    def __init__(self, context: "AutomationContext", target: str):
        """Initialize the host-scoped proxy.

        Args:
            context: The AutomationContext that handles execution
            target: Host name or group name to target
        """
        self._context = context
        self._target = target

    async def wait_for_ssh(
        self,
        timeout: int = 600,
        delay: int = 0,
        sleep: int = 1,
        connect_timeout: int = 5,
    ) -> dict[str, Any]:
        """Wait for SSH to become available on this host.

        This is the FTL2-native implementation that shadows Ansible's
        wait_for_connection module. Uses the same parameters for seamless
        Ansible knowledge transfer.

        Args:
            timeout: Maximum seconds to wait (default: 600, matches Ansible)
            delay: Seconds to wait before first check (default: 0)
            sleep: Seconds between retry attempts (default: 1)
            connect_timeout: Timeout for each connection attempt (default: 5)

        Returns:
            dict with 'elapsed' (seconds waited) and 'changed' (always False)

        Raises:
            TimeoutError: If SSH is not available within the timeout

        Example:
            ftl.add_host("minecraft-9", ansible_host=ip)
            await ftl.minecraft_9.wait_for_ssh(timeout=120, delay=10)
            await ftl.minecraft_9.dnf(name="java-17-openjdk")
        """
        import asyncio

        # Initial delay before first check
        if delay > 0:
            await asyncio.sleep(delay)

        # Resolve target to list of hosts (handles both groups and individual hosts)
        # This is the same pattern used by run_on()
        hosts_proxy = self._context.hosts

        try:
            # Use hosts_proxy[target] which handles both groups and hosts
            # via __getitem__ (not keys() which only has host names)
            host_configs = hosts_proxy[self._target]
        except KeyError:
            # Target not in inventory, fall back to using target name directly
            # This handles localhost and other special cases
            host_configs = []

        if not host_configs:
            # No hosts found in inventory, use target name directly
            # This handles localhost, special targets, or unknown hosts
            ansible_host = self._target
            port = 22

            start = time.monotonic()
            last_error = None

            while True:
                try:
                    reader, writer = await asyncio.wait_for(
                        asyncio.open_connection(ansible_host, port),
                        timeout=connect_timeout,
                    )
                    writer.close()
                    await writer.wait_closed()
                    elapsed = int(time.monotonic() - start)
                    return {"elapsed": elapsed, "changed": False}
                except (OSError, asyncio.TimeoutError) as e:
                    last_error = e
                    elapsed = time.monotonic() - start
                    if elapsed >= timeout:
                        raise TimeoutError(
                            f"SSH not available on {ansible_host}:{port} "
                            f"after {timeout} seconds"
                        ) from last_error
                    await asyncio.sleep(sleep)

        # Wait for SSH on all hosts in the target (group or single host)
        start = time.monotonic()

        for host_config in host_configs:
            # Use ansible_host (IP address) if set, otherwise fall back to host name
            ansible_host = host_config.ansible_host or host_config.name
            port = host_config.ansible_port or 22

            last_error = None

            while True:
                try:
                    reader, writer = await asyncio.wait_for(
                        asyncio.open_connection(ansible_host, port),
                        timeout=connect_timeout,
                    )
                    writer.close()
                    await writer.wait_closed()
                    break  # This host is ready, move to next
                except (OSError, asyncio.TimeoutError) as e:
                    last_error = e
                    elapsed = time.monotonic() - start
                    if elapsed >= timeout:
                        raise TimeoutError(
                            f"SSH not available on {host_config.name} ({ansible_host}:{port}) "
                            f"after {timeout} seconds"
                        ) from last_error
                    await asyncio.sleep(sleep)

        elapsed = int(time.monotonic() - start)
        return {"elapsed": elapsed, "changed": False}

    async def ping(self) -> dict[str, str]:
        """Test FTL2 gate connectivity by executing through the full pipeline.

        This is the FTL2-native implementation that shadows Ansible's
        ping module. Unlike Ansible's ping (which tests connection plugin),
        this tests the complete FTL2 execution pipeline:

        1. TCP - Port reachable
        2. SSH - Authentication works
        3. Gate setup - Remote gate process starts (for remote hosts)
        4. Command execution - Can run commands through gate
        5. Response - Round-trip communication works

        The "pong" response is generated by actually executing a command
        on the target, proving the entire pipeline works.

        Returns:
            dict with {"ping": "pong"} - "pong" comes from the remote host

        Raises:
            ConnectionError: If connection fails
            AuthenticationError: If SSH auth fails
            TimeoutError: If connection times out

        Example:
            result = await ftl.minecraft.ping()
            assert result["ping"] == "pong"
        """
        from ftl2.exceptions import ConnectionError as FTL2ConnectionError

        try:
            # For local/localhost, use local execution
            if self._target in ("local", "localhost"):
                result = await self._context.execute("command", {"cmd": "echo pong"})
                stdout = result.get("stdout", "").strip()
                if stdout != "pong":
                    raise FTL2ConnectionError(
                        f"Ping failed: unexpected response '{stdout}'"
                    )
                return {"ping": "pong"}

            # For remote hosts, run through the full gate pipeline
            results = await self._context.run_on(self._target, "command", cmd="echo pong")
            if not results:
                raise FTL2ConnectionError(
                    f"Ping failed: no response from {self._target}"
                )

            # Check all results (supports both single hosts and groups)
            failed = []
            for r in results:
                if not r.success:
                    failed.append(f"{r.host}: {r.error}")
                else:
                    # Gate wraps output under "result" key
                    output = r.output.get("result", r.output)
                    stdout = output.get("stdout", "").strip()
                    if stdout != "pong":
                        failed.append(f"{r.host}: unexpected response '{stdout}'")

            if failed:
                raise FTL2ConnectionError(
                    f"Ping failed on {len(failed)}/{len(results)} host(s): "
                    + "; ".join(failed)
                )

            return {"ping": "pong"}

        except TimeoutError:
            raise
        except FTL2ConnectionError:
            raise
        except Exception as e:
            raise FTL2ConnectionError(f"Ping failed: {e}") from e

    async def _get_host_configs(self) -> list:
        """Resolve target to list of host configs."""
        hosts_proxy = self._context.hosts
        try:
            return hosts_proxy[self._target]
        except KeyError:
            return []

    def _track_result(
        self,
        module_name: str,
        result: dict[str, Any],
        start_time: float,
        params: dict[str, Any] | None = None,
    ) -> None:
        """Register a native method result in the audit/summary pipeline."""
        from ftl2.ftl_modules.executor import ExecuteResult

        duration = time.time() - start_time
        exec_result = ExecuteResult(
            success=not result.get("failed", False),
            changed=result.get("changed", False),
            output=result,
            module=module_name,
            host=self._target or "localhost",
            used_ftl=True,
            duration=duration,
            timestamp=start_time,
        )
        if params is not None:
            exec_result.params = params
        self._context._results.append(exec_result)

        if self._context.verbose and not self._context.quiet:
            self._context._log_result(
                f"{self._target}:{module_name}", exec_result, duration
            )

    async def copy(
        self,
        src: str | None = None,
        dest: str = "",
        content: str | None = None,
        mode: str | None = None,
        owner: str | None = None,
        group: str | None = None,
        backup: bool = False,
    ) -> dict[str, Any]:
        """Copy a local file to the remote host via SFTP.

        This is the FTL2-native implementation that shadows Ansible's copy
        module. Unlike bundled modules, this reads the source file locally
        and transfers it directly via SFTP.

        Args:
            src: Source file path (on controller). Relative paths resolve from CWD.
            dest: Destination file path (on target). Required.
            content: Inline content to write instead of src file.
            mode: File mode (e.g., "0644", "755")
            owner: File owner username
            group: File group name
            backup: Create timestamped backup if dest exists

        Returns:
            dict with 'changed', 'dest', 'src', and optionally 'backup'

        Raises:
            FileNotFoundError: If src doesn't exist
            ValueError: If neither src nor content provided

        Example:
            await ftl.webserver.copy(src="nginx.conf", dest="/etc/nginx/nginx.conf")
            await ftl.webserver.copy(content="Hello", dest="/tmp/hello.txt")
        """
        from pathlib import Path
        from datetime import datetime

        start_time = time.time()

        if not dest:
            raise ValueError("dest is required")

        if not src and not content:
            raise ValueError("Either 'src' or 'content' must be provided")

        # Read local file content
        if src:
            src_path = Path(src).expanduser()
            if not src_path.is_absolute():
                src_path = Path.cwd() / src_path

            if not src_path.exists():
                raise FileNotFoundError(f"Source file not found: {src_path}")

            file_content = src_path.read_bytes()
        else:
            # Inline content
            file_content = content.encode("utf-8") if isinstance(content, str) else content

        # For localhost, use local file operations
        if self._target in ("local", "localhost"):
            dest_path = Path(dest)
            changed = True
            backup_path = None

            # Check if content matches
            if dest_path.exists():
                if dest_path.read_bytes() == file_content:
                    changed = False

                # Create backup if requested
                if backup and changed:
                    backup_path = f"{dest}.{datetime.now().strftime('%Y%m%d%H%M%S')}"
                    dest_path.rename(backup_path)

            if changed:
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                dest_path.write_bytes(file_content)

            # Set mode
            if mode:
                mode_str = mode.lstrip("0") if mode.startswith("0") else mode
                mode_int = int(mode_str, 8)
                dest_path.chmod(mode_int)

            result: dict[str, Any] = {
                "changed": changed,
                "dest": dest,
                "src": str(src_path) if src else "<content>",
            }
            if backup_path:
                result["backup"] = backup_path
            self._track_result("copy", result, start_time)
            return result

        # Remote execution via SFTP
        host_configs = await self._get_host_configs()
        if not host_configs:
            raise ValueError(f"No hosts found for target: {self._target}")

        # Execute on first host (copy is typically run on single host)
        # For group operations, this would need to loop
        host_config = host_configs[0]
        ssh = await self._context._get_ssh_connection(host_config)

        changed = True
        backup_path = None

        # Check if content matches (idempotency)
        remote_content = await ssh.read_file_or_none(dest)
        if remote_content == file_content:
            changed = False

        # Create backup if requested
        if backup and changed and remote_content is not None:
            backup_path = f"{dest}.{datetime.now().strftime('%Y%m%d%H%M%S')}"
            await ssh.rename(dest, backup_path)

        # Ensure destination directory exists
        if changed:
            dest_dir = str(Path(dest).parent)
            await ssh.run(f"mkdir -p '{dest_dir}'")

        # Write file
        if changed:
            await ssh.write_file(dest, file_content)

        # Set mode
        if mode:
            mode_str = mode.lstrip("0") if mode.startswith("0") else mode
            mode_int = int(mode_str, 8)
            current_stat = await ssh.stat(dest)
            if current_stat and current_stat["mode"] != mode_int:
                await ssh.chmod(dest, mode_int)
                changed = True

        # Set ownership (check current owner/group before changing)
        if owner or group:
            stdout, _, _ = await ssh.run(f"stat -c '%U %G' {dest}")
            parts = stdout.strip().split()
            if len(parts) == 2:
                current_owner, current_group = parts
                needs_owner = owner and current_owner != owner
                needs_group = group and current_group != group
                if needs_owner or needs_group:
                    await ssh.chown(dest, owner if needs_owner else None,
                                    group if needs_group else None)
                    changed = True
            else:
                # Can't determine current ownership, set unconditionally
                await ssh.chown(dest, owner, group)
                changed = True

        result = {
            "changed": changed,
            "dest": dest,
            "src": str(src_path) if src else "<content>",
        }
        if backup_path:
            result["backup"] = backup_path
        self._track_result("copy", result, start_time)
        return result

    async def template(
        self,
        src: str,
        dest: str,
        mode: str | None = None,
        owner: str | None = None,
        group: str | None = None,
        **variables: Any,
    ) -> dict[str, Any]:
        """Render a Jinja2 template and copy to remote host.

        This is the FTL2-native implementation that shadows Ansible's template
        module. Renders the template locally using Jinja2, then transfers the
        result via SFTP.

        Args:
            src: Template file path (on controller). Relative paths resolve from CWD.
            dest: Destination file path (on target)
            mode: File mode (e.g., "0644")
            owner: File owner username
            group: File group name
            **variables: Template variables passed to Jinja2

        Returns:
            dict with 'changed', 'dest', 'src'

        Raises:
            FileNotFoundError: If template doesn't exist

        Example:
            await ftl.webserver.template(
                src="nginx.conf.j2",
                dest="/etc/nginx/nginx.conf",
                server_name="example.com",
                port=8080,
            )
        """
        from pathlib import Path
        from jinja2 import Environment, FileSystemLoader

        # Resolve template path
        src_path = Path(src)
        if not src_path.is_absolute():
            src_path = Path.cwd() / src_path

        if not src_path.exists():
            raise FileNotFoundError(f"Template not found: {src_path}")

        # Set up Jinja2 environment
        env = Environment(
            loader=FileSystemLoader(src_path.parent),
            keep_trailing_newline=True,
        )
        template = env.get_template(src_path.name)

        # Render template
        rendered = template.render(**variables)
        content = rendered.encode("utf-8")

        # Use copy() for the actual transfer (handles idempotency, permissions)
        # Pass content instead of src since we've already rendered
        result = await self.copy(
            content=rendered,
            dest=dest,
            mode=mode,
            owner=owner,
            group=group,
        )

        # Update result to show template source
        result["src"] = str(src_path)
        return result

    async def fetch(
        self,
        src: str,
        dest: str,
        flat: bool = False,
    ) -> dict[str, Any]:
        """Fetch a file from remote host to local.

        This is the FTL2-native implementation that shadows Ansible's fetch
        module. Reads the file from the remote host via SFTP and writes it
        locally.

        Args:
            src: Source file path (on remote)
            dest: Destination directory or file path (on controller)
            flat: If True, write directly to dest. If False, create
                  dest/hostname/src structure (Ansible default)

        Returns:
            dict with 'changed', 'dest', 'src'

        Raises:
            FileNotFoundError: If remote file doesn't exist

        Example:
            await ftl.webserver.fetch(src="/var/log/nginx/error.log", dest="./logs/")
        """
        from pathlib import Path

        start_time = time.time()

        # For localhost, just copy locally
        if self._target in ("local", "localhost"):
            src_path = Path(src)
            if not src_path.exists():
                raise FileNotFoundError(f"File not found: {src}")

            content = src_path.read_bytes()
            dest_path = Path(dest)

            if not flat:
                dest_path = dest_path / "localhost" / src.lstrip("/")

            dest_path.parent.mkdir(parents=True, exist_ok=True)
            dest_path.write_bytes(content)

            result = {
                "changed": True,
                "dest": str(dest_path),
                "src": src,
            }
            self._track_result("fetch", result, start_time)
            return result

        # Remote fetch via SFTP
        host_configs = await self._get_host_configs()
        if not host_configs:
            raise ValueError(f"No hosts found for target: {self._target}")

        host_config = host_configs[0]
        ssh = await self._context._get_ssh_connection(host_config)

        # Read remote file
        content = await ssh.read_file_or_none(src)
        if content is None:
            raise FileNotFoundError(f"Remote file not found: {src}")

        # Determine local destination
        dest_path = Path(dest)
        if not flat:
            dest_path = dest_path / host_config.name / src.lstrip("/")

        # Create parent directories and write
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_bytes(content)

        result = {
            "changed": True,
            "dest": str(dest_path),
            "src": src,
        }
        self._track_result("fetch", result, start_time)
        return result

    async def shell(
        self,
        cmd: str,
        chdir: str | None = None,
        creates: str | None = None,
        removes: str | None = None,
        executable: str = "/bin/sh",
        stdin: str | None = None,
    ) -> dict[str, Any]:
        """Execute a command through a shell.

        This is the FTL2-native implementation that shadows Ansible's shell
        module. Unlike the command module, this runs through a shell interpreter,
        enabling pipes, redirects, environment variables, and shell builtins.

        Args:
            cmd: The command to execute (passed to shell via -c)
            chdir: Change to this directory before running the command
            creates: If this path exists, skip execution (for idempotency)
            removes: If this path does NOT exist, skip execution (for idempotency)
            executable: Shell to use (default: /bin/sh). Use /bin/bash for bash features.
            stdin: Data to send to the command's stdin

        Returns:
            dict with 'changed', 'stdout', 'stderr', 'rc', 'cmd'

        Example:
            # Basic shell command with pipes
            await ftl.webserver.shell(cmd="ps aux | grep nginx | wc -l")

            # With redirects
            await ftl.webserver.shell(cmd="echo 'Hello' > /tmp/hello.txt")

            # Idempotent - only run if file doesn't exist
            await ftl.webserver.shell(
                cmd="expensive-setup-command",
                creates="/var/lib/app/.initialized"
            )

            # Use bash for advanced features
            await ftl.webserver.shell(
                cmd="for i in {1..5}; do echo $i; done",
                executable="/bin/bash"
            )
        """
        import shlex
        import subprocess

        start_time = time.time()

        # For localhost, execute locally
        if self._target in ("local", "localhost"):
            # Handle creates/removes idempotency
            from pathlib import Path

            if creates and Path(creates).exists():
                result = {
                    "changed": False,
                    "stdout": "",
                    "stderr": "",
                    "rc": 0,
                    "cmd": cmd,
                    "msg": f"skipped, since {creates} exists",
                }
                self._track_result("shell", result, start_time)
                return result

            if removes and not Path(removes).exists():
                result = {
                    "changed": False,
                    "stdout": "",
                    "stderr": "",
                    "rc": 0,
                    "cmd": cmd,
                    "msg": f"skipped, since {removes} does not exist",
                }
                self._track_result("shell", result, start_time)
                return result

            # Build and execute locally
            proc = subprocess.run(
                [executable, "-c", cmd],
                cwd=chdir,
                input=stdin,
                capture_output=True,
                text=True,
            )

            result = {
                "changed": True,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
                "rc": proc.returncode,
                "cmd": cmd,
                "delta": time.time() - start_time,
                "stdout_lines": proc.stdout.splitlines(),
                "stderr_lines": proc.stderr.splitlines(),
            }
            self._track_result("shell", result, start_time)
            return result

        # Remote execution via SSH
        host_configs = await self._get_host_configs()
        if not host_configs:
            raise ValueError(f"No hosts found for target: {self._target}")

        host_config = host_configs[0]
        ssh = await self._context._get_ssh_connection(host_config)

        # Handle creates/removes idempotency
        if creates:
            exists = await ssh.path_exists(creates)
            if exists:
                result = {
                    "changed": False,
                    "stdout": "",
                    "stderr": "",
                    "rc": 0,
                    "cmd": cmd,
                    "msg": f"skipped, since {creates} exists",
                }
                self._track_result("shell", result, start_time)
                return result

        if removes:
            exists = await ssh.path_exists(removes)
            if not exists:
                result = {
                    "changed": False,
                    "stdout": "",
                    "stderr": "",
                    "rc": 0,
                    "cmd": cmd,
                    "msg": f"skipped, since {removes} does not exist",
                }
                self._track_result("shell", result, start_time)
                return result

        # Build shell command: executable -c 'cmd'
        # Handle chdir by prefixing with cd
        if chdir:
            full_cmd = f"cd {shlex.quote(chdir)} && {executable} -c {shlex.quote(cmd)}"
        else:
            full_cmd = f"{executable} -c {shlex.quote(cmd)}"

        # Execute via SSH
        stdout, stderr, rc = await ssh.run(full_cmd, stdin=stdin or "")

        result = {
            "changed": True,
            "stdout": stdout,
            "stderr": stderr,
            "rc": rc,
            "cmd": cmd,
            "stdout_lines": stdout.splitlines(),
            "stderr_lines": stderr.splitlines(),
        }
        self._track_result("shell", result, start_time)
        return result

    async def watch(self, path: str) -> dict[str, Any]:
        """Watch a file or directory for changes on the remote host.

        Registers an inotify watch via the gate. File change events
        are delivered through handlers registered with ``on()`` and
        received during ``ftl.listen()``.

        Args:
            path: Absolute path to watch on the remote host

        Returns:
            dict with 'path' and 'status' ("ok" or "error")

        Example:
            await ftl.webserver.watch(path="/etc/nginx/nginx.conf")
            ftl.webserver.on("FileChanged", lambda e: print(e))
            await ftl.listen(timeout=60)
        """
        host_configs = await self._get_host_configs()
        if not host_configs:
            raise ValueError(f"No hosts found for target: {self._target}")

        async def _watch_one(host_config):
            resp_type, resp_data = await self._context._send_gate_command(
                host_config, "Watch", {"path": path}
            )
            if resp_type == "WatchResult":
                if resp_data.get("status") == "error":
                    raise RuntimeError(
                        f"Watch failed for {path} on {host_config.name}: "
                        f"{resp_data.get('message', 'unknown error')}"
                    )
                return resp_data
            elif resp_type == "Error":
                raise RuntimeError(resp_data.get("message", "Watch failed"))
            else:
                raise RuntimeError(f"Unexpected response to Watch: {resp_type}")

        results = await asyncio.gather(*(_watch_one(h) for h in host_configs))
        return results[0]

    async def monitor(
        self,
        interval: float = 2.0,
        include_processes: bool = True,
    ) -> dict[str, Any]:
        """Start system metrics streaming from the remote host.

        Requires ``python3-psutil`` installed on the remote host.
        Metrics are delivered through handlers registered with
        ``on("SystemMetrics", handler)`` and received during
        ``ftl.listen()``.

        Args:
            interval: Seconds between metric samples (default 2.0)
            include_processes: Include top 20 processes by CPU (default True)

        Returns:
            dict with 'status' ("ok" or "error")

        Example:
            await ftl.webserver.dnf(name="python3-psutil", state="present")
            await ftl.webserver.monitor(interval=2)
            ftl.webserver.on("SystemMetrics", lambda m: print(m["cpu"]))
            await ftl.listen(timeout=30)
        """
        host_configs = await self._get_host_configs()
        if not host_configs:
            raise ValueError(f"No hosts found for target: {self._target}")

        async def _monitor_one(host_config):
            resp_type, resp_data = await self._context._send_gate_command(
                host_config,
                "StartMonitor",
                {"interval": interval, "include_processes": include_processes},
            )
            if resp_type == "MonitorResult":
                if resp_data.get("status") == "error":
                    raise RuntimeError(
                        f"Monitor failed on {host_config.name}: "
                        f"{resp_data.get('message', 'unknown error')}"
                    )
                return resp_data
            elif resp_type == "Error":
                raise RuntimeError(resp_data.get("message", "Monitor failed"))
            else:
                raise RuntimeError(f"Unexpected response to StartMonitor: {resp_type}")

        results = await asyncio.gather(*(_monitor_one(h) for h in host_configs))
        return results[0]

    async def unmonitor(self) -> dict[str, Any]:
        """Stop system metrics streaming from the remote host.

        Returns:
            dict with 'status' ("stopped")
        """
        host_configs = await self._get_host_configs()
        if not host_configs:
            raise ValueError(f"No hosts found for target: {self._target}")

        async def _unmonitor_one(host_config):
            resp_type, resp_data = await self._context._send_gate_command(
                host_config, "StopMonitor", {}
            )
            if resp_type == "MonitorResult":
                return resp_data
            elif resp_type == "Error":
                raise RuntimeError(resp_data.get("message", "StopMonitor failed"))
            else:
                raise RuntimeError(f"Unexpected response to StopMonitor: {resp_type}")

        results = await asyncio.gather(*(_unmonitor_one(h) for h in host_configs))
        return results[0]

    def on(self, event_type: str, handler: Any) -> None:
        """Register an event handler for this host/group.

        Args:
            event_type: Event type to handle (e.g., "FileChanged")
            handler: Callback function. Receives event data dict.
                Can be sync or async.

        Example:
            def on_change(event):
                print(f"File {event['path']} was {event['event']}")

            ftl.webserver.on("FileChanged", on_change)
        """
        self._context._register_event_handler(self._target, event_type, handler)

    def __getattr__(self, name: str) -> "HostScopedModuleProxy":
        """Return a module proxy scoped to this host/group.

        Args:
            name: Module name or namespace component

        Returns:
            HostScopedModuleProxy for the module
        """
        if name.startswith("_"):
            raise AttributeError(name)

        return HostScopedModuleProxy(self._context, self._target, name)

    def __repr__(self) -> str:
        return f"HostScopedProxy({self._target!r})"


class HostScopedModuleProxy:
    """Proxy for a module scoped to a specific host/group.

    Supports both simple modules and FQCN:
        ftl.webservers.service(...)
        ftl.webservers.ansible.posix.firewalld(...)
    """

    def __init__(self, context: "AutomationContext", target: str, path: str):
        """Initialize the host-scoped module proxy.

        Args:
            context: The AutomationContext that handles execution
            target: Host name or group name to target
            path: Module name or namespace path
        """
        self._context = context
        self._target = target
        self._path = path

    def __getattr__(self, name: str) -> "HostScopedModuleProxy":
        """Extend the module path for FQCN support.

        Args:
            name: Next component of the namespace

        Returns:
            HostScopedModuleProxy with extended path
        """
        if name.startswith("_"):
            raise AttributeError(name)

        new_path = f"{self._path}.{name}"
        return HostScopedModuleProxy(self._context, self._target, new_path)

    async def __call__(self, **kwargs: Any) -> Any:
        """Execute the module on the target host/group.

        Args:
            **kwargs: Module parameters

        Returns:
            For localhost: dict (module output) - more intuitive for local use
            For remote hosts/groups: list[ExecuteResult]

        Raises:
            ExcludedModuleError: If the module is excluded from FTL2
        """
        # Check if module is shadowed by a native implementation
        if is_shadowed(self._path):
            method_name = get_native_method(self._path)
            host_proxy = HostScopedProxy(self._context, self._target)
            native_method = getattr(host_proxy, method_name)
            return await native_method(**kwargs)

        # Check if module is excluded
        _check_excluded(self._path)

        # Special case: local/localhost executes directly without inventory
        if self._target in ("local", "localhost"):
            return await self._context.execute(self._path, kwargs)

        return await self._context.run_on(self._target, self._path, **kwargs)

    def __repr__(self) -> str:
        return f"HostScopedModuleProxy({self._target!r}, {self._path!r})"


class NamespaceProxy:
    """Proxy for FQCN namespace traversal.

    Enables dotted access like ftl.amazon.aws.ec2_instance by tracking
    the namespace path and returning nested proxies until the final
    module is called.

    Example:
        ftl.amazon        -> NamespaceProxy(context, "amazon")
        ftl.amazon.aws    -> NamespaceProxy(context, "amazon.aws")
        ftl.amazon.aws.ec2_instance(...) -> executes "amazon.aws.ec2_instance"
    """

    def __init__(self, context: "AutomationContext", path: str):
        """Initialize the namespace proxy.

        Args:
            context: The AutomationContext that handles execution
            path: The current namespace path (e.g., "amazon" or "amazon.aws")
        """
        self._context = context
        self._path = path

    def __getattr__(self, name: str) -> "NamespaceProxy":
        """Return a nested proxy for the next namespace component.

        Args:
            name: Next component of the namespace

        Returns:
            NamespaceProxy with extended path
        """
        if name.startswith("_"):
            raise AttributeError(name)

        # Extend the path
        new_path = f"{self._path}.{name}"
        return NamespaceProxy(self._context, new_path)

    async def __call__(self, **kwargs: Any) -> dict[str, Any]:
        """Execute the module at the current path.

        This is called when the namespace proxy is invoked as a function,
        e.g., ftl.amazon.aws.ec2_instance(instance_type="t3.micro")

        Args:
            **kwargs: Module parameters

        Returns:
            Module output dictionary

        Raises:
            ExcludedModuleError: If the module is excluded from FTL2
        """
        # Check if module is excluded
        _check_excluded(self._path)

        return await self._context.execute(self._path, kwargs)

    def __repr__(self) -> str:
        return f"NamespaceProxy({self._path!r})"


class ModuleProxy:
    """Proxy that enables ftl.module_name() syntax via __getattr__.

    When you access an attribute like `ftl.file`, this proxy intercepts
    the access and returns an async wrapper that calls the FTL module.

    For simple modules (file, copy, command), it returns a callable wrapper.
    For namespaced modules (amazon.aws.ec2_instance), it returns a
    NamespaceProxy that enables chained attribute access.

    Example:
        proxy = ModuleProxy(context)

        # Simple module
        result = await proxy.file(path="/tmp/test", state="touch")

        # FQCN module (collection)
        result = await proxy.amazon.aws.ec2_instance(instance_type="t3.micro")
    """

    def __init__(self, context: "AutomationContext"):
        """Initialize the proxy with an automation context.

        Args:
            context: The AutomationContext that handles execution
        """
        self._context = context

    def __getitem__(self, name: str) -> HostScopedProxy:
        """Return a HostScopedProxy for the given host or group name.

        Supports names with dashes and other characters that aren't valid
        in Python attributes::

            await ftl["ftl2-scale-0"].hostname(name="ftl2-scale-0")

        Args:
            name: Host or group name (exact match, no normalization)

        Returns:
            HostScopedProxy for the target

        Raises:
            KeyError: If the name is not a known host or group
        """
        if name in ("local", "localhost"):
            return HostScopedProxy(self._context, "localhost")

        hosts_proxy = self._context.hosts
        if name in hosts_proxy.groups or name in hosts_proxy.keys():
            return HostScopedProxy(self._context, name)

        raise KeyError(f"Host or group '{name}' not found in inventory")

    def __getattr__(self, name: str) -> Callable[..., Any] | NamespaceProxy | HostScopedProxy:
        """Return async wrapper for module, host proxy, or namespace proxy.

        Priority:
        1. local/localhost -> HostScopedProxy for localhost
        2. Host/group names -> HostScopedProxy for that target
        3. Known modules -> async wrapper
        4. Unknown names -> NamespaceProxy for FQCN

        Args:
            name: Module name, host/group name, or namespace

        Returns:
            Async function for known modules, HostScopedProxy for hosts/groups,
            NamespaceProxy for collection namespaces

        Raises:
            AttributeError: For private attributes or disabled modules
        """
        # Don't intercept private attributes
        if name.startswith("_"):
            raise AttributeError(name)

        # Check for local/localhost first
        if name in ("local", "localhost"):
            return HostScopedProxy(self._context, "localhost")

        # Check if it's a host or group name
        # Also check with underscore→dash normalization since Python attributes
        # can't have dashes but hostnames commonly do (DNS standard)
        try:
            hosts_proxy = self._context.hosts
            # Try exact match first
            if name in hosts_proxy.groups or name in hosts_proxy.keys():
                return HostScopedProxy(self._context, name)
            # Try underscore→dash normalization (e.g., minecraft_9 → minecraft-9)
            normalized = name.replace("_", "-")
            if normalized != name:
                if normalized in hosts_proxy.groups or normalized in hosts_proxy.keys():
                    return HostScopedProxy(self._context, normalized)
        except Exception:
            # Inventory not loaded or other issue - continue to module check
            pass

        # Check if it's a known simple module
        from ftl2.ftl_modules import get_module, list_modules

        module = get_module(name)
        if module is not None:
            # Known module - return async wrapper
            async def wrapper(**kwargs: Any) -> dict[str, Any]:
                """Execute the module with the given parameters."""
                # Check if module is excluded
                _check_excluded(name)
                return await self._context.execute(name, kwargs)

            wrapper.__name__ = name
            wrapper.__doc__ = f"Execute the '{name}' module."
            return wrapper

        # Check if it's in the enabled modules list (if restricted)
        if self._context._enabled_modules is not None:
            if name in list_modules():
                raise AttributeError(
                    f"Module '{name}' is not enabled. "
                    f"Enabled modules: {', '.join(self._context._enabled_modules)}"
                )

        # Not a known simple module - treat as namespace for FQCN
        # This enables: ftl.amazon.aws.ec2_instance(...)
        return NamespaceProxy(self._context, name)
