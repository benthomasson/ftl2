All four issues from Round 1 are properly fixed:

1. **`gate_deploy` now forces subsystem registration** via `register_subsystem=True` parameter on `_get_or_create_gate`
2. **All lifecycle methods use correct become-aware cache keys** — `gate_cache_key(host.name, host.become_config)` in all four methods
3. **`SSHHost.connection` property** replaces private `_conn` access
4. **Null guard in `_resolve_hosts`** covers all five lifecycle methods with a clear error

New tests are well-designed — notably `test_drain_misses_without_become_key` which proves the cache key fix matters by showing the old behavior would fail.

**STATUS: APPROVED** — review written to `workspaces/issue-68-v2/reviewer/REVIEW.md`.

[Committed changes to reviewer branch]