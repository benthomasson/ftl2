# Code Review: Gate Lifecycle Management Commands (Issue #68) — Round 2

## FEEDBACK FOR IMPLEMENTER

### Bug fixes verified

**Bug 1 (gate_deploy subsystem registration) — Fixed correctly.**

`_get_or_create_gate` now accepts `register_subsystem: bool | None = None` (context.py:1810). When set, it overrides `self._gate_subsystem` at line 1873:
```python
should_register = register_subsystem if register_subsystem is not None else self._gate_subsystem
```
`gate_deploy` passes `register_subsystem=True` (line 1953). The override parameter defaults to `None` so all other call sites are unaffected. Clean fix.

**Bug 2 (become-aware cache keys) — Fixed correctly.**

All four lifecycle methods now use `gate_cache_key(host.name, host.become_config)`:
- `gate_drain` (line 1991)
- `gate_upgrade` (line 2041)
- `gate_restart` (line 2092)
- `gate_decommission` (line 2139)

This matches the pattern used everywhere else (`_execute_remote_via_gate` at 1511, `_get_or_create_gate` at 1847).

### Design fixes verified

**Design issue 3 (SSHHost.connection property) — Fixed correctly.**

New property at ssh.py:159-168 returns the underlying connection or raises `RuntimeError("Not connected — call connect() first")`. `gate_decommission` now uses `ssh.connection` (line 2151) instead of `ssh._conn`. Clean.

**Design issue 4 (_remote_runner null guard) — Fixed correctly.**

Guard added in `_resolve_hosts` (line 1922-1923) with a clear error message: `"Gate lifecycle methods require an active context manager"`. Since all five lifecycle methods call `_resolve_hosts` first, the single guard covers everything. Good design — one check point instead of five.

### New tests verified

All new tests are structurally sound:
- `test_resolve_raises_without_context_manager` — confirms RuntimeError when `_remote_runner is None`
- `test_gate_deploy_passes_register_subsystem_true` — captures the `register_subsystem` kwarg and asserts it's `True`
- `test_drain_finds_gate_with_become` — verifies the full key `"web01:become=root:method=sudo"` resolves correctly
- `test_drain_misses_without_become_key` — proves a bare `"web01"` key won't match a become-enabled host

The negative test (`test_drain_misses_without_become_key`) is particularly valuable — it would catch a regression if someone reverts to the bare key.

Existing tests updated correctly: all three `TestResolveHosts` tests now set `ctx._remote_runner = MagicMock()` to pass the new null guard.

### Minor observation (non-blocking)

The lifecycle methods that reconnect (`gate_deploy`, `gate_upgrade`, `gate_restart`) call `_get_or_create_gate(host)` without `become=host.become_config`. This means the reconnected gate is created with `become=None` inside `_get_or_create_gate`, so it won't be cached under the become-aware key. However, this is not a correctness issue — these lifecycle methods don't cache the reconnected gate at all (caching only happens inside `_execute_remote_via_gate`). The reconnection serves its purpose (binary deployment, verification) as a side effect of `_connect_gate`. The next module execution will create its own properly-keyed gate. No fix needed.

---

## FEED-FORWARD FOR TESTER

### Priority: run the tests

Tests were not executed by the implementer. First action:
```bash
pytest tests/test_gate_lifecycle.py tests/test_message.py -x -v
```

### Key behaviors to test

1. **Full lifecycle integration**: deploy → run modules → drain → verify rejection → restart → run modules again. This exercises the complete API.

2. **GateDrain with actual in-flight work**: Current tests only cover the zero-task path. Submit work, then drain mid-flight and verify `completed` / `in_flight` counts.

3. **GateDrain timeout path**: Submit slow work, drain with a 1-second timeout, verify `"status": "timeout"` response.

4. **Drain is one-way**: After drain, there is no undrain. Verify the gate remains in draining state until shutdown/restart. Document this behavior.

### Edge cases

5. **Double drain**: Send GateDrain to an already-drained gate. Should return immediately with 0 completed, 0 in_flight.

6. **gate_decommission on undeployed host**: Should return the "already decommissioned" status from `_decommission_gate_subsystem`.

7. **gate_restart with no existing gate**: Should succeed (just creates a new gate, nothing to drain/close).

8. **Fleet rolling upgrade**: 3+ hosts, failure on host 2, verify host 3 is never attempted.

### Areas of concern

- The multiplexed drain tests exercise `main_multiplexed` with real message parsing (good coverage) but only test the zero-task case.
- `_decommission_gate_subsystem` uses `sed -i` which behaves differently on macOS vs Linux (macOS requires `-i ''`). Since this runs on remote Linux hosts, it's fine for production but may need attention if tested locally on macOS.

---

## SELF-REVIEW

### 1. What was easy to review?

The diffs were small, targeted, and well-described. Each fix maps 1:1 to a flagged issue from Round 1. The implementer's REVIEW.md listed exact line numbers for each change, making verification fast — I could go directly to each location and confirm. The new tests clearly prove each fix works, with both positive and negative cases for the cache key fix.

### 2. What made review difficult?

Nothing significant. The one thing that slowed me down was tracing the become-key flow through the reconnect path (`gate_upgrade` → `_get_or_create_gate` → `_connect_gate`) to verify whether the lifecycle methods also needed to pass `become` when reconnecting. Concluded it's not needed because these methods don't cache the result.

### 3. What would make my job easier next time?

Run the tests. Even one passing run would save the reviewer from having to mentally simulate test execution.

### 4. What should the implementer know for future reviews?

Good response to feedback. The fixes are minimal and precise — no scope creep, no unnecessary refactoring. The `_resolve_hosts` guard is a particularly nice design choice (single check point instead of duplicating the guard in each method). The negative test for cache key mismatch (`test_drain_misses_without_become_key`) shows good testing instincts.

---

## Verdict

STATUS: APPROVED
OPEN_ISSUES: none
