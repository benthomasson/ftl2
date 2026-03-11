# CLAUDE.md

## What This Is

FTL2 is a Python automation framework that runs Ansible modules in-process instead of as subprocesses. It provides an `async with automation()` context manager that gives Python scripts direct access to the entire Ansible module ecosystem — 3-17x faster than `ansible-playbook`.

## Installation

```bash
pip install ftl2
```

Or use `uv run` with PEP 723 inline metadata (no install needed):
```python
#!/usr/bin/env python3
# /// script
# dependencies = ["ftl2"]
# requires-python = ">=3.13"
# ///
```

Then run directly:
```bash
uv run my_script.py
```

## The Pattern

```python
import asyncio
from ftl2 import automation

async def main():
    async with automation(
        secret_bindings={
            "community.general.linode_v4": {
                "access_token": "LINODE_TOKEN",
                "root_pass": "LINODE_ROOT_PASS",
            },
        }
    ) as ftl:
        await ftl.file(path="/tmp/test", state="directory")

if __name__ == "__main__":
    asyncio.run(main())
```

## Module Names

Use the same Ansible module names and parameters:

```python
# Short names for builtin modules
await ftl.file(path="/tmp/test", state="touch")
await ftl.copy(src="config.yml", dest="/etc/app/config.yml")
await ftl.command(cmd="echo hello")
await ftl.service(name="nginx", state="restarted")
await ftl.template(src="app.conf.j2", dest="/etc/app.conf")

# FQCN for collection modules
await ftl.community.general.linode_v4(label="web01", type="g6-standard-1", ...)
await ftl.community.general.slack(channel="#ops", msg="Done!")
await ftl.ansible.posix.authorized_key(user="ben", key=ssh_key)
await ftl.ansible.posix.firewalld(port="80/tcp", state="enabled")
```

## Secret Bindings

Secrets are configured once and injected automatically:

```python
async with automation(
    secret_bindings={
        "community.general.linode_v4": {
            "access_token": "LINODE_TOKEN",
            "root_pass": "LINODE_ROOT_PASS",
        },
        "community.general.slack": {"token": "SLACK_TOKEN"},
        "uri": {"bearer_token": "API_TOKEN"},
    }
) as ftl:
    # No credentials in the code - injected from environment
    await ftl.local.community.general.linode_v4(label="web01", ...)

    # bearer_token injected automatically, redacted in audit logs
    await ftl.local.uri(
        url="https://api.example.com/data",
        body={"key": "value"},
        body_format="json",
    )
```

## Targeting Hosts and Groups

```python
async with automation(inventory="inventory.yml") as ftl:
    # Local execution (for cloud/API modules)
    await ftl.local.community.general.linode_v4(label="web01", ...)

    # Target a group
    await ftl.webservers.service(name="nginx", state="restarted")

    # Target a specific host
    await ftl.db01.command(cmd="pg_dump mydb")
```

## State File Tracking

```python
async with automation(state_file=".ftl2-state.json") as ftl:
    if ftl.state.has("web01"):
        resource = ftl.state.get("web01")
        print(f"Server exists: {resource['ipv4'][0]}")
    else:
        server = await ftl.local.community.general.linode_v4(label="web01", ...)
        ftl.state.add("web01", {
            "provider": "linode",
            "id": server["instance"]["id"],
            "ipv4": server["instance"]["ipv4"],
        })
        ftl.add_host(
            hostname="web01",
            ansible_host=server["instance"]["ipv4"][0],
            ansible_user="root",
            groups=["webservers"],
        )
```

State operations:
```python
ftl.state.has("web01")          # Check existence
ftl.state.get("web01")          # Get resource dict
ftl.state.add("web01", {...})   # Add resource (persists immediately)
ftl.state.remove("web01")       # Remove resource
ftl.state.resources()           # List all resource names
ftl.state.hosts()               # List all host names
```

## Return Types

**Local execution** returns a `dict`:
```python
server = await ftl.local.community.general.linode_v4(label="web01", ...)
ip = server["instance"]["ipv4"][0]
```

**Remote execution** returns `list[ExecuteResult]`:
```python
results = await ftl.webservers.command(cmd="uptime")
for result in results:
    print(f"{result.host}: {result.output}")
```

## Safety Features

```python
# Check mode - preview without executing
async with automation(check_mode=True) as ftl:
    await ftl.file(path="/etc/important", state="absent")

# Fail fast - stop on first error
async with automation(fail_fast=True) as ftl:
    await ftl.file(...)
```

## Gate Modules (Pre-built Remote Execution)

```python
async with automation(
    gate_modules="auto",      # Read from .ftl2-modules.txt, or record on first run
    record_deps=True,         # Record modules to .ftl2-modules.txt
) as ftl:
    await ftl.webservers.dnf(name="nginx", state="present")
```

First run records modules; subsequent runs bake them into the gate for faster execution.

## Audit Recording

```python
async with automation(record="audit.json") as ftl:
    await ftl.file(path="/tmp/test", state="directory")
# Writes audit.json with all actions, timestamps, durations
# Secret-injected params are excluded
```

## Common Gotchas

### Bootstrap python3-dnf on Fedora
```python
await host.command(cmd="dnf install -y python3-dnf")  # Before any dnf calls
await host.dnf(name="nginx", state="present")
```

### `user` module: `group` vs `groups`
```python
# WRONG - changes primary group
await host.user(name="ben", group="wheel")
# RIGHT - adds supplementary group
await host.user(name="ben", groups=["wheel"])
```

### Some modules require FQCN
```python
# WRONG - not in ansible.builtin
await host.authorized_key(user="ben", key=ssh_key)
# RIGHT
await host.ansible.posix.authorized_key(user="ben", key=ssh_key)
```

| Module | FQCN |
|--------|------|
| `authorized_key` | `ansible.posix.authorized_key` |
| `firewalld` | `ansible.posix.firewalld` |
| `slack` | `community.general.slack` |
| `linode_v4` | `community.general.linode_v4` |

### `swap` module: string size
```python
await host.swap(path="/swapfile", size="1G")  # String, not int
```

### No Jinja2 or Lookup Plugins
```python
# WRONG
key="{{ lookup('file', '~/.ssh/id_rsa.pub') }}"
# RIGHT
from pathlib import Path
key = (Path.home() / ".ssh" / "id_rsa.pub").read_text().strip()
```

### .gitignore
```
.ftl2-state.json
.ftl2-deps.txt
.ftl2-modules.txt
*.pyz
audit.json
```

---

## Project Structure (for developers)

```
src/ftl2/
    automation/         # AutomationContext, ModuleProxy — the async with automation() API
        context.py      # Main context manager, execute(), secret_bindings, record
        proxy.py        # Host/group proxy for ftl.webservers.dnf() syntax
        __init__.py     # automation() wrapper function
    ftl_modules/        # FTL-native module implementations (in-process, no subprocess)
        http.py         # ftl_uri, ftl_get_url (httpx-based)
        executor.py     # ExecuteResult dataclass, FTL module dispatcher
        swap.py, pip.py # Other native modules
    ftl_gate/           # Remote execution gate (.pyz zipapp)
        __main__.py     # Gate-side: receives modules over stdin, executes, returns results
    state/              # State management
        state.py        # State class for .ftl2-state.json
        execution.py    # ExecutionState for CLI run tracking
    gate.py             # Gate builder — creates .pyz with baked-in modules
    runners.py          # SSH connection, gate deployment, remote module execution
    cli.py              # Click CLI (ftl2 command)
    ssh.py              # SSH host abstraction
    inventory.py        # Inventory loading (YAML)
    builder.py          # ftl-gate-builder entry point
```

## Key Abstractions

- **AutomationContext** (`automation/context.py`) — the core. Manages inventory, secrets, module execution, state, and recording
- **ModuleProxy** (`automation/proxy.py`) — translates `ftl.webservers.dnf()` into `context.execute("dnf", hosts, params)`
- **Gate** (`gate.py` + `ftl_gate/`) — .pyz zipapp deployed to remote hosts for module execution
- **ExecuteResult** (`ftl_modules/executor.py`) — dataclass returned from every module call

## How Module Execution Works

1. Script calls `await ftl.webservers.dnf(name="nginx", state="present")`
2. ModuleProxy resolves `webservers` to a host group and `dnf` to a module name
3. AutomationContext injects secret_bindings, captures original params for audit
4. For local: module runs in-process via Ansible's module machinery
5. For remote: module is sent to the gate over SSH stdin, gate executes and returns result
6. Result is stored in `_results` list for audit recording

## Development Commands

```bash
pytest                          # Run tests
pytest tests/test_automation.py # Run specific test
ruff check src/                 # Lint
ruff format src/                # Format
mypy src/ftl2                   # Type check
```

## Dependencies

- `asyncssh` — SSH connections
- `httpx` — async HTTP (used by ftl_uri)
- `click` — CLI
- `pyyaml` — inventory parsing
- `jinja2` — template module
- `rich` — CLI output
- `ftl-module-utils` — Ansible module_utils extracted for standalone use
- `ftl-builtin-modules` — Ansible builtin modules extracted
- `ftl-collections` — Community collection module_utils (community.general, amazon.aws, etc.)
