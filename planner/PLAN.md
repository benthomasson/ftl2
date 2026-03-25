# Plan: Fix circuit_breaker division by zero (Issue #25)

## PLAN

**Bug**: In `src/ftl2/retry.py:287`, `check_circuit_breaker` computes `failed_hosts / total_hosts` without guarding against `total_hosts=0`. If called with zero hosts, this raises `ZeroDivisionError`.

**Fix**: Add `if total_hosts == 0: return False` early in the function, before line 287. When there are no hosts, the circuit breaker should not trip. This guard should go after the `config.enabled` check but before the `min_hosts` check (or alongside it — logically zero hosts is below any minimum).

Note: the existing `min_hosts` check on line 284 would catch this if `min_hosts >= 1`, but `min_hosts` defaults could be 0, so an explicit zero guard is the correct fix.

**Success criteria**: `check_circuit_breaker(0, 0, config)` returns `False` without raising. Add a test case for `total_hosts=0`.

## SELF-REVIEW

1. Straightforward bug with an obvious fix — planning was quick.
2. No missing info needed.
3. N/A
4. Confidence: **HIGH** — single-line guard for a clear division-by-zero bug.
