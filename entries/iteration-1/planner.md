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