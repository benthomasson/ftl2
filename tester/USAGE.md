# Gate Lifecycle Management — Usage Guide

## Overview

FTL2 permanent gates persist as SSH subsystems across connections for performance. Five lifecycle methods on `AutomationContext` let you manage them programmatically:

| Method | Purpose |
|--------|---------|
| `gate_deploy` | Install gate binary and register SSH subsystem |
| `gate_drain` | Stop accepting new work, wait for in-flight tasks |
| `gate_upgrade` | Drain + replace gate binary + reconnect |
| `gate_restart` | Drain + shutdown + reconnect (fresh process) |
| `gate_decommission` | Drain + shutdown + unregister subsystem + cleanup |

All methods accept a host name or inventory group name and return `list[dict]` with per-host results.

## Prerequisites

- FTL2 installed (`pip install ftl2`)
- SSH access to target hosts
- Root access for `gate_deploy` and `gate_decommission` (modifies sshd_config)
- An inventory with target hosts defined

## Basic Usage

```python
import asyncio
from ftl2 import automation

async def main():
    async with automation(inventory="inventory.yml") as ftl:
        # Deploy permanent gate to a host
        results = await ftl.gate_deploy("web01")
        print(results)
        # [{"host": "web01", "status": "ok", "message": "Gate deployed as SSH subsystem"}]

        # Deploy to an entire group
        results = await ftl.gate_deploy("webservers")
        # Returns one result dict per host in the group

asyncio.run(main())
```

## Draining a Gate

Drain stops the gate from accepting new module requests while letting in-flight tasks finish.

```python
async with automation(inventory="inventory.yml") as ftl:
    # Drain with custom timeout (default is 300 seconds)
    results = await ftl.gate_drain("web01", timeout_seconds=60)
    print(results)
    # [{"host": "web01", "status": "drained", "completed": 3, "in_flight": 0}]
```

After draining, the gate is still alive but rejects new `Module` and `FTLModule` requests. Follow up with `gate_restart` or `gate_decommission`.

## Upgrading Gates

Upgrade drains the existing gate, replaces the binary, and reconnects.

```python
async with automation(inventory="inventory.yml") as ftl:
    # Rolling upgrade (one at a time, stops on first failure)
    results = await ftl.gate_upgrade("webservers", strategy="rolling")

    # Parallel upgrade (all at once)
    results = await ftl.gate_upgrade("webservers", strategy="parallel")

    # Custom drain timeout during upgrade
    results = await ftl.gate_upgrade("webservers", drain_timeout=120)
```

**Rolling strategy** processes hosts sequentially and stops at the first failure. This is safe for canary deploys — if `web01` fails, `web02` and `web03` are not touched.

**Parallel strategy** upgrades all hosts concurrently. Faster, but a failure on one host doesn't prevent others from proceeding.

## Restarting Gates

Restart drains the gate, shuts it down, and reconnects with a fresh process.

```python
async with automation(inventory="inventory.yml") as ftl:
    # Graceful restart (drain first, then shutdown)
    results = await ftl.gate_restart("web01")

    # Force restart (skip drain, immediate shutdown)
    results = await ftl.gate_restart("web01", force=True)
```

Use `force=True` when the gate is in a bad state and you need to restart without waiting for in-flight work.

## Decommissioning Gates

Decommission removes the gate entirely: drain, shutdown, unregister the SSH subsystem, and optionally delete the binary.

```python
async with automation(inventory="inventory.yml") as ftl:
    # Full decommission (removes binary too)
    results = await ftl.gate_decommission("web03", cleanup=True)

    # Decommission but keep the binary on disk
    results = await ftl.gate_decommission("web03", cleanup=False)
```

Requires root access on the target host (modifies `/etc/ssh/sshd_config` and reloads sshd).

## Result Format

All lifecycle methods return `list[dict]`. Each dict has at minimum:

```python
{
    "host": "web01",       # Host name
    "status": "ok",        # "ok", "drained", or "error"
    "message": "...",      # Human-readable description
}
```

For `gate_drain`, successful results also include:

```python
{
    "host": "web01",
    "status": "drained",
    "completed": 3,        # Tasks that finished during drain
    "in_flight": 0,        # Tasks still running (0 means fully drained)
}
```

## Error Handling

Errors are captured per-host, not raised as exceptions:

```python
results = await ftl.gate_deploy("webservers")
for r in results:
    if r["status"] == "error":
        print(f"Failed on {r['host']}: {r['message']}")
    else:
        print(f"Success on {r['host']}")
```

## Common Errors

| Error | Cause | Fix |
|-------|-------|-----|
| `"Gate lifecycle methods require an active context manager"` | Called outside `async with automation()` | Wrap in context manager |
| `"Unknown host or group: ..."` | Host/group not in inventory | Check inventory file |
| `"No active gate connection"` | `gate_drain` called but no gate is cached for this host | Deploy or connect first |
| `"Not root, cannot decommission"` | `gate_decommission` needs root SSH access | Use `ansible_become: true` |
| `"Not connected — call connect() first"` | SSHHost used before `connect()` | Internal error, file a bug |

## Become (Privilege Escalation) Notes

Gates deployed with `ansible_become: true` use a composite cache key like `web01:become=root:method=sudo`. All lifecycle methods use this become-aware key, so:

- A gate deployed with become will only be found by lifecycle methods when the host has `ansible_become: true` in the inventory
- A gate deployed without become will not be found if the host later has become enabled

This is by design — become and non-become gates are separate connections.
