# Implementation: Fix circuit_breaker division by zero (issue #25)

## Changes

1. **`src/ftl2/retry.py:284`** — Added `if total_hosts == 0: return False` guard before the existing `min_hosts` check. This prevents `ZeroDivisionError` on line 289 when `total_hosts=0`.

2. **`tests/test_cli.py:1159`** — Added `test_circuit_breaker_zero_hosts` test with `min_hosts=0` to ensure `check_circuit_breaker(0, 0, config)` returns `False` without raising.

## SELF-REVIEW

1. **What went well:** Straightforward one-line fix plus one test — matched the plan exactly.
2. **What was unclear:** Nothing; the plan was precise.
3. **Next time:** Nothing to improve — this is the ideal level of bug report specificity.
4. **Concerns:** None. The guard is placed before `min_hosts` so it catches `total_hosts=0` even when `min_hosts=0`.
