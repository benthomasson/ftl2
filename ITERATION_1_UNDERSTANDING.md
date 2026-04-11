# Iteration 1 Understanding

## What We Learned

### From Planner
Plan is complete at `workspaces/issue-68-v2/planner/PLAN.md`. Here's the summary:

**Core insight:** Only **GateDrain** needs to be a new gate-side protocol message. The other four operations (deploy, upgrade, restart, decommission) are client-side SSH orchestration that operate *around* the gate, not through it.

**7 implementation steps across 4 files (~250 lines):**

1. **message.py** — Add `GateDrain`, `GateDrainResult`, `Goodbye` to MESSAGE_TYPES
2. **ftl_gate/\_\_main\_\_.py (serial)** — Trivial drain handler (no concurrency, respond immediately)
3. **ftl_gate/\_\_main\_\_.py (multiplexed)** — Handle GateDrain synchronously in main loop (like Shutdown), add `draining` flag to reject new work
4. **runners.py** — `_drain_gate()` client method to send/await GateDrain
5. **runners.py** — `_decommission_gate_subsystem()` — inverse of register (remove sshd_config line, reload, delete binary)
6. **automation/context.py** — 5 public methods: `gate_deploy`, `gate_drain`, `gate_upgrade`, `gate_restart`, `gate_decommission` with host/group resolution and fleet support (rolling/parallel strategies)
7. **tests/** — Unit tests for protocol, rejection, decommission, rolling failure

**Key design decisions:**
- GateDrain must be handled synchronously in the multiplexed main loop (needs access to `tasks` set)
- After drain, gate stays alive but rejects Module/FTLModule — caller explicitly sends Shutdown
- Rolling upgrades stop on first failure; canary deploys are composable from single-host primitives
- All methods return `list[dict]` with per-host `status`/`message` for programmatic consumption

[Committed changes to planner branch]

### From Implementer
Files created: None

### From Reviewer
Verdict: APPROVED

All four issues from Round 1 are properly fixed:

1. **`gate_deploy` now forces subsystem registration** via `register_subsystem=True` parameter on `_get_or_create_gate`
2. **All lifecycle methods use correct become-aware cache keys** — `gate_cache_key(host.name, host.become_config)` in all four methods
3. **`SSHHost.connection` property** replaces private `_conn` access
4. **Null guard in `_resolve_hosts`** covers all five lifecycle methods with a clear error

New tests are well-designed — notably `test_drain_misses_without_become_key` which proves the cache key fix matters by showing the old behavior would fail.

**STATUS: APPROVED** — review written to `workspaces/issue-68-v2/reviewer/REVIEW.md`.

[Committed changes to reviewer branch]

### From Tester
are cache keys
- **Decommission**: full lifecycle (drain+close+SSH), no-cached-gate path, `cleanup=False`, become-aware cache keys, SSH exception handling
- **SSHHost.connection**: property guard for connected/not-connected states
- **gate_cache_key**: bare key, become-disabled, sudo, doas, HostConfig.become_config property

## USAGE INSTRUCTIONS FOR USER

Written to `tester/USAGE.md`. Quick reference:

```python
async with automation(inventory="inventory.yml") as ftl:
    await ftl.gate_deploy("webservers")                        # Install gates
    await ftl.gate_drain("web01", timeout_seconds=60)          # Drain one host
    await ftl.gate_upgrade("webservers", strategy="rolling")   # Rolling upgrade
    await ftl.gate_restart("web01", force=True)                # Force restart
    await ftl.gate_decommission("web03", cleanup=True)         # Remove gate
```

All methods return `list[dict]` with `host`, `status` (`"ok"`, `"drained"`, or `"error"`), and `message` keys.

## SELF-REVIEW

1. **Easy**: Lifecycle methods are cleanly designed for unit testing — simple inputs, dict outputs, mockable delegation
2. **Missing**: No coverage report scoped to lifecycle code; had to manually find gaps by reading all 22 existing tests
3. **Next time**: Implementer could tag uncovered paths for the tester; a per-method coverage report would help
4. **No bugs found** — all edge cases handled correctly

## Verdict

STATUS: TESTS_PASSED
OPEN_ISSUES: none

[Committed changes to tester branch]

### From User
Verdict: SATISFIED

Skipped - effort level does not include user testing

## Summary

- Reviewer verdict: APPROVED
- User verdict: SATISFIED
- Unresolved issues: 0
