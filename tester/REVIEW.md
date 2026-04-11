# Tester Review — Gate Lifecycle Management (Issue #68)

## TEST CASES

### Test file: `tests/test_gate_lifecycle_extended.py` (23 tests)

Added alongside the existing `tests/test_gate_lifecycle.py` (22 tests). Combined: **45 tests, all passing**.

| # | Test | What it validates |
|---|------|-------------------|
| 1 | `test_deploy_group_deploys_all_hosts` | Deploy to 3-host group, all succeed |
| 2 | `test_deploy_partial_failure` | First host SSH fails, second still deployed |
| 3 | `test_drain_exception_returns_error` | TimeoutError during drain captured as error status |
| 4 | `test_drain_passes_custom_timeout` | `timeout_seconds=42` forwarded to `_drain_gate` |
| 5 | `test_drain_default_timeout` | Default `timeout_seconds=300` used |
| 6 | `test_parallel_upgrade_all_hosts` | `strategy="parallel"` upgrades all hosts |
| 7 | `test_upgrade_no_existing_gate_skips_drain` | No cached gate -> skip drain/close, create new |
| 8 | `test_rolling_upgrade_all_succeed` | Rolling through 3 hosts, all ok |
| 9 | `test_upgrade_become_aware_cache_key` | Upgrade finds gate under become key |
| 10 | `test_restart_no_cached_gate_just_reconnects` | No cached gate -> just reconnect |
| 11 | `test_restart_become_aware_cache_key` | Restart finds gate under become key |
| 12 | `test_decommission_full_lifecycle` | Drain + close + SSH decommission |
| 13 | `test_decommission_no_cached_gate` | No cached gate -> skip drain, just SSH decommission |
| 14 | `test_decommission_cleanup_false` | `cleanup=False` forwarded correctly |
| 15 | `test_decommission_become_aware_cache_key` | Decommission finds gate under become key |
| 16 | `test_decommission_ssh_exception_returns_error` | SSH failure captured as error |
| 17 | `test_connection_raises_when_not_connected` | SSHHost.connection guard |
| 18 | `test_connection_returns_conn_when_connected` | SSHHost.connection returns conn |
| 19 | `test_bare_key_without_become` | `gate_cache_key("web01")` -> `"web01"` |
| 20 | `test_bare_key_with_become_disabled` | `become=False` -> `"web01"` |
| 21 | `test_become_key_with_sudo` | `become=True, sudo` -> `"web01:become=root:method=sudo"` |
| 22 | `test_become_key_with_doas` | `become=True, doas` -> `"web01:become=admin:method=doas"` |
| 23 | `test_host_become_config_property` | `HostConfig.become_config` generates correct key |

### Reviewer-noted edge cases covered

- **Bug 1 (register_subsystem):** Covered by existing `test_gate_deploy_passes_register_subsystem_true` + my `test_deploy_group_deploys_all_hosts`
- **Bug 2 (become-aware cache keys):** Covered by `test_upgrade_become_aware_cache_key`, `test_restart_become_aware_cache_key`, `test_decommission_become_aware_cache_key` (one per lifecycle method)
- **Design issue 3 (SSHHost.connection):** Covered by `test_connection_raises_when_not_connected` and `test_connection_returns_conn_when_connected`
- **Design issue 4 (null guard):** Covered by existing `test_resolve_raises_without_context_manager`

### How to run

```bash
cd /Users/ben/git/faster-than-light2

# Run all gate lifecycle tests
.venv/bin/python -m pytest tests/test_gate_lifecycle.py tests/test_gate_lifecycle_extended.py -v

# Run just the extended tests
.venv/bin/python -m pytest tests/test_gate_lifecycle_extended.py -v
```

---

## USAGE INSTRUCTIONS FOR USER

See `USAGE.md` in this directory for the full usage guide. Key points:

### Quick start

```python
async with automation(inventory="inventory.yml") as ftl:
    await ftl.gate_deploy("webservers")          # Install gates
    await ftl.gate_drain("web01", timeout_seconds=60)  # Drain one host
    await ftl.gate_upgrade("webservers", strategy="rolling")  # Rolling upgrade
    await ftl.gate_restart("web01", force=True)   # Force restart
    await ftl.gate_decommission("web03", cleanup=True)  # Remove gate
```

### All methods return `list[dict]` with per-host results

Check `r["status"]` — values are `"ok"`, `"drained"`, or `"error"`.

---

## SELF-REVIEW

### 1. What was easy to test? What was hard?

**Easy:** The lifecycle methods on `AutomationContext` are well-designed for testing. They accept simple string arguments, return plain dicts, and delegate to mockable runner methods. The helper `_make_ctx_with_hosts` made test setup concise.

**Harder:** The gate-side protocol tests in the original file (e.g., `TestMultiplexedDrain`) require more setup — building `StreamReader`s with length-prefixed messages. I didn't need to add more of those since the original file already covers the protocol layer well.

### 2. What information was missing that would have helped?

- A clear list of which code paths the implementer's tests already cover vs. which they intentionally left for the tester. I had to read all 22 existing tests to figure out the gaps.
- The relationship between `gate_subsystem=True` on `automation()` and the `register_subsystem` parameter — I figured it out by reading `_get_or_create_gate` but it wasn't in the task description.

### 3. What would make your job easier next time?

- A test coverage report scoped to just the lifecycle methods (lines 1904-2160 of `context.py`) would immediately show uncovered branches.
- The implementer could leave TODO comments marking intentionally untested paths (e.g., "tester: cover parallel upgrade path").

### 4. Any gaps in the implementation that testing revealed?

No bugs found. The implementation handles all edge cases correctly:
- Partial failures don't stop deploy/decommission (only rolling upgrade stops on failure)
- Missing gates are handled gracefully (skip drain/close)
- Exception handling wraps all lifecycle methods consistently
- Cache keys are correct for both become and non-become hosts

One observation: `gate_upgrade` with `strategy="parallel"` does not stop on failure (by design), but also doesn't communicate which hosts were skipped. This is fine for parallel semantics but worth noting for fleet operators.

---

## Verdict

STATUS: TESTS_PASSED
OPEN_ISSUES: none
