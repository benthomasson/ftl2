## Summary of Round 2 fixes

Addressed both bugs and both design issues from the reviewer:

### Bug fixes
1. **`gate_deploy` now forces subsystem registration** — Added `register_subsystem: bool | None` parameter to `_get_or_create_gate`. `gate_deploy` passes `register_subsystem=True`, so subsystem registration happens regardless of the context-level `gate_subsystem` setting.

2. **All lifecycle methods now use the correct cache key** — Changed `gate_cache_key(host.name)` to `gate_cache_key(host.name, host.become_config)` in `gate_drain`, `gate_upgrade`, `gate_restart`, and `gate_decommission`. Gates for become-enabled hosts are now found correctly.

### Design fixes
3. **Added `SSHHost.connection` property** — `gate_decommission` now uses `ssh.connection` instead of accessing the private `ssh._conn` attribute.

4. **Added `_remote_runner` null guard** — `_resolve_hosts` now raises `RuntimeError` if called outside the context manager, covering all 5 lifecycle methods.

### New tests
- `test_resolve_raises_without_context_manager` — RuntimeError when no context manager
- `test_gate_deploy_passes_register_subsystem_true` — verifies the subsystem registration fix
- `test_drain_finds_gate_with_become` — verifies become-aware cache key lookup
- `test_drain_misses_without_become_key` — proves bare key doesn't match become hosts

[Committed changes to implementer branch]