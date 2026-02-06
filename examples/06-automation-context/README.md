# Automation Context Manager Examples

This directory demonstrates FTL2's automation context manager interface - a clean, AI-friendly way to write automation scripts.

## Overview

The automation context manager provides an intuitive interface:

```python
from ftl2 import automation

async with automation() as ftl:
    await ftl.file(path="/tmp/test", state="directory")
    await ftl.copy(src="config.yml", dest="/etc/app/config.yml")
    await ftl.command(cmd="systemctl restart myapp")
```

## Examples by Phase

| Example | Phase | Description |
|---------|-------|-------------|
| `example_phase1_basic.py` | 1 | Core context manager with ftl.module_name() syntax |
| `example_phase2_inventory.py` | 2 | Inventory integration with ftl.hosts and run_on() |
| `example_phase3_secrets.py` | 3 | Secrets management with ftl.secrets |
| `example_phase4_check_mode.py` | 4 | Check mode (dry run) for validation |
| `example_phase5_output.py` | 5 | Output modes and event callbacks |
| `example_phase6_error_handling.py` | 6 | Error handling with ftl.failed and ftl.errors |
| `example_fqcn_modules.py` | - | FQCN collection modules (amazon.aws.ec2_instance) |

## Quick Start

```bash
# Run Phase 1 examples
uv run python example_phase1_basic.py
```

## Phase 1: Core Context Manager

The foundation - clean module access via attributes:

```python
async with automation() as ftl:
    # Access any module as an attribute
    await ftl.file(path="/tmp/test", state="touch")
    await ftl.copy(src="a.txt", dest="b.txt")
    await ftl.command(cmd="echo hello")
```

### Features

**Module Restriction:**
```python
async with automation(modules=["file", "copy"]) as ftl:
    await ftl.file(...)  # OK
    await ftl.command(...)  # Raises AttributeError
```

**Verbose Mode:**
```python
async with automation(verbose=True) as ftl:
    await ftl.file(path="/tmp/test", state="touch")
    # Output: [file] ok (changed)
```

**Result Tracking:**
```python
async with automation() as ftl:
    await ftl.file(path="/tmp/a", state="touch")
    await ftl.file(path="/tmp/b", state="touch")

    print(f"Executed {len(ftl.results)} modules")
    for r in ftl.results:
        print(f"  {r.module}: success={r.success}")
```

## Why This Interface?

1. **AI-Friendly**: Natural language patterns that AI generates easily
2. **Clean Syntax**: No boilerplate, just `await ftl.module_name()`
3. **250x Faster**: FTL modules run in-process, not as subprocesses
4. **Type-Safe**: Full IDE autocomplete and type checking support
5. **Pythonic**: Uses standard async context managers

## Phase 2: Inventory Integration

Load inventory and execute on remote hosts:

```python
# From YAML file
async with automation(inventory="hosts.yml") as ftl:
    # Access hosts
    print(ftl.hosts.groups)        # ['webservers', 'databases']
    print(ftl.hosts["webservers"]) # [HostConfig(...), HostConfig(...)]

    # Run on specific host/group
    results = await ftl.run_on("webservers", "file", path="/var/www", state="directory")

    # Run on host list
    results = await ftl.run_on(ftl.hosts["db01"], "command", cmd="pg_dump mydb")
```

**From dictionary:**
```python
inventory = {
    "webservers": {
        "hosts": {
            "web01": {"ansible_host": "192.168.1.10"},
            "web02": {"ansible_host": "192.168.1.11"},
        }
    }
}
async with automation(inventory=inventory) as ftl:
    await ftl.run_on("webservers", "service", name="nginx", state="restarted")
```

**Host access:**
```python
ftl.hosts["web01"]       # Get specific host
ftl.hosts["webservers"]  # Get all hosts in group
ftl.hosts.all            # Get all hosts
ftl.hosts.groups         # Get group names
ftl.hosts.keys()         # Get all host names
len(ftl.hosts)           # Number of hosts
"web01" in ftl.hosts     # Check if host exists
```

## Phase 3: Secrets Management

Secure access to sensitive configuration from environment variables:

```python
async with automation(secrets=["AWS_ACCESS_KEY_ID", "API_TOKEN"]) as ftl:
    # Access secrets securely
    key = ftl.secrets["AWS_ACCESS_KEY_ID"]

    # Check if secret exists
    if "API_TOKEN" in ftl.secrets:
        token = ftl.secrets["API_TOKEN"]

    # Get with default
    region = ftl.secrets.get("AWS_REGION", "us-east-1")

    # List requested vs loaded secrets
    print(ftl.secrets.keys())        # All requested names
    print(ftl.secrets.loaded_keys()) # Only those that were set
```

**Safety features:**
```python
# Secret values are NEVER exposed
print(ftl.secrets)  # <SecretsProxy: 2 secrets loaded>
repr(ftl.secrets)   # SecretsProxy(loaded=['KEY1'], missing=['KEY2'])

# Clear error messages
ftl.secrets["NOT_REQUESTED"]  # KeyError: not requested
ftl.secrets["NOT_SET"]        # KeyError: not set in environment
```

## Phase 4: Check Mode (Dry Run)

Preview changes without making them:

```python
async with automation(check_mode=True) as ftl:
    # Modules run but report what WOULD change
    await ftl.file(path="/tmp/test", state="directory")
    # Output: [file] ok (changed) [CHECK MODE]
```

**With verbose output:**
```python
async with automation(check_mode=True, verbose=True) as ftl:
    await ftl.file(path="/tmp/test", state="touch")
    # [file] ok (changed) [CHECK MODE]
```

**Validation workflow:**
```python
# Phase 1: Validate
async with automation(check_mode=True) as ftl:
    await ftl.file(path="/etc/app", state="directory")
    await ftl.copy(src="config.yml", dest="/etc/app/config.yml")

    if any(not r.success for r in ftl.results):
        print("Validation failed!")
        return

# Phase 2: Execute for real
async with automation(check_mode=False) as ftl:
    # ... same operations
```

## Phase 5: Progress and Output

Control output modes and receive structured events:

**Verbose mode with timing:**
```python
async with automation(verbose=True) as ftl:
    await ftl.file(path="/tmp/test", state="touch")
    # [file] ok (changed) (0.02s)
```

**Quiet mode for scripts:**
```python
async with automation(quiet=True) as ftl:
    await ftl.file(path="/tmp/test", state="touch")
    # No output - check ftl.results programmatically

    success = all(r.success for r in ftl.results)
```

**Event callback for custom handling:**
```python
events = []
async with automation(on_event=events.append) as ftl:
    await ftl.file(path="/tmp/test", state="touch")

for event in events:
    if event["event"] == "module_complete":
        print(f"{event['module']}: {event['duration']:.3f}s")
```

**Output modes:**
```python
ftl.output_mode  # OutputMode.NORMAL, VERBOSE, QUIET, or EVENTS
```

## Phase 6: Error Handling

Check for failures and inspect errors:

```python
async with automation() as ftl:
    await ftl.file(path="/tmp/test", state="touch")
    await ftl.command(cmd="echo hello")

    # Check for any failures
    if ftl.failed:
        print(f"Errors: {len(ftl.errors)}")
        for error in ftl.errors:
            print(f"  [{error.host}:{error.module}] {error.error}")
```

**Error messages:**
```python
for msg in ftl.error_messages:
    print(f"Error: {msg}")
```

**Fail fast mode:**
```python
from ftl2.automation import AutomationError

try:
    async with automation(fail_fast=True) as ftl:
        await ftl.file(path="/nonexistent", state="touch")
        # Raises AutomationError on failure
except AutomationError as e:
    print(f"Failed: {e}")
    print(f"Module: {e.result.module}")
```

## FQCN Collection Modules

Access Ansible collection modules with dotted notation:

```python
async with automation() as ftl:
    # Simple modules (FTL native, 250x faster)
    await ftl.file(path="/tmp/test", state="touch")

    # FQCN modules (Ansible collections)
    await ftl.amazon.aws.ec2_instance(
        name="my-instance",
        instance_type="t3.micro",
    )

    await ftl.community.general.slack(
        channel="#deployments",
        msg="Deployment complete!",
    )

    await ftl.ansible.builtin.debug(msg="Hello!")
```

How it works:
- `ftl.amazon` returns a `NamespaceProxy("amazon")`
- `.aws` chains to `NamespaceProxy("amazon.aws")`
- `.ec2_instance(...)` executes `"amazon.aws.ec2_instance"`

## All Phases Complete

The automation context manager now provides:
- Phase 1: Clean `ftl.module_name()` syntax
- Phase 2: Inventory integration with `ftl.hosts` and `ftl.run_on()`
- Phase 3: Secrets management with `ftl.secrets`
- Phase 4: Check mode (dry run)
- Phase 5: Output modes and event callbacks
- Phase 6: Error handling with `ftl.failed` and `ftl.errors`
