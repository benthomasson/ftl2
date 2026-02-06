# FTL Modules Examples

This directory demonstrates FTL modules - in-process Python functions that are 250x+ faster than traditional Ansible module execution.

## Quick Start

```bash
# Run local examples (no setup required)
uv run python example_local.py

# Run remote examples (requires Docker)
docker-compose up -d
uv run python example_remote.py
docker-compose down
```

## Examples

### 1. Local Execution (`example_local.py`)

Demonstrates FTL modules running locally:
- File operations (touch, copy, delete)
- Command execution
- HTTP requests (mocked)
- Batch execution of multiple modules

### 2. Remote Execution (`example_remote.py`)

Demonstrates remote execution via async SSH:
- SSH connection to Docker container
- Remote command execution
- File operations on remote host
- Concurrent execution on multiple hosts

### 3. Comparison (`example_comparison.py`)

Side-by-side comparison of:
- FTL modules vs Ansible modules (via module_loading)
- Performance timing for each approach

## FTL Module API

### Direct Function Calls

```python
from ftl2.ftl_modules import ftl_file, ftl_command, ftl_copy

# Touch a file
result = ftl_file(path="/tmp/test.txt", state="touch")
print(f"Changed: {result['changed']}")

# Run a command
result = ftl_command(cmd="echo hello")
print(f"Output: {result['stdout']}")

# Copy a file
result = ftl_copy(src="/tmp/source.txt", dest="/tmp/dest.txt")
```

### Executor API (Recommended)

```python
import asyncio
from ftl2.ftl_modules import execute, execute_on_hosts, run, LocalHost

async def main():
    # Simple execution
    result = await execute("file", {"path": "/tmp/test.txt", "state": "touch"})
    print(f"Success: {result.success}, Changed: {result.changed}")

    # Convenience function
    result = await run("command", cmd="uptime")
    print(result.output["stdout"])

    # Concurrent execution on multiple hosts
    hosts = [LocalHost(name=f"host{i}") for i in range(10)]
    results = await execute_on_hosts(hosts, "command", {"cmd": "echo hello"})
    for r in results:
        print(f"{r.host}: {r.output.get('stdout', '').strip()}")

asyncio.run(main())
```

### SSH Remote Execution

```python
import asyncio
from ftl2.ssh import SSHHost
from ftl2.ftl_modules import execute

async def main():
    host = SSHHost(
        hostname="localhost",
        port=2222,
        username="testuser",
        password="testpass",
        known_hosts=None,  # Disable host key checking for testing
    )

    async with host:
        # Run command on remote host
        stdout, stderr, rc = await host.run("uptime")
        print(f"Uptime: {stdout}")

        # Use with executor (falls back to Ansible module bundling)
        result = await execute("command", {"cmd": "hostname"}, host=host)
        print(f"Hostname: {result.output.get('stdout', '')}")

asyncio.run(main())
```

## Available FTL Modules

| Module | Function | Description |
|--------|----------|-------------|
| file | `ftl_file()` | Manage files and directories |
| copy | `ftl_copy()` | Copy files |
| template | `ftl_template()` | Render Jinja2 templates |
| command | `ftl_command()` | Run commands |
| shell | `ftl_shell()` | Run shell commands |
| uri | `ftl_uri()` | HTTP requests (async) |
| get_url | `ftl_get_url()` | Download files (async) |
| pip | `ftl_pip()` | Manage Python packages |

## Performance

FTL modules are significantly faster than subprocess-based execution:

| Module | FTL Time | Subprocess Time | Speedup |
|--------|----------|-----------------|---------|
| file | 0.07ms | 22.6ms | **330x** |
| uri | 0.28ms | 23.4ms | **84x** |
| command | 3.2ms | 22.9ms | **7x** |

See `benchmarks/RESULTS.md` for detailed performance data.
