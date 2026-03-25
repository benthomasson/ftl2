# Plan: Fix circuit_breaker division by zero (Issue #25)

## PLAN

**Bug**: `check_circuit_breaker` in `src/ftl2/retry.py:287` divides `failed_hosts / total_hosts` without guarding against `total_hosts=0`. If `config.min_hosts` is 0, the `min_hosts` check on line 284 passes through, and the division on line 287 raises `ZeroDivisionError`.

**Fix**: Add an early return `if total_hosts == 0: return False` before the division (line 287). Zero hosts means nothing failed, so the circuit breaker should not trigger. Place it before the `min_hosts` check or immediately after — either works.

**Success criteria**: `check_circuit_breaker(total_hosts=0, failed_hosts=0, config)` returns `False` without raising. Add a test case for `total_hosts=0`.

## SELF-REVIEW

1. Straightforward bug with a clear fix — no ambiguity.
2. No missing information.
3. N/A
4. Confidence: **HIGH** — single-line fix with obvious correct behavior.
