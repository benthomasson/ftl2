# Plan: Fix circuit_breaker division by zero (issue #25)

## PLAN

**What and Why:** `check_circuit_breaker` in `src/ftl2/retry.py:287` computes `failed_hosts / total_hosts` without guarding against `total_hosts=0`. While the default `min_hosts=5` usually prevents reaching that line, a caller could pass `total_hosts=0` directly, or `min_hosts` could be configured to `0`, causing a `ZeroDivisionError`.

**Implementation Steps:**
1. In `check_circuit_breaker` (`src/ftl2/retry.py:266`), add an early return `False` when `total_hosts == 0` — before line 287. Place it before or alongside the existing `min_hosts` guard. If there are zero hosts, there's nothing to circuit-break.
2. Add a test case in `tests/test_cli.py` alongside the existing `test_circuit_breaker_check` test: call `check_circuit_breaker(0, 0, config)` with `min_hosts=0` and assert it returns `False` without raising.

**Success Criteria:** `check_circuit_breaker(0, 0, config)` returns `False` for any config. No `ZeroDivisionError`. Existing tests still pass.

## SELF-REVIEW

1. **What went well:** Straightforward bug with a clear fix — easy to locate and scope.
2. **Missing info:** None needed; the code is self-explanatory.
3. **Next time:** Nothing — this is about as clean as a bug report gets.
4. **Confidence:** HIGH — single-line guard fix with obvious correctness.
