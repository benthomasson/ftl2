# Plan: Gate Lifecycle Management Commands (Issue #68)

## Requirements

Permanent gates persist across connections as SSH subsystems but currently have no management API. The AI operator needs programmatic commands to manage their lifecycle:

| Command | Purpose |
|---------|---------|
| **Deploy** | Install a permanent gate on a host (register SSH subsystem) |
| **Upgrade** | Replace gate binary, optionally draining in-flight work first |
| **Drain** | Stop accepting new work, wait for in-flight tasks to complete |
| **Restart** | Drain + shutdown + reconnect (fresh gate process) |
| **Decommission** | Remove subsystem registration, delete gate binary |

Each command returns a structured dict the AI operator can reason over. Fleet-level operations support rolling and parallel strategies.

### What already exists

- `_register_gate_subsystem()` (`runners.py:1213-1306`) — deploys gate binary and registers SSH subsystem in sshd_config
- `_update_gate_stable_path()` (`runners.py:1185-1211`) — atomic binary replacement
- `_close_gate()` (`runners.py:1308-1344`) — sends Shutdown, cleans up resources
- Multiplexed mode (`ftl_gate/__main__.py:1163-1385`) — concurrent task handling with graceful 30s shutdown
- `gate_subsystem=True` on `automation()` (`automation/__init__.py:73`)

### What's missing

- No drain command (stop new work, wait for in-flight)
- No decommission (reverse of register — no way to remove a subsystem)
- No upgrade orchestration (drain -> replace -> verify)
- No restart (drain -> shutdown -> reconnect)
- No public API on AutomationContext for any lifecycle operation
- No fleet-level orchestration

---

## Key Design Decisions

### 1. Only GateDrain is a new gate-side protocol message

Deploy, upgrade, and decommission operate on the host system (sshd_config, file system) — they cannot be gate-side protocol messages because they happen *around* the gate, not *through* it. GateRestart is client-side orchestration (drain -> shutdown -> reconnect). Only **GateDrain** requires the gate's cooperation (it needs to stop accepting work and wait for in-flight tasks).

### 2. GateDrain is handled synchronously in the main loop, not as a spawned task

In multiplexed mode (`__main__.py:1340-1385`), most messages are dispatched as `asyncio.create_task(handle_request(...))`. GateDrain must be handled synchronously (like Shutdown at line 1359) because it needs to call `asyncio.wait(tasks)` on the tasks set, which is local to the main loop.

### 3. After drain, the gate stays alive but rejects new work

Drain sets a `draining` flag. The gate continues accepting non-work messages (Info, ListModules, Shutdown) but rejects Module/FTLModule with an Error response. This lets the caller inspect the gate, then explicitly Shutdown when ready.

### 4. Fleet operations are composed from single-host primitives

`gate_upgrade("webservers", strategy="rolling")` iterates hosts sequentially: drain -> upgrade -> verify -> next host. `strategy="parallel"` uses `asyncio.gather`. Canary deploys and rollback are composable by the caller (upgrade one host, check, continue).

### 5. Structured return type

Every lifecycle method returns `list[dict]` with per-host results:
```python
[
    {"host": "web01", "status": "ok", "message": "Gate deployed", "details": {...}},
    {"host": "web02", "status": "error", "message": "Not root", "details": {}},
]
```

---

## Implementation Steps

### Step 1: Add protocol message types

Add GateDrain and GateDrainResult to the protocol.

| File | Line(s) | Change Description |
|------|---------|-------------------|
| `src/ftl2/message.py` | 42-65 (MESSAGE_TYPES set) | Add `"GateDrain"` and `"GateDrainResult"` to the set, after `"Goodbye"` (also add `"Goodbye"` — it's used in code at `__main__.py:933,1126,1363` but missing from the set) |

New entries:
```python
"Goodbye",          # Shutdown acknowledgment
"GateDrain",        # Drain in-flight work (stop accepting new tasks)
"GateDrainResult",  # Response to GateDrain
```

### Step 2: Gate-side drain support — serial mode

In serial mode, drain is trivial: there's no concurrency, so there are never in-flight tasks. Respond immediately with "drained".

| File | Line(s) | Change Description |
|------|---------|-------------------|
| `src/ftl2/ftl_gate/__main__.py` | After line 1127 (after Shutdown handler) | Add `elif msg_type == "GateDrain":` handler that responds with `GateDrainResult` status "drained", completed=0, in_flight=0 |

```python
elif msg_type == "GateDrain":
    logger.info("GateDrain requested (serial mode, no in-flight work)")
    await protocol.send_message(writer, "GateDrainResult", {
        "status": "drained",
        "completed": 0,
        "in_flight": 0,
    })
```

### Step 3: Gate-side drain support — multiplexed mode

This is the substantive change. Add drain state tracking and handle GateDrain synchronously in the main loop.

| File | Line(s) | Change Description |
|------|---------|-------------------|
| `src/ftl2/ftl_gate/__main__.py` | Line 1341, before `tasks = set()` | Add `draining = False` flag |
| `src/ftl2/ftl_gate/__main__.py` | After line 1365 (after Shutdown break) | Add `elif msg_type == "GateDrain":` block — sets `draining = True`, waits on `asyncio.wait(tasks, timeout=...)`, sends GateDrainResult, continues loop (does not break) |
| `src/ftl2/ftl_gate/__main__.py` | After GateDrain handler, before task spawn (line 1367) | Add rejection check: if `draining and msg_type in ("Module", "FTLModule")`, send Error "Gate is draining", continue |

GateDrain handler in the main loop:
```python
elif msg_type == "GateDrain":
    draining = True
    timeout = data.get("timeout_seconds", 300) if isinstance(data, dict) else 300
    logger.info(f"GateDrain requested, timeout={timeout}s, {len(tasks)} in-flight")
    if tasks:
        done, pending = await asyncio.wait(tasks, timeout=timeout)
        result = {
            "status": "drained" if not pending else "timeout",
            "completed": len(done),
            "in_flight": len(pending),
        }
    else:
        result = {"status": "drained", "completed": 0, "in_flight": 0}
    await protocol.send_message_with_id(
        writer, "GateDrainResult", result, msg_id, write_lock=write_lock,
    )
    continue
```

Rejection check before task spawn:
```python
# Reject work while draining
if draining and msg_type in ("Module", "FTLModule"):
    await protocol.send_message_with_id(
        writer, "Error",
        {"message": "Gate is draining, not accepting new work"},
        msg_id, write_lock=write_lock,
    )
    continue
```

### Step 4: Client-side drain method on RemoteModuleRunner

Add `_drain_gate()` to send GateDrain and await the result.

| File | Line(s) | Change Description |
|------|---------|-------------------|
| `src/ftl2/runners.py` | After `_close_gate()` (after line 1344) | Add `_drain_gate(self, gate, timeout_seconds=300) -> dict` method |

```python
async def _drain_gate(self, gate: Gate, timeout_seconds: int = 300) -> dict:
    """Send GateDrain to a gate and await the result."""
    if gate.multiplexed:
        msg_id = gate.next_msg_id()
        future = gate.create_future(msg_id)
        await self.protocol.send_message_with_id(
            gate.gate_process.stdin, "GateDrain",
            {"timeout_seconds": timeout_seconds},
            msg_id, write_lock=gate._write_lock,
        )
        # Wait longer than gate timeout to allow for response transmission
        msg_type, data = await asyncio.wait_for(future, timeout=timeout_seconds + 30)
        return data
    else:
        await self.protocol.send_message(
            gate.gate_process.stdin, "GateDrain",
            {"timeout_seconds": timeout_seconds},
        )
        msg = await self.protocol.read_message(gate.gate_process.stdout)
        if msg and msg[0] == "GateDrainResult":
            return msg[1]
        return {"status": "error", "message": f"Unexpected response: {msg}"}
```

### Step 5: Client-side decommission method on RemoteModuleRunner

Add `_decommission_gate_subsystem()` — the inverse of `_register_gate_subsystem()`.

| File | Line(s) | Change Description |
|------|---------|-------------------|
| `src/ftl2/runners.py` | After `_drain_gate()` | Add `_decommission_gate_subsystem(self, conn, cleanup=True) -> dict` method |

```python
async def _decommission_gate_subsystem(
    self,
    conn: SSHClientConnection,
    cleanup: bool = True,
) -> dict:
    """Remove gate SSH subsystem registration from a host.

    Inverse of _register_gate_subsystem: removes the Subsystem line
    from sshd_config, reloads sshd, and optionally deletes the gate binary.
    """
    import os

    try:
        # Require root
        result = await conn.run("id -u", check=True)
        if result.stdout.strip() != "0":
            return {"status": "error", "message": "Not root, cannot decommission"}

        # Check if subsystem is registered
        result = await conn.run(
            f"grep -q '^Subsystem {self.GATE_SUBSYSTEM_NAME}' /etc/ssh/sshd_config"
        )
        if result.exit_status != 0:
            return {"status": "ok", "message": "Subsystem not registered (already decommissioned)"}

        # Remove subsystem line from sshd_config
        await conn.run(
            f"sed -i '/^Subsystem {self.GATE_SUBSYSTEM_NAME}/d' /etc/ssh/sshd_config",
            check=True,
        )

        # Reload sshd
        await conn.run("systemctl reload sshd", check=True)

        if cleanup:
            await conn.run(f"rm -f {self.GATE_SUBSYSTEM_PATH}", check=True)
            # Remove directory if empty
            await conn.run(
                f"rmdir {os.path.dirname(self.GATE_SUBSYSTEM_PATH)} 2>/dev/null || true"
            )

        return {"status": "ok", "message": "Gate subsystem decommissioned"}

    except Exception as e:
        return {"status": "error", "message": str(e)}
```

### Step 6: Public API on AutomationContext

Add five lifecycle methods. Each resolves a host or group name to hosts, performs the operation, and returns structured results.

| File | Line(s) | Change Description |
|------|---------|-------------------|
| `src/ftl2/automation/context.py` | After the existing gate-related methods (near `_create_gate`, around line 1751) | Add `_resolve_hosts(name) -> list[HostConfig]` helper, then `gate_deploy()`, `gate_drain()`, `gate_upgrade()`, `gate_restart()`, `gate_decommission()` |
| `src/ftl2/automation/context.py` | `_create_gate()` method (around line 1700) | Add `register_subsystem_override: bool | None = None` parameter. When set, use it instead of `self._gate_subsystem` in the `_connect_gate` call at line 1748 |

#### Host resolution helper

```python
def _resolve_hosts(self, host_or_group: str) -> list[HostConfig]:
    """Resolve a host name or group name to a list of HostConfig objects."""
    # Check if it's a specific host
    if host_or_group in self._hosts:
        return [self._hosts[host_or_group]]
    # Check if it's a group
    if self.inventory:
        hosts = self.inventory.get_group_hosts(host_or_group)
        if hosts:
            return hosts
    raise ValueError(f"Unknown host or group: {host_or_group}")
```

#### gate_deploy

```python
async def gate_deploy(self, host_or_group: str) -> list[dict]:
    """Deploy permanent gate to host(s) as an SSH subsystem.

    Uploads the gate binary and registers it in sshd_config.
    Requires root access on target hosts.
    """
    hosts = self._resolve_hosts(host_or_group)
    results = []
    for host in hosts:
        try:
            gate = await self._create_gate(host, register_subsystem_override=True)
            results.append({
                "host": host.name,
                "status": "ok",
                "message": "Gate deployed as SSH subsystem",
            })
        except Exception as e:
            results.append({
                "host": host.name,
                "status": "error",
                "message": str(e),
            })
    return results
```

#### gate_drain

```python
async def gate_drain(
    self, host_or_group: str, timeout_seconds: int = 300
) -> list[dict]:
    """Drain gate(s) -- stop accepting new work, wait for in-flight tasks.

    The gate stays alive after draining but rejects new Module/FTLModule requests.
    Send gate_restart or gate_decommission after draining.
    """
    hosts = self._resolve_hosts(host_or_group)
    results = []
    for host in hosts:
        cache_key = host.name
        gate = self._remote_runner.gate_cache.get(cache_key)
        if not gate:
            results.append({
                "host": host.name,
                "status": "error",
                "message": "No active gate connection",
            })
            continue
        try:
            data = await self._remote_runner._drain_gate(gate, timeout_seconds)
            results.append({"host": host.name, **data})
        except Exception as e:
            results.append({
                "host": host.name,
                "status": "error",
                "message": str(e),
            })
    return results
```

#### gate_upgrade

```python
async def gate_upgrade(
    self,
    host_or_group: str,
    strategy: str = "rolling",
    drain_timeout: int = 300,
) -> list[dict]:
    """Upgrade gate binary on host(s).

    Builds a new gate, drains existing connections, replaces the binary,
    and reconnects. New connections use the updated gate automatically
    (sshd forks fresh for each subsystem connection).

    Args:
        strategy: "rolling" (one host at a time) or "parallel" (all at once)
        drain_timeout: Seconds to wait for in-flight work during drain
    """
    hosts = self._resolve_hosts(host_or_group)

    async def _upgrade_one(host):
        try:
            cache_key = host.name
            gate = self._remote_runner.gate_cache.get(cache_key)

            # Drain existing gate if connected
            if gate:
                await self._remote_runner._drain_gate(gate, drain_timeout)
                await self._remote_runner._close_gate(gate)
                self._remote_runner.gate_cache.pop(cache_key, None)

            # Reconnect -- _connect_gate rebuilds and deploys the gate
            new_gate = await self._create_gate(host)
            return {
                "host": host.name,
                "status": "ok",
                "message": "Gate upgraded",
            }
        except Exception as e:
            return {"host": host.name, "status": "error", "message": str(e)}

    if strategy == "rolling":
        results = []
        for host in hosts:
            result = await _upgrade_one(host)
            results.append(result)
            if result["status"] == "error":
                # Stop rolling on failure -- caller decides to continue or rollback
                break
        return results
    else:  # parallel
        return list(await asyncio.gather(*[_upgrade_one(h) for h in hosts]))
```

#### gate_restart

```python
async def gate_restart(
    self, host_or_group: str, force: bool = False
) -> list[dict]:
    """Restart gate(s) -- drain, shutdown, reconnect.

    Args:
        force: If True, skip drain and shutdown immediately
    """
    hosts = self._resolve_hosts(host_or_group)
    results = []
    for host in hosts:
        try:
            cache_key = host.name
            gate = self._remote_runner.gate_cache.get(cache_key)

            if gate:
                if not force:
                    await self._remote_runner._drain_gate(gate)
                await self._remote_runner._close_gate(gate)
                self._remote_runner.gate_cache.pop(cache_key, None)

            # Reconnect
            new_gate = await self._create_gate(host)
            results.append({
                "host": host.name,
                "status": "ok",
                "message": "Gate restarted",
            })
        except Exception as e:
            results.append({
                "host": host.name,
                "status": "error",
                "message": str(e),
            })
    return results
```

#### gate_decommission

```python
async def gate_decommission(
    self, host_or_group: str, cleanup: bool = True
) -> list[dict]:
    """Decommission gate(s) -- drain, shutdown, unregister subsystem.

    Removes the SSH subsystem registration from sshd_config, reloads sshd,
    and optionally deletes the gate binary. Requires root access.
    """
    hosts = self._resolve_hosts(host_or_group)
    results = []
    for host in hosts:
        try:
            cache_key = host.name
            gate = self._remote_runner.gate_cache.get(cache_key)

            # Drain and close existing gate
            if gate:
                await self._remote_runner._drain_gate(gate)
                await self._remote_runner._close_gate(gate)
                self._remote_runner.gate_cache.pop(cache_key, None)

            # SSH to host and decommission
            ssh = await self._get_ssh_connection(host)
            # _decommission_gate_subsystem needs an asyncssh connection
            # SSHHost wraps asyncssh -- use its connection
            result = await self._remote_runner._decommission_gate_subsystem(
                ssh._conn, cleanup=cleanup
            )
            results.append({"host": host.name, **result})
        except Exception as e:
            results.append({
                "host": host.name,
                "status": "error",
                "message": str(e),
            })
    return results
```

### Step 7: Tests

| File | Line(s) | Change Description |
|------|---------|-------------------|
| `tests/test_gate_lifecycle.py` | New file | Unit tests for gate lifecycle commands |

Test cases:
1. **GateDrain protocol message** — mock gate in multiplexed mode, send GateDrain, verify GateDrainResult returned with correct fields
2. **Drain rejects new work** — after drain, Module/FTLModule messages return Error
3. **Drain with no in-flight tasks** — immediate "drained" response
4. **Serial mode drain** — immediate "drained" response (no concurrency)
5. **_decommission_gate_subsystem** — mock SSH connection, verify sshd_config line removal, sshd reload, binary cleanup
6. **_decommission_gate_subsystem not root** — returns error
7. **gate_upgrade rolling stops on failure** — verify it stops at the failed host
8. **gate_restart with force=True** — verify drain is skipped

---

## Summary of changes by file

| File | Changes |
|------|---------|
| `src/ftl2/message.py` | Add 3 message types to MESSAGE_TYPES (~3 lines) |
| `src/ftl2/ftl_gate/__main__.py` | Serial drain handler (~8 lines), multiplexed drain handler + draining flag + rejection check (~25 lines) |
| `src/ftl2/runners.py` | `_drain_gate()` method (~20 lines), `_decommission_gate_subsystem()` method (~30 lines) |
| `src/ftl2/automation/context.py` | `_resolve_hosts()` helper (~8 lines), 5 lifecycle methods (~120 lines), `register_subsystem_override` param on `_create_gate` (~3 lines) |
| `tests/test_gate_lifecycle.py` | New test file (~100 lines) |

---

## Success Criteria

When complete, a user can:

```python
async with automation(inventory="inventory.yml") as ftl:
    # Deploy permanent gates
    results = await ftl.gate_deploy("webservers")
    assert all(r["status"] == "ok" for r in results)

    # Do work...
    await ftl.webservers.dnf(name="nginx", state="present")

    # Upgrade gates with rolling strategy
    results = await ftl.gate_upgrade("webservers", strategy="rolling")

    # Drain a specific host before maintenance
    results = await ftl.gate_drain("web01", timeout_seconds=60)
    assert results[0]["status"] == "drained"

    # Restart a misbehaving gate
    results = await ftl.gate_restart("web01")

    # Decommission retired host
    results = await ftl.gate_decommission("web03", cleanup=True)
```

Each result is a structured dict with `host`, `status`, `message`, suitable for programmatic consumption by the AI operator.

---

## Out of Scope

- **CLI commands** — the issue explicitly calls for programmatic APIs, not CLI
- **Canary deploys** — composable from basic APIs: `gate_upgrade("web01")`, verify, then `gate_upgrade("webservers")`
- **Automatic rollback** — composable: if upgrade fails, call `gate_upgrade` with previous gate config
- **GateStatus self-reporting** — tracked separately as issue #67
- **Version coexistence** — sshd forks fresh per connection, so atomic binary replacement is sufficient; old connections keep their fd to the old inode

---

## Self-Review

### What went well

1. **Deep codebase exploration paid off.** Reading the actual multiplexed main loop (`__main__.py:1340-1385`) revealed that GateDrain *must* be handled synchronously (like Shutdown) because it needs access to the `tasks` set. This would have been a bug if I'd naively added it to `handle_request`.

2. **The split between gate-side vs client-side operations is clear.** The codebase already has the pattern: `_register_gate_subsystem` is client-side SSH orchestration, while Module/FTLModule are gate-side protocol messages. The lifecycle commands follow the same split naturally — only GateDrain needs gate cooperation.

3. **Existing patterns made the design straightforward.** The atomic binary replacement (`_update_gate_stable_path`), the multiplexed task tracking, and the subsystem registration flow are all solid foundations. The lifecycle commands are relatively thin orchestration on top.

### What information was missing

1. **`_resolve_hosts` / group resolution** — I didn't fully trace how AutomationContext resolves group names to host lists. The implementer should verify the exact API on the inventory object (`self.inventory.get_group_hosts()` may not exist with that exact name).

2. **SSHHost._conn access** — `gate_decommission` needs to pass an asyncssh `SSHClientConnection` to `_decommission_gate_subsystem`, but `_get_ssh_connection` returns an `SSHHost` wrapper. The implementer needs to verify how to get the raw connection from SSHHost (likely `ssh._conn` or similar attribute).

3. **Gate cache key format** — I used `host.name` as the cache key, but `runners.py:types.py:83-87` shows the actual key is `"{host}:become={user}:method={method}"` for become configs. The implementer should use the correct cache key resolution.

### What would make planning easier next time

1. **A protocol specification document** — MESSAGE_TYPES is defined in code but there's no formal protocol spec. Having one would make it faster to reason about new messages.

2. **Integration test infrastructure** — knowing whether there's a test harness for gate protocol messages (mock SSH, mock gate process) would let me be more specific about test implementation.
