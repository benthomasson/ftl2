# Plan (Iteration 1)

Task: ## Define gate lifecycle management commands for permanent gates

## Summary

Permanent gates (deployed for performance instead of ephemeral per-connection gates) are already a feature, but there are no defined commands for managing their lifecycle. The AI operator needs a programmatic API to deploy, upgrade, drain, restart, and decommission permanent gates.

## Problem

When gates were ephemeral, lifecycle was trivial — deploy on SSH connect, tear down on disconnect. Permanent gates are infrastructure: they persist across connections, can drift or degrade, and need active management. Currently there's no defined command set for:

- **Deploy**: Install a permanent gate on a host
- **Upgrade**: Roll out a new gate version (drain in-flight work, replace, handle version coexistence)
- **Drain**: Stop accepting new work while completing in-flight tasks
- **Restart**: Restart a gate that's in a bad state without losing work
- **Decommission**: Remove a permanent gate cleanly from a host

## Proposal

Define gate lifecycle commands as SSH subsystem operations, consistent with the existing gate communication model:

```
["GateDeploy", {"permanent": true, ...}]
["GateUpgrade", {"target_version": "...", "strategy": "rolling|replace", ...}]
["GateDrain", {"timeout_seconds": 300}]
["GateRestart", {"force": false}]
["GateDecommission", {"cleanup": true}]
```

Each command should return structured status responses the AI operator can reason over.

### Fleet-level considerations

- Rolling upgrades across multiple hosts
- Canary deploys (upgrade one gate, verify, continue)
- Rollback on upgrade failure
- Aggregate status during fleet-wide operations

## Context

- Permanent gates already exist for performance — this is operational debt on deployed infrastructure
- Gates communicate via SSH subsystem with multiplexed channels — lifecycle commands should use the same transport
- The AI operator persona needs these as programmatic APIs, not interactive CLI commands
- Gate self-reporting (#67) provides the status feedback these commands need for verification
- All communication flows through SSH — no out-of-band management plane needed

## Related

- #67 — GateStatus self-reporting (provides feedback for lifecycle operations)

Closes #68

EFFORT LEVEL: MODERATE
Keep plan focused and concise. Cover key design decisions but avoid over-analysis.

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