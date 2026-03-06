# FTL2 Improvements for RHEL 9 Compatibility

**Date:** 2026-03-05
**Time:** 09:30

## Overview

Four improvements to FTL2 discovered during RHEL 9 integration with rhel-expert as a do-bot. All four fixes committed and pushed to main.

## Issues Fixed

### 1. Gate Python 3.9 compatibility (critical)

The gate package (.pyz) used Python 3.10+ syntax (`dict[str, Any] | None`, `str | None`, `tuple[str, Any] | None`) in all three bundled files. This caused TypeError on RHEL 9 systems running the default Python 3.9.

**Fix:** Added `from __future__ import annotations` to all three gate-bundled files:
- `src/ftl2/message.py`
- `src/ftl2/ftl_gate/__main__.py`
- `src/ftl2/ftl_modules/exceptions.py`

This makes all type annotations lazy strings that are never evaluated at runtime.

### 2. add_host() ignores ansible_python_interpreter

`add_host()` accepted `**kwargs` but did not extract `ansible_python_interpreter` into the HostConfig dataclass field. It was silently absorbed into the generic `vars` dict, so `host.ansible_python_interpreter` always returned the default `"python3"`.

**Fix:** Added `ansible_python_interpreter` as an explicit parameter (default `"python3"`) and passed it through to HostConfig constructor. Added test coverage in `test_add_host_with_all_params`.

### 3. Gate cache not invalidated on interpreter change

The in-memory `gate_cache` in RemoteModuleRunner was keyed by `host.name` only. If a host changed its `ansible_python_interpreter`, the stale cached gate would be reused. The remote file cache (`~/.ftl/ftl_gate_{hash}.pyz`) was already correct since the hash includes the interpreter, but bug #2 meant the interpreter never actually changed from the gate builder perspective.

**Fix:** Added `interpreter` field to the `Gate` dataclass. Both `_get_or_create_gate()` in runners.py and `_get_or_create_gate_for_host()` in context.py now check the cached gate interpreter against the requested one. On mismatch, the old gate is closed and a new one created.

### 4. add_host() missing ansible_password

No explicit parameter for password-based SSH auth. Users had to know to pass it via `**kwargs` and hope the SSH layer would find it in `host.vars`.

**Fix:** Added `ansible_password` as an explicit parameter on `add_host()`. Stored into `vars` dict where the SSH connection code already reads it (`host.vars.get("ansible_password")`).

## Commits

- `4cdfc1c` — Fix gate Python 3.9 compatibility and add_host() ansible_python_interpreter
- `efcebcc` — Invalidate gate cache on interpreter change and add ansible_password to add_host

## Files Changed

- `src/ftl2/message.py` — added future annotations
- `src/ftl2/ftl_gate/__main__.py` — added future annotations
- `src/ftl2/ftl_modules/exceptions.py` — added future annotations
- `src/ftl2/automation/context.py` — add_host() params, gate cache interpreter check
- `src/ftl2/runners.py` — Gate.interpreter field, cache invalidation logic
- `tests/test_automation.py` — test coverage for ansible_python_interpreter

## Related

- Source of feedback: ~/git/rhel-expert/entries/2026/03/05/ftl2-improvements.md
- FTL2 repo: ~/git/faster-than-light2/
