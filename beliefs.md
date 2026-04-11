# Beliefs Registry

## Repos


### plan-1-1 [IN] AXIOM
**message.py** — Add `GateDrain`, `GateDrainResult`, `Goodbye` to MESSAGE_TYPES
- Date: 2026-04-11

### plan-1-2 [IN] AXIOM
**ftl_gate/\_\_main\_\_.py (serial)** — Trivial drain handler (no concurrency, respond immediately)
- Date: 2026-04-11

### plan-1-3 [IN] AXIOM
**ftl_gate/\_\_main\_\_.py (multiplexed)** — Handle GateDrain synchronously in main loop (like Shutdown), add `draining` flag to reject new work
- Date: 2026-04-11

### plan-1-4 [IN] AXIOM
**runners.py** — `_drain_gate()` client method to send/await GateDrain
- Date: 2026-04-11

### plan-1-5 [IN] AXIOM
**runners.py** — `_decommission_gate_subsystem()` — inverse of register (remove sshd_config line, reload, delete binary)
- Date: 2026-04-11

### test-1-1 [IN] OBSERVATION
Tests TESTS_PASSED
- Date: 2026-04-11
