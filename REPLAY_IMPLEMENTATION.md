# Audit Replay Implementation Summary

## Overview

Successfully implemented the audit replay feature for FTL2, enabling crash recovery by replaying successful actions from a previous run and resuming execution from the point of failure.

## Implementation Date

2026-02-09

## Changes Made

### 1. Core Data Structures

**File: `src/ftl2/ftl_modules/executor.py`**
- Added `replayed: bool = False` field to `ExecuteResult` dataclass
- Updated docstring to document the new field

### 2. AutomationContext Enhancements

**File: `src/ftl2/automation/context.py`**
- Added `replay` parameter to `__init__()` with comprehensive documentation
- Added `_replay_actions` and `_replay_index` instance variables
- Implemented replay log loading in constructor (parses JSON from file)
- Implemented `_try_replay()` helper method with positional matching logic:
  - Matches on module name + host
  - Only replays successful actions
  - Re-executes failures
  - Disengages on first mismatch
- Added replay intercept in `execute()` (local execution path)
- Added replay intercept in `_execute_on_host()` (remote execution path)
- Added `output` field to audit recording in `_write_recording()`
- Added `replayed` marker to audit output for replayed actions

### 3. Automation Helper Function

**File: `src/ftl2/automation/__init__.py`**
- Added `replay` parameter to `automation()` helper function
- Added documentation for the replay parameter
- Wired replay parameter through to `AutomationContext`

## How It Works

### Positional Matching Algorithm

1. **Initialization**: Load previous audit log JSON into memory
2. **Execution Loop**: For each module call:
   - Check if next entry in replay log matches (module + host)
   - If match AND success: return cached output, skip execution
   - If mismatch OR failure: disengage replay, execute normally
   - Increment replay index on each match
3. **Output**: Write new audit log with both replayed and newly-executed actions

### Key Design Decisions

- **Positional matching** (not content-based): Simpler and correct for crash recovery
- **Fail-safe**: Mismatches disengage replay rather than error
- **Zero-duration replays**: Replayed actions show 0.0s duration
- **Output caching**: Return full output dict so downstream code works
- **Visual feedback**: Print `↩ replayed (skipped)` for each replay

## Usage Example

```python
from ftl2.automation import automation

# First run - crashes at step 3
async with automation(
    record="audit.json",
    fail_fast=True,
) as ftl:
    await ftl.file(path="/tmp/test1", state="touch")  # ✓ OK
    await ftl.file(path="/tmp/test2", state="touch")  # ✓ OK
    await ftl.copy(src="missing.jar", dest="/opt/")   # ✗ CRASH

# Second run - replay successful steps
async with automation(
    record="audit.json",  # Write new audit
    replay="audit.json",  # Read previous audit
    fail_fast=True,
) as ftl:
    await ftl.file(path="/tmp/test1", state="touch")  # ↩ replayed
    await ftl.file(path="/tmp/test2", state="touch")  # ↩ replayed
    await ftl.copy(src="server.jar", dest="/opt/")    # ▶ executes
```

## Test Coverage

### Unit Tests (`test_replay.py`)

1. **Basic Replay**: Verify successful actions are skipped and cached output returned
2. **Failed Actions**: Verify failures trigger re-execution instead of replay
3. **Mismatch Handling**: Verify replay disengages when script changes
4. **Output Caching**: Verify replayed output matches original

All tests passed ✅

### Security Tests (`test_replay_secrets.py`)

5. **Secret Bindings**: Verify replay works safely with secret injection
6. **Positional Matching**: Verify replay ignores parameter changes

All tests passed ✅

### Integration Demo (`demo_crash_recovery.py`)

Interactive demonstration of:
- Multi-step deployment that crashes partway through
- Retry with replay enabled
- Visual feedback showing replayed vs executed steps
- Final audit log showing mixed replayed/executed actions

## Security: Replay and Secret Bindings

Replay is **completely safe** with the secret bindings system. The implementation maintains all security guarantees.

### Execution Order

The key to security is the order of operations in `execute()`:

```python
async def execute(self, module_name: str, params: dict[str, Any]) -> dict[str, Any]:
    start_time = time.time()
    original_params = params  # ← NO SECRETS YET (pre-injection)

    # 1. CHECK REPLAY FIRST (before secret injection)
    replay_result = self._try_replay(module_name, "localhost", original_params)
    if replay_result is not None:
        return replay_result.output  # Skip everything below

    # 2. INJECT SECRETS (only if NOT replayed)
    secret_injections = self._get_secret_bindings_for_module(module_name)
    if secret_injections:
        params = {**secret_injections, **params}

    # 3. EXECUTE MODULE (only if NOT replayed)
    result = await execute(module_name, params, ...)

    # 4. RECORD TO AUDIT (using original_params, not injected ones)
    result.params = self._redact_params(module_name, original_params)
```

### Security Properties

1. **Secrets never reach the audit log**
   - Audit records `original_params` (captured before injection)
   - `_redact_params()` further redacts sensitive HTTP headers/tokens
   - Replayed actions use the same pre-injection params

2. **Replay happens before secret injection**
   - Replay check uses `original_params` (no secrets)
   - If replay hits, execution returns immediately — secrets never injected
   - Secret injection is skipped entirely for replayed actions

3. **Replay works without secrets configured**
   - Returns cached output from audit log
   - No need to re-inject secrets since execution is skipped
   - You can replay actions even if secrets are no longer in environment

### Test Verification

✅ Replay with secret bindings configured — secrets not injected on replay
✅ Replay without secret bindings — works using cached output
✅ Positional matching ignores param changes — maintains security

**Key insight**: Replay intercepts execution _before_ any secret processing happens, making it orthogonal to the secret binding system.

## Performance Characteristics

- **Replay overhead**: Near-zero (just dict lookup and result construction)
- **Memory usage**: Entire audit log loaded into memory (acceptable for typical use)
- **Disk I/O**: Single read at initialization, single write at exit

## Files Modified

1. `src/ftl2/ftl_modules/executor.py` (2 changes)
2. `src/ftl2/automation/context.py` (6 changes)
3. `src/ftl2/automation/__init__.py` (2 changes)

## Test Files Created

1. `test_replay.py` — Comprehensive unit tests (core functionality)
2. `test_replay_secrets.py` — Secret binding security tests
3. `demo_crash_recovery.py` — Interactive crash recovery demo

## Future Enhancements

Possible improvements identified but not implemented:

1. **Auto-replay**: `replay="auto"` to automatically use `record` path if file exists
2. **Partial replay**: Resume from specific step number
3. **Replay statistics**: Print summary of replayed vs executed actions
4. **Replay validation**: Warn if audit is stale (timestamp check)
5. **Content-based matching**: Use params hash for duplicate operations

## Related Documentation

- Original design: `/entries/2026/02/09/ftl2-audit-replay.md`
- Audit recording: Implemented in FTL2 v0.x
- State file system: Complementary crash recovery for dynamic hosts

## Notes

- Replay and record can point to the same file (loaded at init, written at exit)
- Secret parameters are excluded from audit (captured before injection)
- HTTP headers are redacted in audit to prevent credential leakage
- Replay works for both local and remote (SSH) execution
- `asyncio.gather` order is preserved, so multi-host operations are deterministic
