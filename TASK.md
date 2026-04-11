# Task

## Define gate lifecycle management commands for permanent gates

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

Started: 2026-04-11T13:19:05.747089