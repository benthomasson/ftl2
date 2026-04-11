## Summary

### Files modified (6 total)

| File | Change |
|------|--------|
| `src/ftl2/message.py` | Added `Goodbye`, `GateDrain`, `GateDrainResult` to MESSAGE_TYPES |
| `src/ftl2/ftl_gate/__main__.py` | Serial-mode drain handler + multiplexed drain handler with `draining` flag and work rejection |
| `src/ftl2/runners.py` | `_drain_gate()` (~30 lines) and `_decommission_gate_subsystem()` (~50 lines) |
| `src/ftl2/automation/context.py` | `_resolve_hosts()` helper + 5 public methods: `gate_deploy`, `gate_drain`, `gate_upgrade`, `gate_restart`, `gate_decommission` (~250 lines) |
| `tests/test_gate_lifecycle.py` | **New file** — 14 tests across protocol, gate-side, client-side, and context layers |
| `tests/test_message.py` | Updated MESSAGE_TYPES count assertion (22 -> 25) |

### What went well
- Plan's core insight was correct: only GateDrain needs a gate-side protocol message; everything else is client-side orchestration
- Existing patterns (Shutdown handler, `_register_gate_subsystem`, `gate_cache_key`) made implementation clean
- Tests for multiplexed drain run the real `main_multiplexed` function, not just mocks

### What was unclear
- Whether `gate_deploy` should force subsystem registration even without `gate_subsystem=True` on the context
- How to cleanly get the raw asyncssh connection from `SSHHost` (used private `_conn`)

### Concerns for reviewer
1. **`gate_deploy` relies on context config** — doesn't independently force subsystem registration
2. **`ssh._conn` is a private attribute** — used in `gate_decommission`, may warrant a public accessor
3. **No become support** — lifecycle methods use simple cache keys without `BecomeConfig`
4. **Tests not executed** — bash was not permitted; needs `pytest tests/test_gate_lifecycle.py tests/test_message.py -x`

[Committed changes to implementer branch]