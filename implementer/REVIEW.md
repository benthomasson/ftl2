# Implementation Review — Round 2 (Post-Reviewer Fixes)

## Changes in this round

Addressed 2 bugs and 2 design issues flagged by the reviewer.

### Files modified

1. **`src/ftl2/automation/context.py`** — 4 fixes:
   - **Bug 1 fix:** Added `register_subsystem: bool | None = None` parameter to `_get_or_create_gate` (line 1810). When set, it overrides `self._gate_subsystem` in the `_connect_gate` call (line 1873). `gate_deploy` now passes `register_subsystem=True` (line 1953), ensuring subsystem registration happens regardless of context-level settings.
   - **Bug 2 fix:** Changed all 4 lifecycle methods from `gate_cache_key(host.name)` to `gate_cache_key(host.name, host.become_config)` — in `gate_drain` (line 1991), `gate_upgrade` (line 2041), `gate_restart` (line 2092), and `gate_decommission` (line 2139). This matches how gates are stored in the cache at `_get_or_create_gate` (line 1847).
   - **Design issue 3 fix:** `gate_decommission` now uses `ssh.connection` (line 2151) instead of `ssh._conn`, using the new public property.
   - **Design issue 4 fix:** `_resolve_hosts` now guards against `self._remote_runner is None` (line 1922), raising `RuntimeError` with a clear message. Since all 5 lifecycle methods call `_resolve_hosts` first, this single check covers all of them.

2. **`src/ftl2/ssh.py`** — 1 addition:
   - Added `connection` property (lines 159-168) to `SSHHost` that returns `self._conn` or raises `RuntimeError("Not connected")`. This replaces the private `_conn` access in `gate_decommission`.

3. **`tests/test_gate_lifecycle.py`** — 5 changes:
   - Existing `TestResolveHosts` tests (3 tests) now set `ctx._remote_runner = MagicMock()` to pass the new null guard.
   - Added `test_resolve_raises_without_context_manager` — verifies `RuntimeError` when `_remote_runner is None`.
   - Added `TestGateDeploySubsystem.test_gate_deploy_passes_register_subsystem_true` — verifies `gate_deploy` passes `register_subsystem=True` through to `_get_or_create_gate`.
   - Added `TestBecomeConfigCacheKey.test_drain_finds_gate_with_become` — creates a host with `ansible_become=True`, caches a gate under the become-aware key (`web01:become=root:method=sudo`), and verifies `gate_drain` finds it.
   - Added `TestBecomeConfigCacheKey.test_drain_misses_without_become_key` — proves that a become host does NOT match a gate cached under the bare key `"web01"`, returning "No active gate connection".

## Self-review

### 1. Source files modified (full list across both rounds)
- `src/ftl2/message.py`
- `src/ftl2/ftl_gate/__main__.py`
- `src/ftl2/runners.py`
- `src/ftl2/automation/context.py`
- `src/ftl2/ssh.py`
- `tests/test_gate_lifecycle.py` (new)
- `tests/test_message.py`

### 2. What went well
- Both bugs had clean, minimal fixes. The `register_subsystem` override is a single parameter addition that threads through the existing call chain without restructuring anything.
- The become-config fix was a simple find-and-replace of `gate_cache_key(host.name)` → `gate_cache_key(host.name, host.become_config)` in 4 locations, matching the pattern already used by `_get_or_create_gate`.
- Putting the `_remote_runner` null check in `_resolve_hosts` covers all 5 lifecycle methods with a single guard instead of repeating it 5 times.

### 3. What was unclear in the plan
- Nothing was unclear in this round — the reviewer's feedback was specific and actionable.

### 4. Concerns for the reviewer
- **Tests still need to be run.** Bash execution was not available in this session. Priority: `pytest tests/test_gate_lifecycle.py tests/test_message.py -x -v`.
- **`SSHHost.connection` property is new public API.** It's a one-liner so risk is low, but any existing code accessing `ssh._conn` directly should be migrated to `ssh.connection`. I only changed the one reference in `gate_decommission`; there may be other `_conn` accesses elsewhere that could benefit from this property.
- **`_get_or_create_gate` signature changed** — added an optional `register_subsystem` parameter. All existing callers omit it, getting `None`, which falls through to `self._gate_subsystem` (the original behavior). No existing behavior changes.
